from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


class BanCog(commands.Cog):
    """Moderation cog containing a staff-only ban command."""

    def __init__(self, bot: commands.Bot) -> None:
        # Keep a bot reference for consistency with the project's other cogs.
        self.bot = bot

    @staticmethod
    def _build_ban_dm_embed(guild_name: str, moderator: object, reason: str) -> discord.Embed:
        """Create the direct-message embed shown to members before they are banned."""

        # Match kick/ban notifications so disciplinary messages have one consistent style.
        return discord.Embed(
            description=(
                f"You are being banned from **{guild_name}**.\n"
                f"**Moderator:** {moderator}\n"
                f"**Reason:** {reason}"
            ),
            color=discord.Color.red(),
        )

    @staticmethod
    def _build_ban_success_embed(member_mention: str, reason: str, dm_notice: str) -> discord.Embed:
        """Create the confirmation embed sent after a successful ban."""

        # Keep moderation confirmations easy to scan in-channel for staff.
        return discord.Embed(
            title="Member Banned",
            description=f"✅ Banned {member_mention}",
            color=discord.Color.red(),
        ).add_field(name="Reason", value=reason, inline=False).add_field(
            name="DM Status",
            value=dm_notice,
            inline=False,
        )

    @app_commands.command(name="ban", description="Ban a member from the server with a reason.")
    @app_commands.describe(
        mention="The member to ban from this server",
        reason="Why this member is being banned",
    )
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.checks.bot_has_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, mention: discord.Member, reason: str) -> None:
        """Ban a guild member when invoker and bot both have required permissions."""

        # This command only makes sense in a guild context where members can be moderated.
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        # Prevent users from banning themselves by mistake or as an abuse edge case.
        if mention.id == interaction.user.id:
            await interaction.response.send_message("You cannot ban yourself.", ephemeral=True)
            return

        # Prevent attempting to ban the server owner to avoid a guaranteed API failure.
        if mention.id == interaction.guild.owner_id:
            await interaction.response.send_message("I cannot ban the server owner.", ephemeral=True)
            return

        # Enforce role hierarchy so moderators cannot ban members above or equal to them.
        if isinstance(interaction.user, discord.Member) and mention.top_role >= interaction.user.top_role:
            await interaction.response.send_message(
                "You can only ban members with a lower top role than yours.",
                ephemeral=True,
            )
            return

        # Enforce bot hierarchy so we fail gracefully before calling the API.
        bot_member = interaction.guild.me
        if bot_member is not None and mention.top_role >= bot_member.top_role:
            await interaction.response.send_message(
                "I cannot ban that member because their top role is higher than or equal to mine.",
                ephemeral=True,
            )
            return

        # Attempt to DM the target before the ban so they receive context and appeal details.
        dm_sent = False
        try:
            await mention.send(embed=self._build_ban_dm_embed(interaction.guild.name, interaction.user, reason))
            dm_sent = True
        except Exception:
            # Continue with the ban even when the DM cannot be delivered for any reason.
            dm_sent = False

        try:
            await mention.ban(reason=f"{interaction.user} - {reason}")
        except discord.Forbidden:
            await interaction.response.send_message(
                "Ban failed: I do not have permission to ban that member.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.response.send_message(
                "Ban failed due to a Discord API error. Please try again.",
                ephemeral=True,
            )
            return

        # Confirm success with a concise moderation message visible to command invoker.
        dm_notice = "The user was notified via DM before the ban." if dm_sent else "I could not DM the user before ban."
        await interaction.response.send_message(
            embed=self._build_ban_success_embed(mention.mention, reason, dm_notice),
            ephemeral=True,
        )

    @ban.error
    async def ban_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        """Return clear permission guidance for known slash-command check failures."""

        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the **Ban Members** permission to use `/ban`.",
                ephemeral=True,
            )
            return

        if isinstance(error, app_commands.BotMissingPermissions):
            await interaction.response.send_message(
                "I need the **Ban Members** permission to use `/ban`.",
                ephemeral=True,
            )
            return

        raise error


async def setup(bot: commands.Bot) -> None:
    """Discord extension entrypoint for loading this cog."""

    await bot.add_cog(BanCog(bot))
