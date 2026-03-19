"""Webhook listener cog that creates application channels from structured webhook commands."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

import discord
from discord.ext import commands

from habbo_verification_core import ServerConfigStore


@dataclass(frozen=True)
class ChannelCreateRequest:
    """Normalized webhook payload describing which application channel should be created."""

    unit_prefix: str
    username: str


class WebhookApplicationChannelCog(commands.Cog):
    """Create a new application channel when a trusted webhook posts a channel-create command."""

    # Accept a strict, machine-friendly command so webhook integrations remain predictable.
    COMMAND_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"^RPA\s+channelcreate\s+(?P<unit_prefix>[A-Za-z0-9_-]+)\s+(?P<username>.+?)\s*$",
        re.IGNORECASE,
    )

    # Restrict channel creation to the currently approved application prefixes provided by the user.
    ALLOWED_UNIT_PREFIXES: Final[frozenset[str]] = frozenset({"IA", "FU", "MT", "ET", "EA", "TU"})

    def __init__(self, bot: commands.Bot) -> None:
        # Store the bot reference for parity with the rest of the project and easier testing.
        self.bot = bot
        # Resolve the archive channel from shared server configuration instead of hardcoding it in the cog.
        self.server_config_store = ServerConfigStore()

    @classmethod
    def parse_channel_create_request(cls, content: str) -> ChannelCreateRequest | None:
        """Return normalized command details when the webhook payload matches the expected format."""

        match = cls.COMMAND_PATTERN.fullmatch(content.strip())
        if match is None:
            return None

        unit_prefix = match.group("unit_prefix").strip().upper()
        username = match.group("username").strip()
        if unit_prefix not in cls.ALLOWED_UNIT_PREFIXES or not username:
            return None

        return ChannelCreateRequest(unit_prefix=unit_prefix, username=username)

    @staticmethod
    def build_channel_name(unit_prefix: str, username: str) -> str:
        """Convert the prefix and applicant username into a Discord-safe text channel name."""

        # Lowercase the full channel slug because Discord text channels must remain lowercase.
        normalized_username = re.sub(r"[^a-z0-9]+", "-", username.lower()).strip("-")
        normalized_prefix = re.sub(r"[^a-z0-9]+", "-", unit_prefix.lower()).strip("-")

        # Keep the requested PREFIX-username structure while preserving deterministic fallbacks.
        if normalized_prefix and normalized_username:
            return f"{normalized_prefix}-{normalized_username}"[:100]
        if normalized_prefix:
            return normalized_prefix[:100]
        if normalized_username:
            return normalized_username[:100]
        return "application"

    async def _create_application_channel(
        self,
        message: discord.Message,
        request: ChannelCreateRequest,
    ) -> discord.TextChannel | None:
        """Create the applicant channel in the same category as the webhook message when possible."""

        if message.guild is None:
            return None

        channel_name = self.build_channel_name(request.unit_prefix, request.username)
        current_channel = message.channel
        category = getattr(current_channel, "category", None)

        try:
            return await message.guild.create_text_channel(
                channel_name,
                category=category,
                reason=(
                    f"Application channel requested by webhook for {request.unit_prefix} applicant {request.username}"
                ),
            )
        except (discord.Forbidden, discord.HTTPException):
            return None

    async def _get_archived_webhook_message(self) -> discord.Message | None:
        """Return the newest archived message that contains the reusable webhook embed."""

        archive_channel_id = self.server_config_store.get_webhook_archive_channel_id()
        if archive_channel_id is None:
            return None

        archive_channel = self.bot.get_channel(archive_channel_id)
        if archive_channel is None:
            return None

        try:
            # Walk newest-first so the latest archived webhook embed is always reused.
            async for archived_message in archive_channel.history(limit=25):
                if archived_message.embeds:
                    return archived_message
        except (AttributeError, discord.Forbidden, discord.HTTPException):
            return None

        return None

    async def _send_archived_webhook_message(self, created_channel: discord.TextChannel) -> bool:
        """Forward the archived webhook message into the new channel, falling back to embed copy when needed."""

        archived_message = await self._get_archived_webhook_message()
        if archived_message is None:
            return False

        try:
            # Prefer Discord's native forward behavior so the destination channel receives the archived message itself.
            await archived_message.forward(created_channel)
            return True
        except AttributeError:
            pass
        except (discord.Forbidden, discord.HTTPException):
            # If native forwarding exists but fails, do not silently post altered content.
            return False

        try:
            # Some discord.py builds may not expose `Message.forward`; in that case, resend the archived embed payload.
            await created_channel.send(
                content=archived_message.content or None,
                embed=archived_message.embeds[0].copy(),
            )
            return True
        except (discord.Forbidden, discord.HTTPException, IndexError, AttributeError):
            return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Create an application channel when a webhook posts the expected command string."""

        # This workflow is intentionally webhook-only so normal member chat cannot create channels.
        if message.webhook_id is None or not message.content:
            return

        # Ignore DMs because application channels can only exist inside guilds.
        if message.guild is None:
            return

        request = self.parse_channel_create_request(message.content)
        if request is None:
            return

        created_channel = await self._create_application_channel(message, request)
        if created_channel is None:
            return

        if await self._send_archived_webhook_message(created_channel):
            return

        try:
            # Remove the channel if the archived webhook message cannot be delivered, preventing incomplete setup.
            await created_channel.delete(reason="Failed to forward archived webhook resources after channel creation")
        except (discord.Forbidden, discord.HTTPException):
            pass


async def setup(bot: commands.Bot) -> None:
    """Discord extension entrypoint for loading the webhook application cog."""

    await bot.add_cog(WebhookApplicationChannelCog(bot))
