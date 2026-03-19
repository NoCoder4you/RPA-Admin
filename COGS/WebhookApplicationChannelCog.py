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


class ApplicationClaimView(discord.ui.View):
    """Allow staff to grant themselves access to a newly created application channel."""

    def __init__(self, *, application_channel_id: int) -> None:
        # Keep the claim control active until staff no longer need to self-assign access.
        super().__init__(timeout=None)
        self.application_channel_id = application_channel_id

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.success, custom_id="application_channel:claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Grant the clicking staff member permission to view and speak in the application channel."""

        if interaction.guild is None:
            await interaction.response.send_message(
                "This claim button can only be used inside a server.",
                ephemeral=True,
            )
            return

        target_channel = interaction.guild.get_channel(self.application_channel_id)
        if target_channel is None:
            target_channel = interaction.client.get_channel(self.application_channel_id)
        if target_channel is None:
            await interaction.response.send_message(
                "I could not find the application channel tied to this claim button.",
                ephemeral=True,
            )
            return

        try:
            # Grant the claimer the minimum permissions needed to work the application inside the target channel.
            await target_channel.set_permissions(
                interaction.user,
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            )
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                "I could not grant you access to that application channel.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"You can now view and respond in {target_channel.mention}.",
            ephemeral=True,
        )


class WebhookApplicationChannelCog(commands.Cog):
    """Create a new application channel when a trusted webhook posts a channel-create command."""

    # Accept a strict, machine-friendly command so webhook integrations remain predictable.
    COMMAND_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"^RPA\s+channelcreate\s+(?P<unit_prefix>[A-Za-z0-9_-]+)\s+(?P<username>.+?)\s*$",
        re.IGNORECASE,
    )

    # Restrict channel creation to the currently approved application prefixes provided by the user.
    ALLOWED_UNIT_PREFIXES: Final[frozenset[str]] = frozenset({"IA", "MT", "ET", "EA", "TU"})

    NEW_APPLICATION_MESSAGE: Final[str] = "# New Unit Application Recieved"

    def __init__(self, bot: commands.Bot) -> None:
        # Store the bot reference for parity with the rest of the project and easier testing.
        self.bot = bot
        # Resolve all channel and role IDs from shared server configuration instead of hardcoding them here.
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

    def _build_new_application_notification(self) -> str:
        """Build the staff notification posted into the configured new-applications channel."""

        unit_leadership_role_id = self.server_config_store.get_unit_leadership_role_id()
        leadership_mention = (
            f"<@&{unit_leadership_role_id}>" if unit_leadership_role_id is not None else "@Unit Leadership"
        )
        return f"{leadership_mention}\n{self.NEW_APPLICATION_MESSAGE}"

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

    async def _send_new_application_notification(self, created_channel: discord.TextChannel) -> bool:
        """Post the Unit Leadership notification with a Claim button in the configured new-applications channel."""

        new_applications_channel_id = self.server_config_store.get_new_applications_channel_id()
        if new_applications_channel_id is None:
            return False

        new_applications_channel = self.bot.get_channel(new_applications_channel_id)
        if new_applications_channel is None:
            return False

        try:
            await new_applications_channel.send(
                content=self._build_new_application_notification(),
                view=ApplicationClaimView(application_channel_id=created_channel.id),
            )
            return True
        except (discord.Forbidden, discord.HTTPException, AttributeError):
            return False

    async def _delete_channel_create_message(self, message: discord.Message) -> None:
        """Delete the original webhook command message after the new channel is ready."""

        try:
            # Clean up the one-time webhook command so the source channel does not retain stale create requests.
            await message.delete()
        except (AttributeError, discord.Forbidden, discord.HTTPException):
            pass

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

        if not await self._send_archived_webhook_message(created_channel):
            try:
                # Remove the channel if the archived webhook message cannot be delivered, preventing incomplete setup.
                await created_channel.delete(reason="Failed to forward archived webhook resources after channel creation")
            except (discord.Forbidden, discord.HTTPException):
                pass
            return

        if not await self._send_new_application_notification(created_channel):
            try:
                # Remove the channel if staff cannot be notified, preventing unseen application channels from piling up.
                await created_channel.delete(reason="Failed to send new application notification after channel creation")
            except (discord.Forbidden, discord.HTTPException):
                pass
            return

        await self._delete_channel_create_message(message)


async def setup(bot: commands.Bot) -> None:
    """Discord extension entrypoint for loading the webhook application cog."""

    await bot.add_cog(WebhookApplicationChannelCog(bot))
