"""Discord cog that provides an anonymous `/embedmaker` slash command."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


class EmbedMakerCog(commands.Cog):
    """Community utility cog for sending branded anonymous embeds."""

    def __init__(self, bot: commands.Bot) -> None:
        # Keep a bot reference for consistency with the rest of the project cogs.
        self.bot = bot

    @staticmethod
    def _resolve_color(color_hex: str | None) -> discord.Color:
        """Parse an optional hex color string, falling back to Discord red.

        The command accepts values like `#ff0000` or `ff0000`. Invalid input is
        intentionally rejected by the caller so staff immediately know what to fix.
        """

        if not color_hex:
            return discord.Color.red()

        normalized = color_hex.strip().removeprefix("#")
        if len(normalized) != 6:
            raise ValueError("Embed colors must be 6-digit hexadecimal values.")

        try:
            return discord.Color(int(normalized, 16))
        except ValueError as exc:  # pragma: no cover - exercised via caller-facing branch.
            raise ValueError("Embed colors must be valid hexadecimal values.") from exc

    def _build_embed(
        self,
        *,
        title: str,
        description: str,
        thumbnail_url: str | None,
        color_hex: str | None = None,
    ) -> discord.Embed:
        """Create a branded embed using the requested anonymous presentation."""

        embed = discord.Embed(
            title=title,
            description=description,
            color=self._resolve_color(color_hex),
        )

        # Always brand the embed with the bot avatar when one is available.
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)

        # Use the exact footer text requested so every anonymous embed is consistent.
        embed.set_footer(text="Royal Protection Agency - Royal Guard")
        return embed

    @app_commands.command(name="embedmaker", description="Create and post an anonymous branded embed.")
    @app_commands.describe(
        title="The title shown at the top of the embed",
        description="The main message body shown inside the embed",
        color="Optional hex color such as #ff0000",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def embedmaker(
        self,
        interaction: discord.Interaction,
        title: app_commands.Range[str, 1, 256],
        description: app_commands.Range[str, 1, 4000],
        color: str | None = None,
    ) -> None:
        """Send a channel-visible embed without exposing who invoked the command."""

        # The command posts publicly in the current server channel, so require guild context.
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "This command can only be used in a server channel.",
                ephemeral=True,
            )
            return

        # Resolve the bot avatar once so the embed uses the bot identity rather than the author's.
        bot_avatar = getattr(getattr(interaction.guild, "me", None), "display_avatar", None)
        thumbnail_url = str(bot_avatar.url) if bot_avatar and getattr(bot_avatar, "url", None) else None

        try:
            embed = self._build_embed(
                title=title,
                description=description,
                thumbnail_url=thumbnail_url,
                color_hex=color,
            )
        except ValueError:
            await interaction.response.send_message(
                "Please provide a valid 6-digit hex color such as `#ff0000`.",
                ephemeral=True,
            )
            return

        # Post the anonymous embed to the active channel, then privately confirm success.
        await interaction.channel.send(embed=embed)
        await interaction.response.send_message(
            "✅ Anonymous embed sent.",
            ephemeral=True,
        )

    @embedmaker.error
    async def embedmaker_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        """Return clear feedback when staff permissions are missing."""

        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the **Manage Messages** permission to use `/embedmaker`.",
                ephemeral=True,
            )
            return

        raise error


async def setup(bot: commands.Bot) -> None:
    """Discord extension entrypoint for loading this cog."""

    await bot.add_cog(EmbedMakerCog(bot))
