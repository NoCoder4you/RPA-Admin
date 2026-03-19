"""Webhook listener cog that creates application channels from structured webhook commands."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

import discord
from discord.ext import commands


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

    # Keep the form content in one place so future unit-specific changes are easy to make.
    DEFAULT_FORM_QUESTIONS: Final[tuple[str, ...]] = (
        "What is your Habbo username and what rank/position are you applying for?",
        "What prior experience do you have that is relevant to this unit?",
        "Why do you want to join this unit within RPA?",
        "What skills or strengths would you bring to the team?",
        "What is your usual availability and timezone?",
    )

    def __init__(self, bot: commands.Bot) -> None:
        # Store the bot reference for parity with the rest of the project and easier testing.
        self.bot = bot

    @classmethod
    def parse_channel_create_request(cls, content: str) -> ChannelCreateRequest | None:
        """Return normalized command details when the webhook payload matches the expected format."""

        match = cls.COMMAND_PATTERN.fullmatch(content.strip())
        if match is None:
            return None

        unit_prefix = match.group("unit_prefix").strip().upper()
        username = match.group("username").strip()
        if not username:
            return None

        return ChannelCreateRequest(unit_prefix=unit_prefix, username=username)

    @staticmethod
    def build_channel_name(username: str) -> str:
        """Convert the applicant username into a Discord-safe text channel name."""

        # Lowercase and replace spaces/invalid separators with hyphens for Discord compatibility.
        normalized = re.sub(r"[^a-z0-9]+", "-", username.lower()).strip("-")

        # Discord requires a non-empty name, so keep a deterministic fallback if normalization strips everything.
        return normalized[:100] or "application"

    @classmethod
    def build_application_embed(cls, request: ChannelCreateRequest) -> discord.Embed:
        """Create the branded application embed posted inside the newly created channel."""

        embed = discord.Embed(
            title=f"{request.unit_prefix} Application Form",
            description=(
                f"Welcome **{request.username}**. Please answer the questions below for the "
                f"**{request.unit_prefix}** application process."
            ),
            color=discord.Color.blue(),
        )
        embed.add_field(name="Applicant Username", value=request.username, inline=True)
        embed.add_field(name="Unit Prefix", value=request.unit_prefix, inline=True)
        embed.add_field(
            name="Instructions",
            value=(
                "Reply in this channel with complete answers to each question so staff can review your application."
            ),
            inline=False,
        )

        for index, question in enumerate(cls.DEFAULT_FORM_QUESTIONS, start=1):
            embed.add_field(name=f"Question {index}", value=question, inline=False)

        embed.set_footer(text="RPA Application Automation")
        return embed

    async def _create_application_channel(
        self,
        message: discord.Message,
        request: ChannelCreateRequest,
    ) -> discord.TextChannel | None:
        """Create the applicant channel in the same category as the webhook message when possible."""

        if message.guild is None:
            return None

        channel_name = self.build_channel_name(request.username)
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

        embed = self.build_application_embed(request)
        try:
            await created_channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            # If the initial form cannot be posted, remove the channel to avoid leaving broken stubs behind.
            try:
                await created_channel.delete(reason="Failed to send application form after webhook channel creation")
            except (discord.Forbidden, discord.HTTPException):
                pass


async def setup(bot: commands.Bot) -> None:
    """Discord extension entrypoint for loading the webhook application cog."""

    await bot.add_cog(WebhookApplicationChannelCog(bot))
