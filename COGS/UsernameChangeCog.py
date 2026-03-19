"""Discord cog that updates saved Habbo usernames after a user renames their Habbo account."""

from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from habbo_verification_core import HabboApiError, ServerConfigStore, VerifiedUserStore, fetch_habbo_profile


class UsernameChangeCog(commands.Cog):
    """Self-service cog for keeping verified Habbo username records in sync with Discord."""

    AUTOROLES_EXTENSION = "COGS.ServerAutoRolesRPA"

    def __init__(self, bot: commands.Bot) -> None:
        # Keep shared dependencies on the cog so tests can replace them with stubs.
        self.bot = bot
        self.verified_store = VerifiedUserStore()
        self.server_config_store = ServerConfigStore()

    @app_commands.command(
        name="usernamechange",
        description="Update your saved Habbo username after you rename your Habbo account.",
    )
    @app_commands.describe(username="Your new Habbo username")
    async def usernamechange(self, interaction: discord.Interaction, username: str) -> None:
        """Update the saved verified Habbo username, Discord nickname, and related role sync state."""

        # Defer because the command performs API fetches and an extension reload.
        await interaction.response.defer(ephemeral=True, thinking=True)
        result_message = await self._process_username_change(interaction, username)
        await interaction.followup.send(result_message, ephemeral=True)

    async def _process_username_change(self, interaction: discord.Interaction, username: str) -> str:
        """Run the full username-change workflow so command logic is easy to unit test."""

        discord_id = str(interaction.user.id)
        stored_username = self.verified_store.get_habbo_username(discord_id)
        if not stored_username:
            return "You are not currently verified, so there is no saved Habbo username to update."

        normalized_username = username.strip()
        if not normalized_username:
            return "Please provide a valid Habbo username."

        try:
            profile = fetch_habbo_profile(normalized_username)
        except HabboApiError as exc:
            return f"I could not fetch that Habbo profile right now: {exc}"

        verified_habbo_username = str(profile.get("name", normalized_username)).strip() or normalized_username

        # Persist the renamed Habbo account immediately so future role syncs use the new name.
        self.verified_store.save(discord_id=discord_id, habbo_username=verified_habbo_username)

        nickname_status = await self._sync_member_nickname(interaction, verified_habbo_username)
        reload_status = await self._reload_autoroles_cog()
        await self._send_verification_log_embed(
            interaction=interaction,
            previous_username=stored_username,
            updated_username=verified_habbo_username,
            nickname_status=nickname_status,
            reload_status=reload_status,
        )

        return (
            f"Updated your saved Habbo username from **{stored_username}** to **{verified_habbo_username}**.\n"
            f"Nickname: {nickname_status}\n"
            f"AutoRoles reload: {reload_status}"
        )

    async def _sync_member_nickname(self, interaction: discord.Interaction, habbo_username: str) -> str:
        """Rename the member in Discord so their nickname matches the verified Habbo username."""

        if interaction.guild is None:
            return "Skipped (nickname can only be changed inside a server)."

        member = interaction.user
        if getattr(member, "nick", None) == habbo_username:
            return "No nickname change was required."

        try:
            await member.edit(
                nick=habbo_username,
                reason="Habbo verification nickname sync",
            )
        except discord.Forbidden:
            return "Failed (bot lacks permission to manage this nickname)."
        except discord.HTTPException:
            return "Failed (Discord rejected the nickname update request)."

        return "Nickname updated to verified Habbo username."

    async def _reload_autoroles_cog(self) -> str:
        """Reload the automatic role updater so it immediately uses the refreshed username mapping."""

        try:
            await self.bot.reload_extension(self.AUTOROLES_EXTENSION)
        except commands.ExtensionNotLoaded:
            try:
                await self.bot.load_extension(self.AUTOROLES_EXTENSION)
            except commands.ExtensionError as exc:
                return f"Failed ({exc})"
            return "Loaded AutoRoles cog because it was not already loaded."
        except commands.ExtensionError as exc:
            return f"Failed ({exc})"

        return "Reloaded AutoRoles cog successfully."

    async def _send_verification_log_embed(
        self,
        *,
        interaction: discord.Interaction,
        previous_username: str,
        updated_username: str,
        nickname_status: str,
        reload_status: str,
    ) -> None:
        """Post a request embed in the configured requests channel and ping the configured admin role."""

        if interaction.guild is None:
            return

        request_channel_id = self.server_config_store.get_username_change_request_channel_id()
        if request_channel_id is None:
            return

        channel = interaction.guild.get_channel(request_channel_id)
        if channel is None:
            channel = self.bot.get_channel(request_channel_id)
        if channel is None:
            return

        admin_role_id = self.server_config_store.get_username_change_admin_role_id()

        embed = discord.Embed(
            title="Habbo Username Change Request",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Member", value=interaction.user.mention, inline=False)
        embed.add_field(name="Previous Username", value=previous_username, inline=True)
        embed.add_field(name="Updated Username", value=updated_username, inline=True)
        embed.add_field(name="Nickname Sync", value=nickname_status, inline=False)
        embed.add_field(name="AutoRoles Reload", value=reload_status, inline=False)

        try:
            content = f"<@&{admin_role_id}>" if admin_role_id else None
            await channel.send(content=content, embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return


async def setup(bot: commands.Bot) -> None:
    """Discord extension entrypoint for loading this cog."""

    await bot.add_cog(UsernameChangeCog(bot))
