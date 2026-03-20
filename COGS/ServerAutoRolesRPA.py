from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from habbo_verification_core import (
    BadgeRoleMapper,
    HabboApiError,
    ServerConfigStore,
    VerifiedUserStore,
    fetch_habbo_group_ids,
    fetch_habbo_profile,
)


class HabboRoleUpdaterCog(commands.Cog):
    """Cog that periodically syncs roles for all previously verified users."""

    VERIFICATION_LOG_CHANNEL_ID = 1481456997726425168

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.verified_store = VerifiedUserStore()
        self.badge_role_mapper = BadgeRoleMapper()
        self.server_config_store = ServerConfigStore()

        # Background updater is intentionally separate from /verify command flow.
        self.automatic_role_updater.start()

    def cog_unload(self) -> None:
        """Stop background updater task when cog unloads."""

        self.automatic_role_updater.cancel()

    @tasks.loop(minutes=10)
    async def automatic_role_updater(self) -> None:
        """Periodically synchronize roles for all users in VerifiedUsers.json."""

        await self._sync_all_verified_users(trigger="auto_loop")

    @automatic_role_updater.before_loop
    async def before_automatic_role_updater(self) -> None:
        """Wait until bot cache is ready before running updater."""

        await self.bot.wait_until_ready()

    @app_commands.command(
        name="uva",
        description="Manually run the automatic verified-user role updater now.",
    )
    @app_commands.checks.has_permissions(manage_roles=True)
    async def UVA(self, interaction: discord.Interaction) -> None:
        """Manually trigger standalone updater and return a concise summary."""

        await interaction.response.defer(ephemeral=True, thinking=True)
        summary = await self._sync_all_verified_users(trigger="manual_command", triggered_by=str(interaction.user))

        embed = discord.Embed(
            title="Verified Role Updater Complete",
            description="Finished syncing roles for saved verified users.",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Total Entries", value=str(summary["total_entries"]), inline=True)
        embed.add_field(name="Updated", value=str(summary["updated"]), inline=True)
        embed.add_field(name="Skipped", value=str(summary["skipped"]), inline=True)
        embed.add_field(name="Errors", value=str(summary["errors"]), inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)

    @UVA.error
    async def update_verified_roles_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        """Provide clear feedback when permission checks fail."""

        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the **Manage Roles** permission to run `/update_verified_roles`.",
                ephemeral=True,
            )
            return
        raise error

    async def _sync_all_verified_users(self, *, trigger: str, triggered_by: str | None = None) -> dict[str, int]:
        """Sync roles for every verified entry from JSON/VerifiedUsers.json."""

        summary = {"total_entries": 0, "updated": 0, "skipped": 0, "errors": 0}
        guild = self._get_primary_guild()
        if guild is None:
            return summary

        entries = self.verified_store.get_all_entries()
        summary["total_entries"] = len(entries)

        for entry in entries:
            discord_id = str(entry.get("discord_id", "")).strip()
            habbo_username = str(entry.get("habbo_username", "")).strip()
            if not discord_id or not habbo_username:
                summary["skipped"] += 1
                continue

            try:
                member = guild.get_member(int(discord_id))
            except ValueError:
                summary["skipped"] += 1
                continue

            if member is None:
                summary["skipped"] += 1
                continue

            try:
                profile = fetch_habbo_profile(habbo_username)
            except HabboApiError:
                summary["errors"] += 1
                continue

            role_status, added_role_names, removed_role_names = await self._assign_roles_to_member_from_profile(
                guild,
                member,
                profile,
            )
            if added_role_names or removed_role_names:
                summary["updated"] += 1
            elif role_status.startswith("Failed"):
                summary["errors"] += 1
            else:
                summary["skipped"] += 1

            await self._send_role_change_embed_for_guild(
                guild=guild,
                member=member,
                added_role_names=added_role_names,
                removed_role_names=removed_role_names,
            )

        return summary

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Reapply saved verification state as soon as a previously verified member rejoins."""

        # Only re-sync members who already exist in the persisted verified-user store.
        stored_habbo_username = self.verified_store.get_habbo_username(str(member.id))
        if not stored_habbo_username:
            return

        try:
            profile = fetch_habbo_profile(stored_habbo_username)
        except HabboApiError:
            # Avoid raising from join events; the user can still be resynced later by the updater.
            return

        role_status, added_role_names, removed_role_names = await self._assign_roles_to_member_from_profile(
            member.guild,
            member,
            profile,
        )
        nickname_status = await self._sync_member_nickname(
            member=member,
            habbo_username=str(profile.get("name", stored_habbo_username)),
        )

        # Reuse the existing role-delta audit embed for moderator visibility when roles changed.
        await self._send_role_change_embed_for_guild(
            guild=member.guild,
            member=member,
            added_role_names=added_role_names,
            removed_role_names=removed_role_names,
        )
        await self._send_verification_rejoin_log(
            guild=member.guild,
            member=member,
            habbo_username=str(profile.get("name", stored_habbo_username)),
            role_status=role_status,
            nickname_status=nickname_status,
            added_role_names=added_role_names,
            removed_role_names=removed_role_names,
        )

    def _get_primary_guild(self) -> discord.Guild | None:
        """Return the first guild because this bot is configured for one server."""

        if not self.bot.guilds:
            return None
        return self.bot.guilds[0]

    async def _sync_member_nickname(self, *, member: discord.Member, habbo_username: str) -> str:
        """Rename a rejoining verified member so their nickname still matches their Habbo name."""

        if getattr(member, "nick", None) == habbo_username:
            return "No nickname change was required."

        try:
            await member.edit(
                nick=habbo_username,
                reason="Habbo automatic role updater nickname sync on member join",
            )
        except discord.Forbidden:
            return "Failed (bot lacks permission to manage this nickname)."
        except discord.HTTPException:
            return "Failed (Discord rejected the nickname update request)."

        return "Nickname updated to verified Habbo username."

    async def _assign_roles_to_member_from_profile(
        self,
        guild: discord.Guild,
        member: discord.Member,
        profile: dict,
    ) -> tuple[str, list[str], list[str]]:
        """Synchronize mapped roles to a specific member using a Habbo profile."""

        unique_id = str(profile.get("uniqueId", "")).strip()
        if not unique_id:
            return "Skipped (Habbo profile has no uniqueId for group lookup).", [], []

        try:
            habbo_group_ids = fetch_habbo_group_ids(unique_id)
            role_ids = self.badge_role_mapper.resolve_role_ids(habbo_group_ids)
        except HabboApiError:
            return "Skipped (could not fetch Habbo groups right now).", [], []

        # Build the desired roles from current Habbo groups and the full set of managed roles.
        target_roles: list[discord.Role] = []
        for role_id in role_ids:
            role = guild.get_role(role_id)
            if role is not None:
                target_roles.append(role)

        managed_role_ids = self.badge_role_mapper.get_all_mapped_role_ids()
        managed_roles: list[discord.Role] = []
        for role_id in managed_role_ids:
            role = guild.get_role(role_id)
            if role is not None:
                managed_roles.append(role)

        target_role_ids = {role.id for role in target_roles}
        current_role_ids = {role.id for role in member.roles}
        managed_role_id_set = {role.id for role in managed_roles}

        # Add missing mapped roles and remove stale mapped roles in one sync pass.
        roles_to_add = [role for role in target_roles if role.id not in current_role_ids]
        roles_to_remove = [role for role in member.roles if role.id in managed_role_id_set and role.id not in target_role_ids]

        try:
            if roles_to_add:
                await member.add_roles(*roles_to_add, reason="Habbo automatic role updater", atomic=False)
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason="Habbo automatic role updater", atomic=False)
        except discord.Forbidden:
            return "Failed (bot lacks permission to manage one or more roles).", [], []

        added_role_names = [role.name for role in roles_to_add]
        removed_role_names = [role.name for role in roles_to_remove]

        if not target_roles and not managed_roles:
            status = "No mapped roles exist in this server."
        elif not target_roles and not roles_to_remove:
            status = "No matching roles found from your Habbo groups."
        elif not added_role_names and not removed_role_names:
            status = "No role changes were required."
        else:
            status = (
                f"Added: {', '.join(added_role_names) if added_role_names else 'none'} | "
                f"Removed: {', '.join(removed_role_names) if removed_role_names else 'none'}"
            )

        return status, added_role_names, removed_role_names

    async def _send_role_change_embed_for_guild(
        self,
        *,
        guild: discord.Guild,
        member: discord.Member,
        added_role_names: list[str],
        removed_role_names: list[str],
    ) -> None:
        """Send a concise updater embed only when at least one role changed."""

        # Requirement: no role delta means no update embed should be posted.
        # This avoids cluttering the audit channel with "user-only" notifications.
        if not added_role_names and not removed_role_names:
            return

        channel_id = self.server_config_store.get_audit_channel_id()
        if channel_id is None:
            return

        channel = guild.get_channel(channel_id)
        if channel is None:
            return

        embed = discord.Embed(
            title="Habbo Role Sync Update",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )

        # Always mention the target user so moderators can open the member profile quickly.
        embed.add_field(name="User", value=member.mention, inline=False)

        # Only show sections for categories that actually changed to keep the updater output brief.
        if added_role_names:
            embed.add_field(name="Added Roles", value="\n".join(added_role_names), inline=False)
        if removed_role_names:
            embed.add_field(name="Removed Roles", value="\n".join(removed_role_names), inline=False)

        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return

    async def _send_verification_rejoin_log(
        self,
        *,
        guild: discord.Guild,
        member: discord.Member,
        habbo_username: str,
        role_status: str,
        nickname_status: str,
        added_role_names: list[str],
        removed_role_names: list[str],
    ) -> None:
        """Post a verification-log summary whenever a stored verified user rejoins the server."""

        channel = guild.get_channel(self.VERIFICATION_LOG_CHANNEL_ID)
        if channel is None:
            channel = self.bot.get_channel(self.VERIFICATION_LOG_CHANNEL_ID)
        if channel is None:
            return

        embed = discord.Embed(
            title="Verified Member Rejoined",
            description="Reapplied saved verification data for a returning member.",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
        # Keep the summary explicit so moderators can confirm join-time sync behavior quickly.
        embed.add_field(name="Member", value=member.mention, inline=False)
        embed.add_field(name="Habbo Username", value=habbo_username, inline=True)
        embed.add_field(name="Role Sync", value=role_status, inline=False)
        embed.add_field(name="Nickname Sync", value=nickname_status, inline=False)
        embed.add_field(name="Added Roles", value=", ".join(added_role_names) if added_role_names else "none", inline=False)
        embed.add_field(
            name="Removed Roles",
            value=", ".join(removed_role_names) if removed_role_names else "none",
            inline=False,
        )

        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return


async def setup(bot: commands.Bot) -> None:
    """discord.py extension entry point."""

    await bot.add_cog(HabboRoleUpdaterCog(bot))
