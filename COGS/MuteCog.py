from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from common_paths import json_file
from habbo_verification_core import ServerConfigStore


_DURATION_PATTERN = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.IGNORECASE)
_MAX_TIMEOUT = timedelta(days=28)
# Store timeout records alongside the project's other JSON persistence files.
_MUTE_LOG_PATH = json_file("mute_timeouts.json")
_MUTED_ROLE_NAME = "Muted"


class MuteCog(commands.Cog):
    """Moderation cog containing a staff-only mute/timeout command."""

    def __init__(self, bot: commands.Bot) -> None:
        # Keep a bot reference for consistency with the project's other cogs.
        self.bot = bot
        # Persist timeout metadata so moderation actions are auditable outside Discord.
        self.mute_log_path = _MUTE_LOG_PATH
        # Reuse the project's single-server audit channel configuration source.
        self.server_config_store = ServerConfigStore()
        # Poll once per minute so members are automatically unmuted when timeout expires.
        self.unmute_expired_members.start()

    def cog_unload(self) -> None:
        """Stop background jobs when this cog is unloaded."""

        self.unmute_expired_members.cancel()

    @staticmethod
    def _is_member_currently_timed_out(member: discord.Member, now_utc: datetime) -> bool:
        """Return True when Discord still considers the member timed out."""

        is_timed_out_method = getattr(member, "is_timed_out", None)
        if callable(is_timed_out_method):
            return bool(is_timed_out_method())

        timeout_until = getattr(member, "timed_out_until", None)
        if timeout_until is None:
            timeout_until = getattr(member, "communication_disabled_until", None)

        if timeout_until is None:
            return False

        if timeout_until.tzinfo is None:
            timeout_until = timeout_until.replace(tzinfo=timezone.utc)

        return timeout_until > now_utc

    async def _remove_expired_mutes_from_guild(self, guild: discord.Guild) -> None:
        """Remove the muted role from members whose timeout has already expired."""

        configured_role_id = self.server_config_store.get_muted_role_id()
        muted_role = guild.get_role(configured_role_id) if configured_role_id is not None else None
        if muted_role is None:
            muted_role = discord.utils.get(guild.roles, name=_MUTED_ROLE_NAME)

        if muted_role is None:
            return

        now_utc = datetime.now(timezone.utc)
        for member in guild.members:
            if muted_role not in member.roles:
                continue

            if self._is_member_currently_timed_out(member, now_utc):
                continue

            try:
                # Remove only stale mute roles so active moderation actions are left untouched.
                await member.remove_roles(muted_role, reason="Automatic unmute after timeout expiration")
                # Send follow-up notifications so users and staff know the mute lifecycle completed.
                await self._send_auto_unmute_notifications(guild, member)
            except (discord.Forbidden, discord.HTTPException):
                logging.exception("Failed to auto-unmute member %s in guild %s", member.id, guild.id)

    @tasks.loop(minutes=1)
    async def unmute_expired_members(self) -> None:
        """Periodically scan guilds and remove stale muted roles."""

        for guild in self.bot.guilds:
            await self._remove_expired_mutes_from_guild(guild)

    @unmute_expired_members.before_loop
    async def before_unmute_expired_members(self) -> None:
        """Ensure the Discord gateway is ready before the periodic unmute task runs."""

        wait_until_ready = getattr(self.bot, "wait_until_ready", None)
        if callable(wait_until_ready):
            maybe_coro = wait_until_ready()
            if hasattr(maybe_coro, "__await__"):
                await maybe_coro

    def _append_mute_record(
        self,
        *,
        guild_id: int,
        member_id: int,
        moderator_id: int,
        reason: str,
        requested_length: str,
        duration_seconds: int,
        started_at: datetime,
        ends_at: datetime,
    ) -> None:
        """Append a timeout record to JSON storage, creating the file if required."""

        # Keep timestamps in ISO 8601 UTC format for machine parsing and readability.
        record = {
            "guild_id": str(guild_id),
            "member_id": str(member_id),
            "moderator_id": str(moderator_id),
            "reason": reason,
            "requested_length": requested_length,
            "duration_seconds": duration_seconds,
            "start_time": started_at.isoformat(),
            "end_time": ends_at.isoformat(),
        }

        self.mute_log_path.parent.mkdir(parents=True, exist_ok=True)

        records: list[dict[str, str | int]] = []
        if self.mute_log_path.exists():
            try:
                with self.mute_log_path.open("r", encoding="utf-8") as existing_file:
                    existing_data = json.load(existing_file)
                if isinstance(existing_data, list):
                    records = existing_data
            except (json.JSONDecodeError, OSError):
                # If the file is malformed or inaccessible, reset to a fresh list.
                records = []

        records.append(record)

        with self.mute_log_path.open("w", encoding="utf-8") as output_file:
            json.dump(records, output_file, indent=2)

    @staticmethod
    def _discord_timestamp_display(moment: datetime) -> str:
        """Return Discord timestamp markdown in absolute + relative formats."""

        # Discord resolves `<t:...>` in each viewer's local timezone, which makes
        # moderation logs much easier for staff to read than raw ISO-8601 strings.
        unix_seconds = int(moment.timestamp())
        return f"<t:{unix_seconds}:F> (<t:{unix_seconds}:R>)"

    async def _ensure_muted_role(self, guild: discord.Guild) -> discord.Role:
        """Get/create the configured muted role and enforce channel restrictions."""

        muted_role: discord.Role | None = None

        # First prefer role ID persisted in serverconfig.json for stable future lookups.
        configured_role_id = self.server_config_store.get_muted_role_id()
        if configured_role_id is not None:
            muted_role = guild.get_role(configured_role_id)

        # Fallback by role name for migration compatibility with older deployments.
        if muted_role is None:
            muted_role = discord.utils.get(guild.roles, name=_MUTED_ROLE_NAME)

        if muted_role is None:
            # Create a baseline role first; per-channel overwrite locks are applied below.
            muted_role = await guild.create_role(
                name=_MUTED_ROLE_NAME,
                reason="Created automatically by /mute command",
            )

        # Save the resolved role ID so future mutes use a consistent role reference.
        self.server_config_store.set_muted_role_id(muted_role.id)

        # Apply explicit denies in every channel so muted members cannot chat or speak.
        for channel in guild.channels:
            overwrite = channel.overwrites_for(muted_role)

            if isinstance(channel, discord.TextChannel):
                overwrite.send_messages = False
            elif isinstance(channel, discord.VoiceChannel):
                overwrite.speak = False
            else:
                # For other channel types, set both when supported by Discord.
                overwrite.send_messages = False
                overwrite.speak = False

            await channel.set_permissions(
                muted_role,
                overwrite=overwrite,
                reason="Enforced muted role restrictions",
            )

        return muted_role

    async def _send_mute_audit_embed(
        self,
        *,
        interaction: discord.Interaction,
        mention: discord.Member,
        reason: str,
        requested_length: str,
        started_at: datetime,
        ends_at: datetime,
        duration_seconds: int,
    ) -> None:
        """Post a moderation embed to the configured audit log channel if available."""

        if interaction.guild is None:
            return

        channel_id = self.server_config_store.get_audit_channel_id()
        if channel_id is None:
            return

        channel = interaction.guild.get_channel(channel_id)
        if channel is None:
            return

        embed = discord.Embed(
            title="Member Muted",
            description=" ",
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc),
        )

        # Include direct mentions to help moderators quickly identify involved users.
        embed.add_field(name="Member", value=mention.mention, inline=False)
        embed.add_field(name="Moderator", value=interaction.user.mention, inline=False)
        embed.add_field(name="Requested Length", value=f"`{requested_length}`", inline=True)
        embed.add_field(name="Duration Seconds", value=str(duration_seconds), inline=True)
        embed.add_field(name="Start Time", value=self._discord_timestamp_display(started_at), inline=False)
        embed.add_field(name="End Time", value=self._discord_timestamp_display(ends_at), inline=False)
        embed.add_field(name="Reason", value=reason, inline=False)

        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            # Avoid failing the mute command when audit logging cannot be delivered.
            return

    async def _send_mute_direct_message(
        self,
        *,
        member: discord.Member,
        moderator: discord.abc.User,
        requested_length: str,
        ends_at: datetime,
        reason: str,
    ) -> None:
        """Send a direct-message embed with mute details to the muted member."""

        # Use the same human-friendly timestamp format as the moderation audit logs.
        expiration_display = self._discord_timestamp_display(ends_at)

        embed = discord.Embed(
            title="You Have Been Muted",
            description="You were muted by a moderator.",
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc),
        )
        # Include the exact moderation details requested for transparency.
        embed.add_field(name="Who Muted", value=getattr(moderator, "mention", str(moderator)), inline=False)
        embed.add_field(name="How Long", value=f"`{requested_length}`", inline=True)
        embed.add_field(name="Expiration Time", value=expiration_display, inline=False)
        embed.add_field(name="Why", value=reason, inline=False)

        try:
            await member.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException, AttributeError):
            # DMs can fail when closed; moderation action should still complete.
            return

    async def _send_auto_unmute_notifications(self, guild: discord.Guild, member: discord.Member) -> None:
        member_embed = discord.Embed(
            title="You Have Been Unmuted",
            description="Your mute has expired and the Muted role was removed automatically.",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )

        try:
            await member.send(embed=member_embed)
        except (discord.Forbidden, discord.HTTPException, AttributeError):
            # Skip DM failures silently so scheduled cleanup is resilient.
            pass

        channel_id = self.server_config_store.get_audit_channel_id()
        if channel_id is None:
            return

        audit_channel = guild.get_channel(channel_id)
        if audit_channel is None:
            return

        audit_embed = discord.Embed(
            title="Member Unmuted",
            description="A member was automatically unmuted after timeout expiration.",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
        audit_embed.add_field(name="Member", value=getattr(member, "mention", str(member)), inline=False)

        try:
            await audit_channel.send(embed=audit_embed)
        except (discord.Forbidden, discord.HTTPException):
            return

    @staticmethod
    def _parse_timeout_length(lengthoftime: str) -> timedelta | None:
        """Parse timeout text like `10m`, `2h`, `7d`, or `1w` into a timedelta."""

        match = _DURATION_PATTERN.match(lengthoftime)
        if not match:
            return None

        amount = int(match.group(1))
        unit = match.group(2).lower()

        # Translate compact user input into a specific moderation duration.
        multiplier_seconds = {
            "s": 1,
            "m": 60,
            "h": 60 * 60,
            "d": 60 * 60 * 24,
            "w": 60 * 60 * 24 * 7,
        }
        return timedelta(seconds=amount * multiplier_seconds[unit])

    @app_commands.command(name="mute", description="Temporarily mute (timeout) a member with a reason.")
    @app_commands.describe(
        mention="The member to mute",
        lengthoftime="How long to mute them (examples: 10m, 2h, 3d, 1w)",
        reason="Why this member is being muted",
    )
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True, manage_roles=True, manage_channels=True)
    async def mute(
        self,
        interaction: discord.Interaction,
        mention: discord.Member,
        lengthoftime: str,
        reason: str,
    ) -> None:
        """Timeout a guild member for a parsed duration when permission checks pass."""

        # This command only makes sense where guild member moderation is possible.
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        duration = self._parse_timeout_length(lengthoftime)
        if duration is None:
            await interaction.response.send_message(
                "Invalid `lengthoftime`. Use formats like `10m`, `2h`, `3d`, or `1w`.",
                ephemeral=True,
            )
            return

        if duration <= timedelta(seconds=0):
            await interaction.response.send_message("Mute duration must be greater than zero.", ephemeral=True)
            return

        # Discord only allows timeouts up to 28 days; fail early with guidance.
        if duration > _MAX_TIMEOUT:
            await interaction.response.send_message(
                "Mute duration cannot exceed 28 days.",
                ephemeral=True,
            )
            return

        if mention.id == interaction.user.id:
            await interaction.response.send_message("You cannot mute yourself.", ephemeral=True)
            return

        if mention.id == interaction.guild.owner_id:
            await interaction.response.send_message("I cannot mute the server owner.", ephemeral=True)
            return

        # Enforce role hierarchy to avoid attempting invalid moderation actions.
        if hasattr(interaction.user, "top_role") and mention.top_role >= interaction.user.top_role:
            await interaction.response.send_message(
                "You can only mute members with a lower top role than yours.",
                ephemeral=True,
            )
            return

        bot_member = interaction.guild.me
        if bot_member is not None and mention.top_role >= bot_member.top_role:
            await interaction.response.send_message(
                "I cannot mute that member because their top role is higher than or equal to mine.",
                ephemeral=True,
            )
            return

        # Acknowledge the interaction immediately so Discord does not mark `/mute` as timed out
        # while role creation/permission updates and API calls are still running.
        await interaction.response.defer(ephemeral=True)

        try:
            muted_role = await self._ensure_muted_role(interaction.guild)
            # Apply the role so text/voice deny overwrites take effect immediately.
            await mention.add_roles(muted_role, reason=f"{interaction.user} - {reason}")
        except discord.Forbidden:
            await interaction.followup.send(
                "Mute failed: I do not have permission to create/apply the Muted role.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.followup.send(
                "Mute failed while configuring the Muted role. Please try again.",
                ephemeral=True,
            )
            return

        timeout_started_at = datetime.now(timezone.utc)
        timeout_until = timeout_started_at + duration

        try:
            await mention.timeout(timeout_until, reason=f"{interaction.user} - {reason}")
        except discord.Forbidden:
            await interaction.followup.send(
                "Mute failed: I do not have permission to mute that member.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.followup.send(
                "Mute failed due to a Discord API error. Please try again.",
                ephemeral=True,
            )
            return

        duration_seconds = int(duration.total_seconds())

        self._append_mute_record(
            guild_id=interaction.guild.id,
            member_id=mention.id,
            moderator_id=interaction.user.id,
            reason=reason,
            requested_length=lengthoftime,
            duration_seconds=duration_seconds,
            started_at=timeout_started_at,
            ends_at=timeout_until,
        )

        # Attempt to notify the muted user directly with clear moderation details.
        await self._send_mute_direct_message(
            member=mention,
            moderator=interaction.user,
            requested_length=lengthoftime,
            ends_at=timeout_until,
            reason=reason,
        )

        await self._send_mute_audit_embed(
            interaction=interaction,
            mention=mention,
            reason=reason,
            requested_length=lengthoftime,
            started_at=timeout_started_at,
            ends_at=timeout_until,
            duration_seconds=duration_seconds,
        )

        await interaction.followup.send(
            f"🔇 Muted {mention.mention} for `{lengthoftime}`. Reason: {reason}",
            ephemeral=True,
        )

    @mute.error
    async def mute_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        """Return clear permission guidance for known slash-command check failures."""

        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the **Moderate Members** permission to use `/mute`.",
                ephemeral=True,
            )
            return

        if isinstance(error, app_commands.BotMissingPermissions):
            await interaction.response.send_message(
                "I need **Moderate Members**, **Manage Roles**, and **Manage Channels** to use `/mute`.",
                ephemeral=True,
            )
            return

        raise error


async def setup(bot: commands.Bot) -> None:
    """Discord extension entrypoint for loading this cog."""

    await bot.add_cog(MuteCog(bot))
