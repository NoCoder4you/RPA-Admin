from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from habbo_verification_core import (
    BadgeRoleMapper,
    HabboApiError,
    HiddenProfileAlertStore,
    ServerConfigStore,
    VerifiedUserStore,
    VerifyRestrictionStore,
    fetch_habbo_group_ids,
    fetch_habbo_profile,
)


class HabboRoleUpdaterCog(commands.Cog):
    """Cog that periodically syncs roles for all previously verified users."""

    VERIFICATION_LOG_CHANNEL_ID = 1481456997726425168
    ERROR_LOG_CHANNEL_ID = 1484064305732259940
    MANUAL_SYNC_REQUEST_DELAY_SECONDS = 1.0

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.verified_store = VerifiedUserStore()
        self.hidden_profile_alert_store = HiddenProfileAlertStore()
        self.badge_role_mapper = BadgeRoleMapper()
        self.server_config_store = ServerConfigStore()
        self.verify_restriction_store = VerifyRestrictionStore()
        # Track temporary Habbo API backoff windows so we do not hammer the endpoint
        # when it starts returning HTTP 429 (Too Many Requests).
        self._habbo_rate_limited_until: datetime | None = None

        # Background updater is intentionally separate from /verify command flow.
        self.automatic_role_updater.start()

    def cog_unload(self) -> None:
        """Stop background updater task when cog unloads."""

        self.automatic_role_updater.cancel()

    @tasks.loop(minutes=15)
    async def automatic_role_updater(self) -> None:
        """Periodically synchronize roles for all users in VerifiedUsers.json."""
        # Keep the scheduler alive even if one sync cycle fails unexpectedly.
        # Without this guard, an uncaught exception permanently stops the 15-minute loop
        # until the cog/bot is restarted manually.
        try:
            summary = await self._sync_all_verified_users(trigger="auto_loop")
            await self._send_sync_summary_embed(
                trigger="auto_loop",
                summary=summary,
            )
        except Exception as exc:  # noqa: BLE001 - keep long-running task resilient.
            guild = self._get_primary_guild()
            if guild is None:
                return
            await self._send_error_embed(
                guild=guild,
                member=None,
                habbo_username="N/A",
                title="Habbo Auto Loop Failed",
                error_text=str(exc),
                context="Trigger: auto_loop",
            )

    @automatic_role_updater.before_loop
    async def before_automatic_role_updater(self) -> None:
        """Wait until bot cache is ready before running updater."""

        await self.bot.wait_until_ready()

    @app_commands.command(
        name="uva",
        description="Manually run the verified-user role updater, including uncached members.",
    )
    @app_commands.checks.has_permissions(manage_roles=True)
    async def UVA(self, interaction: discord.Interaction) -> None:
        """Manually trigger standalone updater and return a concise summary."""

        await interaction.response.defer(ephemeral=True, thinking=True)
        summary = await self._sync_all_verified_users(
            trigger="manual_command",
            triggered_by=str(interaction.user),
            guild_override=interaction.guild,
            request_delay_seconds=self.MANUAL_SYNC_REQUEST_DELAY_SECONDS,
        )

        embed = discord.Embed(
            title="Verified Role Updater Complete",
            description="Finished syncing roles for saved verified users, including uncached members.",
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
                "You need the **Manage Roles** permission to run `/uva`.",
                ephemeral=True,
            )
            return
        raise error

    @commands.command(name="refreshroles", help="Silently refresh one member's mapped roles from their saved VerifiedUsers entry.")
    @commands.has_permissions(manage_roles=True)
    async def refreshroles(self, ctx: commands.Context, member: discord.Member) -> None:
        """Quietly force a saved verified user's role sync from their stored Habbo username."""

        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException, AttributeError):
            pass

        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.", delete_after=10)
            return

        success, message = await self._refresh_member_roles_from_saved_username(
            guild=ctx.guild,
            member=member,
            trigger="text_command",
        )
        if success:
            return

        await ctx.send(message, delete_after=10)

    @refreshroles.error
    async def refreshroles_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        """Return compact feedback for text-command usage errors."""

        if isinstance(error, commands.MissingPermissions):
            await ctx.send("You need the **Manage Roles** permission to run `RPA refreshroles`.", delete_after=10)
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("Usage: `RPA refreshroles @member`", delete_after=10)
            return
        if isinstance(error, commands.BadArgument):
            await ctx.send("I couldn't resolve that member. Use a mention or valid member ID.", delete_after=10)
            return
        raise error

    async def _sync_all_verified_users(
        self,
        *,
        trigger: str,
        triggered_by: str | None = None,
        guild_override: discord.Guild | None = None,
        request_delay_seconds: float = 0.0,
    ) -> dict[str, int]:
        """Sync roles for every verified entry from JSON/VerifiedUsers.json."""

        summary = {"total_entries": 0, "updated": 0, "skipped": 0, "errors": 0}
        guild = guild_override or self._get_primary_guild()
        if guild is None:
            return summary

        entries = self.verified_store.get_all_entries()
        summary["total_entries"] = len(entries)

        if self._is_habbo_rate_limited_now():
            # Skip the whole cycle while the cooldown is active to prevent repeated 429s.
            summary["skipped"] = len(entries)
            return summary

        fetch_attempt_count = 0

        for index, entry in enumerate(entries):
            discord_id = str(entry.get("discord_id", "")).strip()
            habbo_username = str(entry.get("habbo_username", "")).strip()
            if not discord_id or not habbo_username:
                summary["skipped"] += 1
                continue

            try:
                member_id = int(discord_id)
            except ValueError:
                summary["skipped"] += 1
                continue

            member = await self._resolve_member(guild, member_id)
            if member is None:
                summary["skipped"] += 1
                continue

            # Space out Habbo API requests during manual `/uva` runs so large batches are
            # less likely to trigger HTTP 429 responses.
            if fetch_attempt_count > 0 and request_delay_seconds > 0:
                await asyncio.sleep(request_delay_seconds)

            try:
                profile = fetch_habbo_profile(habbo_username)
                fetch_attempt_count += 1
            except HabboApiError as exc:
                fetch_attempt_count += 1
                if self._is_rate_limit_error(exc):
                    # Enter cooldown and stop this cycle immediately so one rate-limit event
                    # does not generate dozens of identical errors for the remaining users.
                    cooldown_until = self._begin_habbo_rate_limit_cooldown()
                    # Only count entries that were not processed yet.
                    # The current entry is already accounted for in `errors`.
                    remaining_entries = len(entries) - index - 1
                    summary["skipped"] += max(remaining_entries, 0)
                    await self._send_error_embed(
                        guild=guild,
                        member=member,
                        habbo_username=habbo_username,
                        title="Habbo API Rate Limited",
                        error_text=(
                            f"{exc}. Auto updater paused until "
                            f"{cooldown_until.strftime('%Y-%m-%d %H:%M:%S UTC')}."
                        ),
                        context=f"Trigger: {trigger}",
                    )
                    summary["errors"] += 1
                    break
                await self._send_error_embed(
                    guild=guild,
                    member=member,
                    habbo_username=habbo_username,
                    title="Habbo Profile Fetch Failed",
                    error_text=str(exc),
                    context=f"Trigger: {trigger}",
                )
                summary["errors"] += 1
                continue

            await self._handle_hidden_profile_audit_state(
                guild=guild,
                member=member,
                habbo_username=habbo_username,
                profile=profile,
            )
            role_status, added_role_names, removed_role_names = await self._assign_roles_to_member_from_profile(
                guild,
                member,
                profile,
            )
            if added_role_names or removed_role_names:
                summary["updated"] += 1
            elif role_status.startswith("Failed"):
                await self._send_error_embed(
                    guild=guild,
                    member=member,
                    habbo_username=habbo_username,
                    title="Habbo Role Sync Failed",
                    error_text=role_status,
                    context=f"Trigger: {trigger}",
                )
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

    async def _resolve_member(self, guild: discord.Guild, member_id: int) -> discord.Member | None:
        """Resolve a guild member from cache first, then fall back to an API fetch."""

        member = guild.get_member(member_id)
        if member is not None:
            return member

        try:
            return await guild.fetch_member(member_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, AttributeError):
            return None

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Reapply saved verification state as soon as a previously verified member rejoins."""

        # Only re-sync members who already exist in the persisted verified-user store.
        stored_habbo_username = self.verified_store.get_habbo_username(str(member.id))
        if not stored_habbo_username:
            return

        try:
            profile = fetch_habbo_profile(stored_habbo_username)
        except HabboApiError as exc:
            await self._send_error_embed(
                guild=member.guild,
                member=member,
                habbo_username=stored_habbo_username,
                title="Habbo Rejoin Sync Fetch Failed",
                error_text=str(exc),
                context="Trigger: member_join",
            )
            # Avoid raising from join events; the user can still be resynced later by the updater.
            return

        await self._handle_hidden_profile_audit_state(
            guild=member.guild,
            member=member,
            habbo_username=stored_habbo_username,
            profile=profile,
        )
        verified_habbo_username = str(profile.get("name", stored_habbo_username))
        restriction_group = self.verify_restriction_store.get_group_for_username(verified_habbo_username)
        if restriction_group is not None:
            # Do not reapply verified/member access if the saved Habbo account is now on a restriction list.
            await self._send_verification_rejoin_log(
                guild=member.guild,
                member=member,
                habbo_username=verified_habbo_username,
                role_status=f"Skipped (member is restricted under {restriction_group}).",
                nickname_status="Skipped (restricted members are not resynced on join).",
                added_role_names=[],
                removed_role_names=[],
            )
            return

        role_status, added_role_names, removed_role_names = await self._assign_roles_to_member_from_profile(
            member.guild,
            member,
            profile,
        )
        verified_role_status, verified_role_names = await self._ensure_verified_role(member.guild, member)
        if verified_role_names:
            added_role_names.extend(verified_role_names)
            # Fold the guaranteed Verified role grant into the human-readable role summary shown in logs.
            if role_status == "No role changes were required.":
                role_status = f"Added: {', '.join(verified_role_names)} | Removed: none"
            else:
                role_status = f"{role_status} | Verified Role: {verified_role_status}"
        nickname_status = await self._sync_member_nickname(
            member=member,
            habbo_username=verified_habbo_username,
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
            habbo_username=verified_habbo_username,
            role_status=role_status,
            nickname_status=nickname_status,
            added_role_names=added_role_names,
            removed_role_names=removed_role_names,
        )

    async def _refresh_member_roles_from_saved_username(
        self,
        *,
        guild: discord.Guild,
        member: discord.Member,
        trigger: str,
    ) -> tuple[bool, str]:
        """Refresh one member's roles using the Habbo username already saved for that Discord ID."""

        stored_habbo_username = self.verified_store.get_habbo_username(str(member.id))
        if not stored_habbo_username:
            return False, "That member does not have a saved verified Habbo username in `VerifiedUsers.json`."

        try:
            profile = fetch_habbo_profile(stored_habbo_username)
        except HabboApiError as exc:
            await self._send_error_embed(
                guild=guild,
                member=member,
                habbo_username=stored_habbo_username,
                title="Habbo Manual Refresh Fetch Failed",
                error_text=str(exc),
                context=f"Trigger: {trigger}",
            )
            return False, "I couldn't fetch that member's saved Habbo profile right now."

        await self._handle_hidden_profile_audit_state(
            guild=guild,
            member=member,
            habbo_username=stored_habbo_username,
            profile=profile,
        )

        role_status, added_role_names, removed_role_names = await self._assign_roles_to_member_from_profile(
            guild,
            member,
            profile,
        )
        await self._send_role_change_embed_for_guild(
            guild=guild,
            member=member,
            added_role_names=added_role_names,
            removed_role_names=removed_role_names,
        )

        if role_status.startswith("Failed"):
            await self._send_error_embed(
                guild=guild,
                member=member,
                habbo_username=stored_habbo_username,
                title="Habbo Manual Refresh Failed",
                error_text=role_status,
                context=f"Trigger: {trigger}",
            )
            return False, role_status

        return True, role_status

    def _get_primary_guild(self) -> discord.Guild | None:
        """Return the configured main guild when available, otherwise fall back to the first guild."""

        configured_main_server_id = self.server_config_store.get_main_server_id()
        if configured_main_server_id is not None:
            configured_guild = self.bot.get_guild(configured_main_server_id)
            if configured_guild is not None:
                return configured_guild

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

    async def _ensure_verified_role(self, guild: discord.Guild, member: discord.Member) -> tuple[str, list[str]]:
        """Ensure rejoining verified members also receive the Discord Verified role."""

        verified_role = discord.utils.get(guild.roles, name="Verified")
        if verified_role is None:
            return "Skipped (Verified role does not exist in this server).", []

        if verified_role in member.roles:
            return "No Verified role change was required.", []

        try:
            # Re-add the stable Verified role on join so previously verified members immediately regain baseline access.
            await member.add_roles(verified_role, reason="Habbo automatic role updater verified-role sync on member join")
        except discord.Forbidden:
            return "Failed (bot lacks permission to manage the Verified role).", []
        except discord.HTTPException:
            return "Failed (Discord rejected the Verified role update request).", []

        return "Verified role added.", [verified_role.name]

    async def _assign_roles_to_member_from_profile(
        self,
        guild: discord.Guild,
        member: discord.Member,
        profile: dict,
    ) -> tuple[str, list[str], list[str]]:
        """Synchronize mapped roles to a specific member using a Habbo profile."""

        if not self._is_profile_visible(profile):
            return "Skipped (Habbo profile is hidden; public groups are unavailable until profileVisible is true).", [], []

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

    async def _handle_hidden_profile_audit_state(
        self,
        *,
        guild: discord.Guild,
        member: discord.Member,
        habbo_username: str,
        profile: dict,
    ) -> None:
        """Alert once per hidden-profile period, then reset when the profile becomes visible again."""

        discord_id = str(getattr(member, "id", "")).strip()
        if not discord_id:
            return

        if self._is_profile_visible(profile):
            self.hidden_profile_alert_store.clear_alerted(discord_id)
            return

        if self.hidden_profile_alert_store.has_alerted(discord_id):
            return

        await self._send_hidden_profile_embed(
            guild=guild,
            member=member,
            habbo_username=habbo_username,
        )
        self.hidden_profile_alert_store.mark_alerted(discord_id)

    async def _send_hidden_profile_embed(
        self,
        *,
        guild: discord.Guild,
        member: discord.Member,
        habbo_username: str,
    ) -> None:
        """Post a one-time audit embed when Habbo profile visibility blocks role syncing."""

        channel_id = self.server_config_store.get_audit_channel_id()
        if channel_id is None:
            return

        channel = guild.get_channel(channel_id)
        if channel is None:
            return

        embed = discord.Embed(
            title="Habbo Role Sync Blocked",
            description="This member could not be roled because their Habbo profile is hidden.",
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="User", value=member.mention, inline=False)
        embed.add_field(name="Habbo Username", value=habbo_username, inline=False)
        embed.add_field(
            name="Reason",
            value="`profileVisible` is `false`, so Habbo does not expose the public group data needed for role sync.",
            inline=False,
        )
        embed.add_field(
            name="Alert Policy",
            value="This notice is sent once while the profile is hidden. It will reset after the profile becomes visible again.",
            inline=False,
        )

        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return

    async def _send_error_embed(
        self,
        *,
        guild: discord.Guild,
        member: discord.Member | None,
        habbo_username: str,
        title: str,
        error_text: str,
        context: str,
    ) -> None:
        """Send role-sync errors to the fixed background log channel."""

        channel = guild.get_channel(self.ERROR_LOG_CHANNEL_ID)
        if channel is None:
            channel = self.bot.get_channel(self.ERROR_LOG_CHANNEL_ID)
        if channel is None:
            return

        embed = discord.Embed(
            title=title,
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        user_value = member.mention if member is not None else "System task"
        embed.add_field(name="User", value=user_value, inline=False)
        embed.add_field(name="Habbo Username", value=habbo_username or "unknown", inline=False)
        embed.add_field(name="Context", value=context, inline=False)
        embed.add_field(name="Error", value=error_text, inline=False)

        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return

    @staticmethod
    def _is_profile_visible(profile: dict) -> bool:
        """Normalize Habbo profile visibility values to a boolean."""

        value = profile.get("profileVisible", True)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() == "true"

    @staticmethod
    def _is_rate_limit_error(error: Exception) -> bool:
        """Return True when an exception message indicates Habbo API rate limiting (HTTP 429)."""

        return "429" in str(error)

    def _is_habbo_rate_limited_now(self) -> bool:
        """Return True while the temporary Habbo API cooldown window is still active."""

        if self._habbo_rate_limited_until is None:
            return False
        return datetime.now(timezone.utc) < self._habbo_rate_limited_until

    def _begin_habbo_rate_limit_cooldown(self, *, minutes: int = 15) -> datetime:
        """Set and return the next time when automatic Habbo sync attempts may resume."""

        self._habbo_rate_limited_until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        return self._habbo_rate_limited_until

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

    async def _send_sync_summary_embed(self, *, trigger: str, summary: dict[str, int]) -> None:
        """Post one concise audit-log summary after each full updater processing cycle."""

        guild = self._get_primary_guild()
        if guild is None:
            return

        channel_id = self.server_config_store.get_audit_channel_id()
        if channel_id is None:
            return

        channel = guild.get_channel(channel_id)
        if channel is None:
            return

        embed = discord.Embed(
            title="Habbo Role Updater Cycle Complete",
            description="Finished processing saved verified-user entries.",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Context", value=f"Trigger: {trigger}", inline=False)
        embed.add_field(name="Total Entries", value=str(summary.get("total_entries", 0)), inline=True)
        embed.add_field(name="Updated", value=str(summary.get("updated", 0)), inline=True)
        embed.add_field(name="Skipped", value=str(summary.get("skipped", 0)), inline=True)
        embed.add_field(name="Errors", value=str(summary.get("errors", 0)), inline=True)

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

        # Rejoin verification embeds are required to land in the dedicated staff verification log channel.
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
