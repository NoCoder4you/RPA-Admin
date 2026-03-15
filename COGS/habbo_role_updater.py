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
        name="update_verified_roles",
        description="Manually run the automatic verified-user role updater now.",
    )
    @app_commands.checks.has_permissions(manage_roles=True)
    async def update_verified_roles(self, interaction: discord.Interaction) -> None:
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

    @update_verified_roles.error
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

            role_status, assigned_role_names = await self._assign_roles_to_member_from_profile(guild, member, profile)
            if role_status.startswith("Assigned:"):
                summary["updated"] += 1
            elif role_status.startswith("Failed"):
                summary["errors"] += 1
            else:
                summary["skipped"] += 1

            await self._send_audit_log_for_guild(
                guild=guild,
                action="verified_role_updater_sync",
                details={
                    "trigger": trigger,
                    "triggered_by": triggered_by or "system",
                    "discord_user_id": discord_id,
                    "discord_user": str(member),
                    "habbo_username": habbo_username,
                    "role_sync_status": role_status,
                    "assigned_roles": ", ".join(assigned_role_names) if assigned_role_names else "none",
                },
            )

        return summary

    def _get_primary_guild(self) -> discord.Guild | None:
        """Return the first guild because this bot is configured for one server."""

        if not self.bot.guilds:
            return None
        return self.bot.guilds[0]

    async def _assign_roles_to_member_from_profile(
        self,
        guild: discord.Guild,
        member: discord.Member,
        profile: dict,
    ) -> tuple[str, list[str]]:
        """Assign mapped roles to a specific member using a Habbo profile."""

        unique_id = str(profile.get("uniqueId", "")).strip()
        if not unique_id:
            return "Skipped (Habbo profile has no uniqueId for group lookup).", []

        try:
            habbo_group_ids = fetch_habbo_group_ids(unique_id)
            role_ids = self.badge_role_mapper.resolve_role_ids(habbo_group_ids)
        except HabboApiError:
            return "Skipped (could not fetch Habbo groups right now).", []

        if not role_ids:
            return "No matching roles found from your Habbo groups.", []

        roles_to_add: list[discord.Role] = []
        for role_id in role_ids:
            role = guild.get_role(role_id)
            if role is not None:
                roles_to_add.append(role)

        if not roles_to_add:
            return "No mapped roles exist in this server.", []

        try:
            await member.add_roles(*roles_to_add, reason="Habbo automatic role updater", atomic=False)
        except discord.Forbidden:
            return "Failed (bot lacks permission to assign one or more roles).", []

        role_names = [role.name for role in roles_to_add]
        return "Assigned: " + ", ".join(role_names), role_names

    async def _send_audit_log_for_guild(self, guild: discord.Guild, action: str, details: dict[str, str]) -> None:
        """Send an audit-style embed to the configured channel from serverconfig.json."""

        channel_id = self.server_config_store.get_audit_channel_id()
        if channel_id is None:
            return

        channel = guild.get_channel(channel_id)
        if channel is None:
            return

        embed = discord.Embed(
            title="Habbo Verification Audit",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Action", value=action, inline=False)
        for key, value in details.items():
            embed.add_field(name=key.replace("_", " ").title(), value=value, inline=False)

        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return


async def setup(bot: commands.Bot) -> None:
    """discord.py extension entry point."""

    await bot.add_cog(HabboRoleUpdaterCog(bot))
