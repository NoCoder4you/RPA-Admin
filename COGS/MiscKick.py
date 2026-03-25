"""Discord cog that provides a moderation `/kick` slash command."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


class KickCog(commands.Cog):
    """Moderation cog containing a staff-only kick command."""

    def __init__(self, bot: commands.Bot) -> None:
        # Keep a bot reference for consistency with the project's other cogs.
        self.bot = bot

    @staticmethod
    def _build_kick_dm_embed(guild_name: str, moderator: object, reason: str) -> discord.Embed:
        """Create the direct-message embed shown to members before they are kicked."""

        # Keep moderation DMs consistent and easier to read than a plain text block.
        return discord.Embed(
            description=(
                f"You are being kicked from **{guild_name}**.\n"
                f"**Moderator:** {moderator}\n"
                f"**Reason:** {reason}"
            ),
            color=discord.Color.orange(),
        )

    @staticmethod
    def _build_kick_success_embed(member_mention: str, reason: str, dm_status_note: str) -> discord.Embed:
        """Create the confirmation embed sent after a successful kick."""

        # Surface the outcome and DM status inside one structured moderation response.
        return discord.Embed(
            title="Member Kicked",
            description=f"✅ Kicked {member_mention}",
            color=discord.Color.orange(),
        ).add_field(name="Reason", value=reason, inline=False).add_field(
            name="DM Status",
            value=dm_status_note,
            inline=False,
        )

    @app_commands.command(name="kick", description="Kick a member from the server with an optional reason.")
    @app_commands.describe(
        mention="The member to remove from this server",
        reason="Why this member is being kicked",
    )
    @app_commands.checks.has_permissions(kick_members=True)
    @app_commands.checks.bot_has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, mention: discord.Member, reason: str) -> None:
        """Kick a guild member when the invoker and bot both have required permissions."""

        # This command only makes sense in a guild context where members can be moderated.
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        # Prevent users from kicking themselves by mistake or as an abuse edge case.
        if mention.id == interaction.user.id:
            await interaction.response.send_message("You cannot kick yourself.", ephemeral=True)
            return

        # Prevent attempting to kick the server owner to avoid a guaranteed API failure.
        if mention.id == interaction.guild.owner_id:
            await interaction.response.send_message("I cannot kick the server owner.", ephemeral=True)
            return

        # Enforce role hierarchy so moderators cannot kick members above or equal to them.
        if isinstance(interaction.user, discord.Member) and mention.top_role >= interaction.user.top_role:
            await interaction.response.send_message(
                "You can only kick members with a lower top role than yours.",
                ephemeral=True,
            )
            return

        # Enforce bot hierarchy so we fail gracefully before calling the API.
        bot_member = interaction.guild.me
        if bot_member is not None and mention.top_role >= bot_member.top_role:
            await interaction.response.send_message(
                "I cannot kick that member because their top role is higher than or equal to mine.",
                ephemeral=True,
            )
            return

        # Try to notify the target user first so they understand who initiated the action and why.
        # If DMs are closed (or Discord rejects the DM), continue with the kick instead of blocking moderation.
        pre_kick_dm_sent = False
        try:
            await mention.send(embed=self._build_kick_dm_embed(interaction.guild.name, interaction.user, reason))
            pre_kick_dm_sent = True
        except Exception:
            # DM delivery is best-effort; any error should not prevent the moderation action.
            pre_kick_dm_sent = False

        try:
            await mention.kick(reason=f"{interaction.user} - {reason}")
        except discord.Forbidden:
            await interaction.response.send_message(
                "Kick failed: I do not have permission to kick that member.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.response.send_message(
                "Kick failed due to a Discord API error. Please try again.",
                ephemeral=True,
            )
            return

        # Confirm success with a concise moderation message visible to command invoker.
        dm_status_note = (
            "I sent them a DM with the reason before kicking."
            if pre_kick_dm_sent
            else "I could not DM them first (likely due to their privacy settings)."
        )
        await interaction.response.send_message(
            embed=self._build_kick_success_embed(mention.mention, reason, dm_status_note),
            ephemeral=True,
        )

    @kick.error
    async def kick_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        """Return clear permission guidance for known slash-command check failures."""

        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the **Kick Members** permission to use `/kick`.",
                ephemeral=True,
            )
            return

        if isinstance(error, app_commands.BotMissingPermissions):
            await interaction.response.send_message(
                "I need the **Kick Members** permission to use `/kick`.",
                ephemeral=True,
            )
            return

        raise error


async def setup(bot: commands.Bot) -> None:
    """Discord extension entrypoint for loading this cog."""

    await bot.add_cog(KickCog(bot))
