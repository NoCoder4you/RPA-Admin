from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord.ext import commands

from habbo_verification_core import ServerConfigStore


class AuditLogCog(commands.Cog):
    """Log moderation-relevant guild events to the configured audit channel."""

    def __init__(self, bot: commands.Bot) -> None:
        # Keep references on the cog for easy mocking in tests and parity with other cogs.
        self.bot = bot
        self.server_config_store = ServerConfigStore()

    @staticmethod
    def _utc_now_iso() -> str:
        """Return an ISO-8601 UTC timestamp used in every audit embed."""

        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _relative_timestamp_markdown() -> str:
        """Return Discord markdown that renders as a relative time in clients."""

        unix_seconds = int(datetime.now(timezone.utc).timestamp())
        return f"<t:{unix_seconds}:R>"

    @staticmethod
    def _full_timestamp_markdown() -> str:
        """Return Discord markdown that renders as a concise absolute timestamp."""

        unix_seconds = int(datetime.now(timezone.utc).timestamp())
        return f"<t:{unix_seconds}:f>"

    async def _find_recent_audit_entry(
        self,
        guild: discord.Guild,
        *,
        action: discord.AuditLogAction,
        target_id: int,
        fallback_target_name: str | None = None,
    ) -> discord.AuditLogEntry | None:
        """Return the freshest audit-log entry matching action + target, if available."""

        # We only scan a handful of recent entries to keep API usage low.
        fallback_entry: discord.AuditLogEntry | None = None
        try:
            async for entry in guild.audit_logs(limit=6, action=action):
                entry_target_id = getattr(entry.target, "id", None)
                if entry_target_id == target_id:
                    return entry

                # Some Discord audit events do not reliably expose the final target ID
                # in the exact shape we expect at event time, so keep the freshest
                # plausible fallback entry to avoid showing "Unknown" too often.
                if fallback_entry is None:
                    fallback_entry = entry

                entry_target_name = getattr(entry.target, "name", None)
                if fallback_target_name and entry_target_name == fallback_target_name:
                    fallback_entry = entry
        except (discord.Forbidden, discord.HTTPException):
            # Missing permissions or temporary API failures should not break the logger.
            return None
        return fallback_entry

    @staticmethod
    def _format_actor(actor: discord.abc.User | None) -> str:
        """Return a readable actor display string for embeds."""

        if actor is None:
            return "Unknown"
        if hasattr(actor, "mention"):
            return f"{actor.mention} (`{actor.id}`)"
        return f"{actor} (`{getattr(actor, 'id', 'unknown')}`)"

    @staticmethod
    def _permission_delta_lines(before: discord.Permissions, after: discord.Permissions, *, limit: int = 12) -> list[str]:
        """Build a compact, human-readable list of changed permission flags."""

        changes: list[str] = []
        for permission_name, old_value in before:
            new_value = getattr(after, permission_name)
            if old_value == new_value:
                continue
            changes.append(f"`{permission_name}`: `{old_value}` ➜ `{new_value}`")
            if len(changes) >= limit:
                break
        return changes

    @staticmethod
    def _format_overwrite_target(target: object) -> str:
        """Return a friendly label for a permission overwrite target."""

        if target is None:
            return "Unknown target"

        target_name = getattr(target, "mention", None) or getattr(target, "name", None) or str(target)
        target_id = getattr(target, "id", None)
        if target_id is None:
            return str(target_name)
        return f"{target_name} (`{target_id}`)"

    def _channel_overwrite_change_lines(
        self,
        before: discord.abc.GuildChannel,
        after: discord.abc.GuildChannel,
        audit_entry: discord.AuditLogEntry | None,
    ) -> list[str]:
        """Summarize permission overwrite changes using audit-log data when available."""

        change_lines: list[str] = []

        # Prefer the audit log because it can tell us which specific overwrite entry changed.
        if audit_entry is not None:
            extra = getattr(audit_entry, "extra", None)
            overwrite_target = getattr(extra, "overwrite", None)
            overwrite_target_type = getattr(extra, "overwrite_type", None)

            before_overwrite = None
            after_overwrite = None
            if overwrite_target is not None:
                before_overwrite = before.overwrites.get(overwrite_target)
                after_overwrite = after.overwrites.get(overwrite_target)

            if overwrite_target is not None and (before_overwrite is not None or after_overwrite is not None):
                target_label = self._format_overwrite_target(overwrite_target)
                if overwrite_target_type is not None:
                    target_label = f"{target_label} [{overwrite_target_type}]"

                before_allow = getattr(before_overwrite, "pair", lambda: (discord.Permissions.none(), discord.Permissions.none()))()[0]
                before_deny = getattr(before_overwrite, "pair", lambda: (discord.Permissions.none(), discord.Permissions.none()))()[1]
                after_allow = getattr(after_overwrite, "pair", lambda: (discord.Permissions.none(), discord.Permissions.none()))()[0]
                after_deny = getattr(after_overwrite, "pair", lambda: (discord.Permissions.none(), discord.Permissions.none()))()[1]

                allow_changes = self._permission_delta_lines(before_allow, after_allow, limit=6)
                deny_changes = self._permission_delta_lines(before_deny, after_deny, limit=6)

                change_lines.append(f"Target: {target_label}")
                if allow_changes:
                    change_lines.append("Allowed changes:")
                    change_lines.extend(f"• {line}" for line in allow_changes)
                if deny_changes:
                    change_lines.append("Denied changes:")
                    change_lines.extend(f"• {line}" for line in deny_changes)

        if change_lines:
            return change_lines

        # Fallback when the audit log does not include granular overwrite info.
        before_targets = {self._format_overwrite_target(target) for target in before.overwrites.keys()}
        after_targets = {self._format_overwrite_target(target) for target in after.overwrites.keys()}
        added_targets = sorted(after_targets - before_targets)
        removed_targets = sorted(before_targets - after_targets)

        if added_targets:
            change_lines.append(f"Added targets: {', '.join(added_targets[:5])}")
        if removed_targets:
            change_lines.append(f"Removed targets: {', '.join(removed_targets[:5])}")

        if not change_lines:
            change_lines.append("Overwrite details were changed, but Discord did not expose the granular diff.")

        return change_lines

    async def _send_audit_embed(
        self,
        guild: discord.Guild,
        *,
        title: str,
        description: str,
        color: discord.Color = discord.Color.blurple(),
        fields: list[tuple[str, str, bool]] | None = None,
    ) -> None:
        """Safely deliver an audit embed to the configured audit channel, if available."""

        audit_channel_id = self.server_config_store.get_audit_channel_id()
        if audit_channel_id is None:
            return

        audit_channel = guild.get_channel(audit_channel_id)
        if audit_channel is None:
            return

        embed = discord.Embed(title=title, description=description, color=color)
        # Combine absolute and relative Discord timestamps for a cleaner presentation in chat.
        embed.add_field(
            name="When",
            value=f"{self._full_timestamp_markdown()} • {self._relative_timestamp_markdown()}",
            inline=False,
        )

        for name, value, inline in fields or []:
            embed.add_field(name=name, value=value, inline=inline)

        await audit_channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Log member joins so staff have a chronological user-entry history."""

        await self._send_audit_embed(
            member.guild,
            title="Member Joined",
            description=f"{member.mention} (`{member.id}`) joined the server.",
            color=discord.Color.green(),
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """Log member leaves for basic retention and moderation visibility."""

        await self._send_audit_embed(
            member.guild,
            title="Member Left",
            description=f"{member.mention} (`{member.id}`) left the server.",
            color=discord.Color.orange(),
        )

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.abc.User) -> None:
        """Log member bans explicitly so punitive actions are easier to review."""

        audit_entry = await self._find_recent_audit_entry(
            guild,
            action=discord.AuditLogAction.ban,
            target_id=user.id,
            fallback_target_name=str(user),
        )
        await self._send_audit_embed(
            guild,
            title="Member Banned",
            description=f"{user.mention if hasattr(user, 'mention') else user} (`{user.id}`) was banned.",
            fields=[("By", self._format_actor(audit_entry.user if audit_entry else None), False)],
            color=discord.Color.red(),
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.abc.User) -> None:
        """Log unbans to keep moderation reversals visible in the audit trail."""

        audit_entry = await self._find_recent_audit_entry(
            guild,
            action=discord.AuditLogAction.unban,
            target_id=user.id,
            fallback_target_name=str(user),
        )
        await self._send_audit_embed(
            guild,
            title="Member Unbanned",
            description=f"{user.mention if hasattr(user, 'mention') else user} (`{user.id}`) was unbanned.",
            fields=[("By", self._format_actor(audit_entry.user if audit_entry else None), False)],
            color=discord.Color.green(),
        )

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        """Log newly created channels so structural changes are visible to admins."""

        audit_entry = await self._find_recent_audit_entry(
            channel.guild,
            action=discord.AuditLogAction.channel_create,
            target_id=channel.id,
            fallback_target_name=getattr(channel, "name", None),
        )
        await self._send_audit_embed(
            channel.guild,
            title="Channel Created",
            description=f"{channel.mention if hasattr(channel, 'mention') else channel.name} (`{channel.id}`) was created.",
            fields=[
                ("Type", str(channel.type), True),
                ("By", self._format_actor(audit_entry.user if audit_entry else None), False),
            ],
            color=discord.Color.green(),
        )

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        """Log channel deletions to preserve evidence of destructive changes."""

        audit_entry = await self._find_recent_audit_entry(
            channel.guild,
            action=discord.AuditLogAction.channel_delete,
            target_id=channel.id,
            fallback_target_name=getattr(channel, "name", None),
        )
        await self._send_audit_embed(
            channel.guild,
            title="Channel Deleted",
            description=f"{channel.name} (`{channel.id}`) was deleted.",
            fields=[
                ("Type", str(channel.type), True),
                ("By", self._format_actor(audit_entry.user if audit_entry else None), False),
            ],
            color=discord.Color.red(),
        )

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        """Log role creation so privilege model changes are historically discoverable."""

        audit_entry = await self._find_recent_audit_entry(
            role.guild,
            action=discord.AuditLogAction.role_create,
            target_id=role.id,
            fallback_target_name=role.name,
        )
        await self._send_audit_embed(
            role.guild,
            title="Role Created",
            description=f"Role **{role.name}** (`{role.id}`) was created.",
            fields=[("By", self._format_actor(audit_entry.user if audit_entry else None), False)],
            color=discord.Color.green(),
        )

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        """Log role deletion for forensic visibility around permission restructuring."""

        audit_entry = await self._find_recent_audit_entry(
            role.guild,
            action=discord.AuditLogAction.role_delete,
            target_id=role.id,
            fallback_target_name=role.name,
        )
        await self._send_audit_embed(
            role.guild,
            title="Role Deleted",
            description=f"Role **{role.name}** (`{role.id}`) was deleted.",
            fields=[("By", self._format_actor(audit_entry.user if audit_entry else None), False)],
            color=discord.Color.red(),
        )

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self,
        before: discord.abc.GuildChannel,
        after: discord.abc.GuildChannel,
    ) -> None:
        """Log channel permission overwrite changes for traceable access-control edits."""

        if before.overwrites == after.overwrites:
            return

        audit_entry = await self._find_recent_audit_entry(
            after.guild,
            action=discord.AuditLogAction.channel_update,
            target_id=after.id,
            fallback_target_name=getattr(after, "name", None),
        )
        overwrite_change_lines = self._channel_overwrite_change_lines(before, after, audit_entry)

        await self._send_audit_embed(
            after.guild,
            title="Channel Permissions Updated",
            description=(
                f"Permission overwrites changed for "
                f"{after.mention if hasattr(after, 'mention') else after.name} (`{after.id}`)."
            ),
            fields=[
                ("By", self._format_actor(audit_entry.user if audit_entry else None), False),
                ("Changes", "\n".join(overwrite_change_lines)[:1024], False),
            ],
            color=discord.Color.gold(),
        )

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        """Log role permission updates so changes in privilege are auditable."""

        if before.permissions == after.permissions:
            return

        audit_entry = await self._find_recent_audit_entry(
            after.guild,
            action=discord.AuditLogAction.role_update,
            target_id=after.id,
            fallback_target_name=after.name,
        )
        permission_deltas = self._permission_delta_lines(before.permissions, after.permissions)
        change_summary = "\n".join(permission_deltas) if permission_deltas else "No individual permission deltas resolved."

        await self._send_audit_embed(
            after.guild,
            title="Role Permissions Updated",
            description=f"Permissions changed for role **{after.name}** (`{after.id}`).",
            fields=[
                ("By", self._format_actor(audit_entry.user if audit_entry else None), False),
                ("Before", str(before.permissions.value), True),
                ("After", str(after.permissions.value), True),
                ("Changed Flags", change_summary[:1024], False),
            ],
            color=discord.Color.gold(),
        )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        """Log nickname and role membership changes that impact moderation context."""

        changed_fields: list[tuple[str, str, bool]] = []

        if before.nick != after.nick:
            changed_fields.append(("Nickname", f"`{before.nick or 'None'}` ➜ `{after.nick or 'None'}`", False))

        before_role_ids = {role.id for role in before.roles}
        after_role_ids = {role.id for role in after.roles}
        if before_role_ids != after_role_ids:
            added = [f"<@&{role.id}>" for role in after.roles if role.id not in before_role_ids]
            removed = [f"<@&{role.id}>" for role in before.roles if role.id not in after_role_ids]

            if added:
                changed_fields.append(("Roles Added", ", ".join(added), False))
            if removed:
                changed_fields.append(("Roles Removed", ", ".join(removed), False))

        if not changed_fields:
            return

        member_role_update_action = getattr(discord.AuditLogAction, "member_role_update", discord.AuditLogAction.member_update)
        audit_entry = await self._find_recent_audit_entry(
            after.guild,
            action=member_role_update_action,
            target_id=after.id,
            fallback_target_name=str(after),
        )
        changed_fields.insert(0, ("By", self._format_actor(audit_entry.user if audit_entry else None), False))

        await self._send_audit_embed(
            after.guild,
            title="Member Updated",
            description=f"Profile/role changes detected for {after.mention} (`{after.id}`).",
            fields=changed_fields,
            color=discord.Color.gold(),
        )

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild) -> None:
        """Log core guild setting changes (name/description/afk) for server-change auditing."""

        changed_fields: list[tuple[str, str, bool]] = []

        if before.name != after.name:
            changed_fields.append(("Server Name", f"`{before.name}` ➜ `{after.name}`", False))

        if before.description != after.description:
            changed_fields.append(("Description", f"`{before.description or 'None'}` ➜ `{after.description or 'None'}`", False))

        if getattr(before, "afk_timeout", None) != getattr(after, "afk_timeout", None):
            changed_fields.append(("AFK Timeout", f"`{before.afk_timeout}` ➜ `{after.afk_timeout}`", True))

        if getattr(before, "afk_channel", None) != getattr(after, "afk_channel", None):
            before_afk_name = before.afk_channel.name if getattr(before, "afk_channel", None) else "None"
            after_afk_name = after.afk_channel.name if getattr(after, "afk_channel", None) else "None"
            changed_fields.append(("AFK Channel", f"`{before_afk_name}` ➜ `{after_afk_name}`", False))

        if not changed_fields:
            return

        audit_entry = await self._find_recent_audit_entry(
            after,
            action=discord.AuditLogAction.guild_update,
            target_id=after.id,
            fallback_target_name=after.name,
        )
        changed_fields.insert(0, ("By", self._format_actor(audit_entry.user if audit_entry else None), False))

        await self._send_audit_embed(
            after,
            title="Server Settings Updated",
            description=f"Guild settings were updated for **{after.name}** (`{after.id}`).",
            fields=changed_fields,
            color=discord.Color.gold(),
        )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Log server mute/deafen state changes from voice-state updates."""

        changed_fields: list[tuple[str, str, bool]] = []

        if before.mute != after.mute:
            changed_fields.append(("Server Mute", f"{before.mute} ➜ {after.mute}", True))

        if before.deaf != after.deaf:
            changed_fields.append(("Server Deaf", f"{before.deaf} ➜ {after.deaf}", True))

        if not changed_fields:
            return

        audit_entry = await self._find_recent_audit_entry(
            member.guild,
            action=discord.AuditLogAction.member_update,
            target_id=member.id,
            fallback_target_name=str(member),
        )
        changed_fields.insert(0, ("By", self._format_actor(audit_entry.user if audit_entry else None), False))

        await self._send_audit_embed(
            member.guild,
            title="Voice Moderation State Updated",
            description=f"Voice moderation state changed for {member.mention} (`{member.id}`).",
            fields=changed_fields,
            color=discord.Color.gold(),
        )


async def setup(bot: commands.Bot) -> None:
    """discord.py entrypoint used by extension discovery in bot.py."""

    await bot.add_cog(AuditLogCog(bot))
