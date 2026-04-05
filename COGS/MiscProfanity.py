"""Message-listener cog that flags predefined profanity and lets moderators choose action."""

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
    """Flag messages that contain configured profanity or obvious written variations."""

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

    def _match_blocked_word(self, content: str) -> str | None:
        """Return the blocked word that matched the message, if any."""

        normalized = self._normalize_for_detection(content)
        if not normalized:
            return None

        tokens = normalized.split()
        compact_text = "".join(tokens)

        for blocked_word in sorted(self.blocked_words):
            # Match whole normalized tokens first to avoid overly broad false positives.
            if blocked_word in tokens:
                return blocked_word

            # Also inspect the separator-free content so forms like "f.u.c.k" are caught.
            if blocked_word in compact_text:
                return blocked_word
        return None

    def contains_profanity(self, content: str) -> bool:
        """Return True when message content contains a blocked word or a simple variation."""

        return self._match_blocked_word(content) is not None

    @staticmethod
    def _truncate_field_value(value: str, *, limit: int = 1024) -> str:
        """Trim embed field values so full message context fits within Discord limits."""

        if len(value) <= limit:
            return value
        return value[: limit - 3] + "..."

    async def _send_user_notice(self, message: discord.Message, *, blocked_word: str) -> bool:
        """Try to DM the affected user and return whether the notice was delivered."""

        embed = discord.Embed(
            title="Profanity Filter",
            description=(
                f"{message.author.mention}, your message has been deleted because it "
                "contained profanity or a variation of a blocked word. Please keep chat respectful."
            ),
            color=discord.Color.red(),
        )
        embed.add_field(name="Blocked Word", value=f"`{blocked_word}`", inline=True)
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
        embed.add_field(
            name="Deleted Message",
            value=self._truncate_field_value(message.content or "*(no text content)*"),
            inline=False,
        )
        try:
            await message.author.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException, AttributeError):
            # DMs can fail because of privacy settings or because the mock/test object
            # does not fully emulate a Discord user. Report that in the log channel instead.
            return False
        return True

    def _build_flagged_embed(self, message: discord.Message, *, blocked_word: str) -> discord.Embed:
        """Build the moderation prompt shown before any deletion action is taken."""

        embed = discord.Embed(
            title="Profanity Filter Flagged Message",
            description=(
                "A message matched the profanity filter. Choose **Ignore** to leave it as-is, "
                "or **Proceed** to delete it and notify the author."
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(name="Member", value=f"{message.author.mention} (`{message.author.id}`)", inline=False)
        embed.add_field(name="Server", value=f"{message.guild.name} (`{message.guild.id}`)", inline=False)
        embed.add_field(
            name="Channel",
            value=message.channel.mention if hasattr(message.channel, "mention") else f"`{getattr(message.channel, 'id', 'unknown')}`",
            inline=False,
        )
        embed.add_field(name="Blocked Word", value=f"`{blocked_word}`", inline=False)
        embed.add_field(
            name="Message Content",
            value=self._truncate_field_value(message.content or "*(no text content)*"),
            inline=False,
        )
        return embed

    async def _send_action_result(
        self,
        *,
        target_channel: discord.abc.Messageable,
        moderator: discord.abc.User | discord.Member | None,
        action: str,
        message: discord.Message,
        blocked_word: str,
        dm_sent: bool | None = None,
    ) -> None:
        """Post a follow-up audit entry after moderators choose ignore/proceed."""

        embed = discord.Embed(
            title="Profanity Filter Action",
            description=f"Moderator action selected: **{action}**.",
            color=discord.Color.orange() if action == "Proceed" else discord.Color.green(),
        )
        embed.add_field(name="Member", value=f"{message.author.mention} (`{message.author.id}`)", inline=False)
        embed.add_field(name="Channel", value=getattr(message.channel, "mention", "Unknown"), inline=False)
        embed.add_field(name="Blocked Word", value=f"`{blocked_word}`", inline=False)
        embed.add_field(
            name="Message Content",
            value=self._truncate_field_value(message.content or "*(no text content)*"),
            inline=False,
        )
        embed.add_field(
            name="Moderator",
            value=f"{moderator.mention} (`{moderator.id}`)" if moderator else "Unknown",
            inline=False,
        )
        if dm_sent is not None:
            embed.add_field(
                name="User Notice",
                value=(
                    "Direct message delivered successfully."
                    if dm_sent
                    else "I could not DM the user, likely because their privacy settings blocked the bot."
                ),
                inline=False,
            )
        await target_channel.send(embed=embed)

    async def _resolve_log_channel(self, message: discord.Message) -> discord.abc.Messageable | None:
        """Return the configured profanity log channel for this guild, when available."""

        if message.guild is None:
            return None
        log_channel_id = self.server_config_store.get_profanity_log_channel_id()
        if log_channel_id is None:
            return None
        return message.guild.get_channel(log_channel_id)

    async def _handle_ignore_action(
        self,
        *,
        interaction: discord.Interaction,
        flagged_message: discord.Message,
        blocked_word: str,
        log_channel: discord.abc.Messageable,
    ) -> None:
        """Leave the original chat message untouched and record the moderator decision."""

        await interaction.response.edit_message(view=None)
        await self._send_action_result(
            target_channel=log_channel,
            moderator=interaction.user,
            action="Ignore",
            message=flagged_message,
            blocked_word=blocked_word,
        )

    async def _handle_proceed_action(
        self,
        *,
        interaction: discord.Interaction,
        flagged_message: discord.Message,
        blocked_word: str,
        log_channel: discord.abc.Messageable,
    ) -> None:
        """Delete the original message and DM the user, matching prior behavior."""

        try:
            await flagged_message.delete()
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                "I could not delete that message due to missing permissions or an API error.",
                ephemeral=True,
            )
            return

        dm_sent = await self._send_user_notice(flagged_message, blocked_word=blocked_word)
        await interaction.response.edit_message(view=None)
        await self._send_action_result(
            target_channel=log_channel,
            moderator=interaction.user,
            action="Proceed",
            message=flagged_message,
            blocked_word=blocked_word,
            dm_sent=dm_sent,
        )

    def _build_flag_review_view(
        self,
        *,
        flagged_message: discord.Message,
        blocked_word: str,
        log_channel: discord.abc.Messageable,
    ) -> discord.ui.View:
        """Create the Ignore/Proceed action buttons attached to each profanity flag."""

        view = discord.ui.View(timeout=3600)

        # Store action state on the view object so only one moderator action is processed.
        view.action_taken = False  # type: ignore[attr-defined]

        async def _deny_if_already_handled(interaction: discord.Interaction) -> bool:
            if not getattr(view, "action_taken", False):
                return False
            await interaction.response.send_message("This flag has already been handled.", ephemeral=True)
            return True

        ignore_button = discord.ui.Button(label="Ignore", style=discord.ButtonStyle.secondary)
        proceed_button = discord.ui.Button(label="Proceed", style=discord.ButtonStyle.danger)

        async def _ignore_callback(interaction: discord.Interaction) -> None:
            if await _deny_if_already_handled(interaction):
                return
            view.action_taken = True  # type: ignore[attr-defined]
            await self._handle_ignore_action(
                interaction=interaction,
                flagged_message=flagged_message,
                blocked_word=blocked_word,
                log_channel=log_channel,
            )

        async def _proceed_callback(interaction: discord.Interaction) -> None:
            if await _deny_if_already_handled(interaction):
                return
            view.action_taken = True  # type: ignore[attr-defined]
            await self._handle_proceed_action(
                interaction=interaction,
                flagged_message=flagged_message,
                blocked_word=blocked_word,
                log_channel=log_channel,
            )

        ignore_button.callback = _ignore_callback
        proceed_button.callback = _proceed_callback
        view.add_item(ignore_button)
        view.add_item(proceed_button)
        return view

    async def _handle_message_for_profanity(self, message: discord.Message) -> None:
        """Flag a profane guild message and request moderator action via buttons."""

        # Ignore bots/webhooks so the filter only targets member-authored chat.
        if message.author.bot or message.webhook_id is not None:
            return

        # The requested server-configured logging behavior only applies inside guilds.
        if message.guild is None or not message.content:
            return

        blocked_word = self._match_blocked_word(message.content)
        if blocked_word is None:
            return

        log_channel = await self._resolve_log_channel(message)
        if log_channel is None:
            return

        flagged_embed = self._build_flagged_embed(message, blocked_word=blocked_word)
        review_view = self._build_flag_review_view(
            flagged_message=message,
            blocked_word=blocked_word,
            log_channel=log_channel,
        )
        await log_channel.send(embed=flagged_embed, view=review_view)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Run profanity enforcement for newly created guild messages."""

        await self._handle_message_for_profanity(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        """Re-check edited messages so profanity added after posting is still removed."""

        # Skip no-op edit events to avoid duplicate delete attempts from embed/cache updates.
        if before.content == after.content:
            return

        await self._handle_message_for_profanity(after)


async def setup(bot: commands.Bot) -> None:
    """Discord extension entrypoint for loading this profanity filter cog."""

    await bot.add_cog(ProfanityCog(bot))
