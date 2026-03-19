"""Message-listener cog that removes predefined profanity and logs moderation actions."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Final

import discord
from discord.ext import commands

from common_paths import json_file
from habbo_verification_core import ServerConfigStore


class ProfanityCog(commands.Cog):
    """Delete messages that contain configured profanity or obvious written variations."""

    # Store blocked words in JSON so moderators can update the list without editing code.
    DEFAULT_WORDS_PATH: Final[Path] = json_file("profanity_words.json")

    # Fall back to a safe built-in list if the JSON file is missing/corrupt.
    DEFAULT_BLOCKED_WORDS: Final[tuple[str, ...]] = (
        "asshole",
        "bitch",
        "bullshit",
        "damn",
        "fuck",
        "motherfucker",
        "shit",
        "whore",
    )

    # Convert common leetspeak substitutions into their alphabetical equivalents.
    LEETSPEAK_TRANSLATION: Final[dict[int, str]] = str.maketrans({
        "0": "o",
        "1": "i",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
        "@": "a",
        "$": "s",
        "!": "i",
    })

    def __init__(self, bot: commands.Bot, *, blocked_words_path: Path | None = None) -> None:
        # Store collaborators on the instance so tests can replace them easily.
        self.bot = bot
        self.server_config_store = ServerConfigStore()
        self.blocked_words_path = blocked_words_path or self.DEFAULT_WORDS_PATH
        self.blocked_words = self._load_blocked_words()

    def _load_blocked_words(self) -> set[str]:
        """Load blocked words from JSON and normalize them for consistent matching."""

        try:
            payload = json.loads(self.blocked_words_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            payload = list(self.DEFAULT_BLOCKED_WORDS)

        if not isinstance(payload, list):
            payload = list(self.DEFAULT_BLOCKED_WORDS)

        normalized_words: set[str] = set()
        for raw_word in payload:
            if not isinstance(raw_word, str):
                continue

            normalized_word = self._normalize_for_detection(raw_word).replace(" ", "")
            if normalized_word:
                normalized_words.add(normalized_word)

        return normalized_words or set(self.DEFAULT_BLOCKED_WORDS)

    @classmethod
    def _normalize_for_detection(cls, value: str) -> str:
        """Normalize message text so punctuation, spacing, and repeated letters are less evasive.

        The goal is not perfect linguistic analysis; it is reliable matching for common
        profanity obfuscations such as ``f.u.c.k``, ``fuuuck``, or ``sh1t``.
        """

        # Strip accents and normalize Unicode into a predictable ASCII-ish shape.
        normalized = unicodedata.normalize("NFKD", value)
        normalized = normalized.encode("ascii", "ignore").decode("ascii")
        normalized = normalized.lower().translate(cls.LEETSPEAK_TRANSLATION)

        # Replace non-alphanumeric separators with spaces to preserve word boundaries.
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized)

        collapsed_tokens: list[str] = []
        for token in normalized.split():
            # Reduce repeated letters so stretched spellings like "fuuuuuck" still match.
            collapsed_tokens.append(re.sub(r"(.)\1+", r"\1", token))
        return " ".join(collapsed_tokens)

    def contains_profanity(self, content: str) -> bool:
        """Return True when message content contains a blocked word or a simple variation."""

        normalized = self._normalize_for_detection(content)
        if not normalized:
            return False

        tokens = normalized.split()
        compact_text = "".join(tokens)

        for blocked_word in self.blocked_words:
            # Match whole normalized tokens first to avoid overly broad false positives.
            if blocked_word in tokens:
                return True

            # Also inspect the separator-free content so forms like "f.u.c.k" are caught.
            if blocked_word in compact_text:
                return True
        return False

    async def _send_user_notice(self, message: discord.Message) -> bool:
        """Try to DM the affected user and return whether the notice was delivered."""

        embed = discord.Embed(
            title="Profanity Filter",
            description=(
                f"{message.author.mention}, your message has been deleted because it "
                "contained profanity or a variation of a blocked word. Please keep chat respectful."
            ),
            color=discord.Color.red(),
        )
        embed.add_field(
            name="Original Channel",
            value=message.channel.mention if hasattr(message.channel, "mention") else "Unknown",
            inline=True,
        )
        embed.add_field(
            name="Server",
            value=message.guild.name if message.guild else "Direct Messages",
            inline=True,
        )
        try:
            await message.author.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException, AttributeError):
            # DMs can fail because of privacy settings or because the mock/test object
            # does not fully emulate a Discord user. Report that in the log channel instead.
            return False
        return True

    async def _send_log_notice(self, message: discord.Message, *, dm_sent: bool) -> None:
        """Send an audit-style embed to the configured profanity log channel, if available."""

        if message.guild is None:
            return

        log_channel_id = self.server_config_store.get_profanity_log_channel_id()
        if log_channel_id is None:
            return

        log_channel = message.guild.get_channel(log_channel_id)
        if log_channel is None:
            return

        embed = discord.Embed(
            title="Profanity Filter Triggered",
            description="A message was deleted by the profanity filter.",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Member", value=f"{message.author.mention} (`{message.author.id}`)", inline=False)
        embed.add_field(name="Server", value=f"{message.guild.name} (`{message.guild.id}`)", inline=False)
        embed.add_field(
            name="Channel",
            value=message.channel.mention if hasattr(message.channel, "mention") else f"`{getattr(message.channel, 'id', 'unknown')}`",
            inline=False,
        )
        embed.add_field(name="Deleted Content", value=message.content[:1024] or "*(no text content)*", inline=False)
        embed.add_field(
            name="User Notice",
            value=(
                "Direct message delivered successfully."
                if dm_sent
                else "I could not DM the user, likely because their privacy settings blocked the bot."
            ),
            inline=False,
        )
        await log_channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Delete matching profanity messages and notify both the user and moderators."""

        # Ignore bots/webhooks so the filter only targets member-authored chat.
        if message.author.bot or message.webhook_id is not None:
            return

        # The requested server-configured logging behavior only applies inside guilds.
        if message.guild is None or not message.content:
            return

        if not self.contains_profanity(message.content):
            return

        try:
            await message.delete()
        except (discord.Forbidden, discord.HTTPException):
            # If the bot cannot delete the message, avoid sending misleading notices.
            return

        dm_sent = await self._send_user_notice(message)
        await self._send_log_notice(message, dm_sent=dm_sent)


async def setup(bot: commands.Bot) -> None:
    """Discord extension entrypoint for loading this profanity filter cog."""

    await bot.add_cog(ProfanityCog(bot))
