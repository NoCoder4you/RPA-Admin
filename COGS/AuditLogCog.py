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

    async def _find_recent_audit_entry_from_actions(
        self,
        guild: discord.Guild,
        *,
        actions: list[discord.AuditLogAction],
        target_id: int,
        fallback_target_name: str | None = None,
    ) -> discord.AuditLogEntry | None:
        """Return the first matching recent audit entry from a prioritized action list."""

        for action in actions:
            entry = await self._find_recent_audit_entry(
                guild,
                action=action,
                target_id=target_id,
                fallback_target_name=fallback_target_name,
            )
            if entry is not None:
                return entry
        return None

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
    def _permission_names_by_transition(
        before: discord.Permissions,
        after: discord.Permissions,
        *,
        enabled_to: bool,
        limit: int = 8,
    ) -> list[str]:
        """Return permission names that changed to the requested boolean state."""

        changed_names: list[str] = []
        for permission_name, old_value in before:
            new_value = getattr(after, permission_name)
            if old_value == new_value or new_value is not enabled_to:
                continue
            changed_names.append(permission_name)
            if len(changed_names) >= limit:
                break
        return changed_names

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

    def _resolve_overwrite_target_label(self, audit_entry: discord.AuditLogEntry | None) -> str | None:
        """Best-effort resolution of the role/member affected by an overwrite audit entry."""

        if audit_entry is None:
            return None

        extra = getattr(audit_entry, "extra", None)
        overwrite_target_type = getattr(extra, "overwrite_type", None)

        def format_from_name_and_id(name: object | None, target_id: object | None) -> str | None:
            normalized_name = str(name).strip() if name not in {None, ""} else None
            normalized_id = None
            try:
                normalized_id = int(target_id) if target_id is not None else None
            except (TypeError, ValueError):
                normalized_id = None

            if normalized_name is None and normalized_id is None:
                return None

            if overwrite_target_type == "role" and normalized_id is not None:
                mention = f"<@&{normalized_id}>"
            elif overwrite_target_type in {"member", "user"} and normalized_id is not None:
                mention = f"<@{normalized_id}>"
            else:
                mention = normalized_name or str(normalized_id)

            label_core = mention
            if normalized_name and normalized_name not in mention:
                label_core = f"{normalized_name} / {mention}"
            if normalized_id is not None:
                label_core = f"{label_core} (`{normalized_id}`)"
            if overwrite_target_type is not None:
                return f"{label_core} [{overwrite_target_type}]"
            return label_core

        preferred_extra_keys = ("overwrite", "role", "member", "user", "target")
        for key in preferred_extra_keys:
            candidate = getattr(extra, key, None)
            if candidate is None:
                continue
            label = self._format_overwrite_target(candidate)
            if overwrite_target_type is not None:
                return f"{label} [{overwrite_target_type}]"
            return label

        if extra is not None and hasattr(extra, "__dict__"):
            for key, candidate in vars(extra).items():
                if key in {"channel", "overwrite_type"} or candidate is None:
                    continue
                if hasattr(candidate, "id") or hasattr(candidate, "name") or hasattr(candidate, "mention"):
                    label = self._format_overwrite_target(candidate)
                    if overwrite_target_type is not None:
                        return f"{label} [{overwrite_target_type}]"
                    return label

            primitive_name_keys = ("role_name", "member_name", "user_name", "name")
            primitive_id_keys = ("role_id", "member_id", "user_id", "id", "overwrite_id")
            primitive_name = None
            primitive_id = None

            for key in primitive_name_keys:
                candidate = getattr(extra, key, None)
                if candidate not in {None, ""}:
                    primitive_name = candidate
                    break

            for key in primitive_id_keys:
                candidate = getattr(extra, key, None)
                if candidate is not None:
                    primitive_id = candidate
                    break

            primitive_label = format_from_name_and_id(primitive_name, primitive_id)
            if primitive_label is not None:
                return primitive_label

        raw_changes = getattr(audit_entry, "changes", None)
        iterable_changes = getattr(raw_changes, "__iter__", None)
        if callable(iterable_changes):
            for change in raw_changes:
                if getattr(change, "key", None) not in {"role", "member", "user", "overwrite"}:
                    continue
                after_value = getattr(change, "after", getattr(change, "new", None))
                before_value = getattr(change, "before", getattr(change, "old", None))
                candidate = after_value or before_value
                if candidate is None:
                    continue
                if hasattr(candidate, "id") or hasattr(candidate, "name") or hasattr(candidate, "mention"):
                    label = self._format_overwrite_target(candidate)
                    if overwrite_target_type is not None:
                        return f"{label} [{overwrite_target_type}]"
                    return label

                if getattr(change, "key", None) in {"role", "member", "user", "overwrite"}:
                    primitive_label = format_from_name_and_id(candidate, None)
                    if primitive_label is not None:
                        return primitive_label

        return None

    @staticmethod
    def _permission_symbol(enabled_state: bool | None) -> str:
        """Return the requested symbol for allowed, neutral, and denied permission states."""

        if enabled_state is True:
            return "✅"
        if enabled_state is False:
            return "❌"
        return "⬜"

    def _resolve_changed_overwrite_target_label(
        self,
        before: discord.abc.GuildChannel,
        after: discord.abc.GuildChannel,
    ) -> str | None:
        """Infer the changed overwrite target from the channel snapshots when audit logs are sparse."""

        before_targets = getattr(before, "overwrites", {}) or {}
        after_targets = getattr(after, "overwrites", {}) or {}

        # Compare overwrite keys directly first because Discord normally reuses the
        # same member/role objects in cache, which makes this the most reliable path.
        before_target_ids = {getattr(target, "id", None): target for target in before_targets}
        after_target_ids = {getattr(target, "id", None): target for target in after_targets}

        changed_target_ids: list[int | None] = []
        all_target_ids = set(before_target_ids) | set(after_target_ids)
        for target_id in all_target_ids:
            before_target = before_target_ids.get(target_id)
            after_target = after_target_ids.get(target_id)
            before_overwrite = before_targets.get(before_target) if before_target is not None else None
            after_overwrite = after_targets.get(after_target) if after_target is not None else None
            if before_overwrite != after_overwrite:
                changed_target_ids.append(target_id)

        if len(changed_target_ids) == 1:
            target_id = changed_target_ids[0]
            candidate = after_target_ids.get(target_id) or before_target_ids.get(target_id)
            return self._format_overwrite_target(candidate) if candidate is not None else None

        return None

    def _channel_overwrite_change_lines(
        self,
        audit_entry: discord.AuditLogEntry | None,
    ) -> tuple[str | None, list[str]]:
        """Summarize permission overwrite changes using only audit-log data."""

        affected_target: str | None = None
        change_lines: list[str] = []

        if audit_entry is None:
            return None, ["Discord audit log entry was not available for this overwrite change."]

        extra = getattr(audit_entry, "extra", None)
        overwrite_target_type = getattr(extra, "overwrite_type", None)
        affected_target = self._resolve_overwrite_target_label(audit_entry)

        # First prefer the audit-log entry's explicit change list because that is the
        # most direct representation of what Discord says changed.
        raw_changes = getattr(audit_entry, "changes", None)
        iterable_changes = getattr(raw_changes, "__iter__", None)
        if callable(iterable_changes):
            for change in raw_changes:
                change_key = getattr(change, "key", None) or getattr(change, "attribute", None)
                before_value = getattr(change, "before", getattr(change, "old", None))
                after_value = getattr(change, "after", getattr(change, "new", None))

                if change_key in {"allow", "deny"}:
                    before_permissions = before_value or discord.Permissions.none()
                    after_permissions = after_value or discord.Permissions.none()
                    permission_changes = self._permission_delta_lines(
                        before_permissions,
                        after_permissions,
                        limit=6,
                    )
                    granted_names = self._permission_names_by_transition(before_permissions, after_permissions, enabled_to=True)
                    removed_names = self._permission_names_by_transition(before_permissions, after_permissions, enabled_to=False)

                    if granted_names:
                        symbol = self._permission_symbol(change_key == "allow")
                        change_lines.extend(f"{symbol} `{name}`" for name in granted_names)
                    if removed_names:
                        neutral_symbol = self._permission_symbol(None)
                        change_lines.extend(f"{neutral_symbol} `{name}`" for name in removed_names)
                    if permission_changes and not granted_names and not removed_names:
                        fallback_label = "Allowed changes:" if change_key == "allow" else "Denied changes:"
                        change_lines.append(fallback_label)
                        change_lines.extend(f"• {line}" for line in permission_changes)
                elif before_value != after_value:
                    change_lines.append(f"`{change_key}`: `{before_value}` ➜ `{after_value}`")

        if change_lines:
            return affected_target, change_lines

        before_state = getattr(audit_entry, "before", None)
        after_state = getattr(audit_entry, "after", None)
        if before_state is not None and after_state is not None:
            before_allow = getattr(before_state, "allow", None) or discord.Permissions.none()
            before_deny = getattr(before_state, "deny", None) or discord.Permissions.none()
            after_allow = getattr(after_state, "allow", None) or discord.Permissions.none()
            after_deny = getattr(after_state, "deny", None) or discord.Permissions.none()

            allow_changes = self._permission_delta_lines(before_allow, after_allow, limit=6)
            deny_changes = self._permission_delta_lines(before_deny, after_deny, limit=6)
            allowed_names = self._permission_names_by_transition(before_allow, after_allow, enabled_to=True)
            removed_allowed_names = self._permission_names_by_transition(before_allow, after_allow, enabled_to=False)
            denied_names = self._permission_names_by_transition(before_deny, after_deny, enabled_to=True)
            removed_denied_names = self._permission_names_by_transition(before_deny, after_deny, enabled_to=False)

            if allowed_names:
                change_lines.extend(f"{self._permission_symbol(True)} `{name}`" for name in allowed_names)
            if removed_allowed_names:
                change_lines.extend(f"{self._permission_symbol(None)} `{name}`" for name in removed_allowed_names)
            if denied_names:
                change_lines.extend(f"{self._permission_symbol(False)} `{name}`" for name in denied_names)
            if removed_denied_names:
                change_lines.extend(f"{self._permission_symbol(None)} `{name}`" for name in removed_denied_names)

            if allow_changes and not allowed_names and not removed_allowed_names:
                change_lines.append("Allowed changes:")
                change_lines.extend(f"• {line}" for line in allow_changes)
            if deny_changes and not denied_names and not removed_denied_names:
                change_lines.append("Denied changes:")
                change_lines.extend(f"• {line}" for line in deny_changes)

        if change_lines:
            return affected_target, change_lines

        return affected_target, ["Discord audit log did not expose granular overwrite details for this change."]

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
        """Log voluntary leaves while avoiding misleading entries for kicks/bans."""

        kick_entry = await self._find_recent_audit_entry(
            member.guild,
            action=discord.AuditLogAction.kick,
            target_id=member.id,
            fallback_target_name=str(member),
        )
        if kick_entry is not None:
            await self._send_audit_embed(
                member.guild,
                title="Member Kicked",
                description=f"{member.mention} (`{member.id}`) was removed from the server.",
                fields=[("By", self._format_actor(kick_entry.user), False)],
                color=discord.Color.red(),
            )
            return

        ban_entry = await self._find_recent_audit_entry(
            member.guild,
            action=discord.AuditLogAction.ban,
            target_id=member.id,
            fallback_target_name=str(member),
        )
        if ban_entry is not None:
            # `on_member_ban` will log the ban itself, so suppress the generic leave log.
            return

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

        overwrite_actions = [
            getattr(discord.AuditLogAction, "overwrite_update", discord.AuditLogAction.channel_update),
            getattr(discord.AuditLogAction, "overwrite_create", discord.AuditLogAction.channel_update),
            getattr(discord.AuditLogAction, "overwrite_delete", discord.AuditLogAction.channel_update),
            discord.AuditLogAction.channel_update,
        ]
        audit_entry = await self._find_recent_audit_entry_from_actions(
            after.guild,
            actions=overwrite_actions,
            target_id=after.id,
            fallback_target_name=getattr(after, "name", None),
        )
        affected_target, overwrite_change_lines = self._channel_overwrite_change_lines(audit_entry)
        if affected_target is None:
            affected_target = self._resolve_changed_overwrite_target_label(before, after)

        fields = [("By", self._format_actor(audit_entry.user if audit_entry else None), False)]
        if affected_target is not None:
            fields.append(("Affected", affected_target, False))
        fields.append(("Changes", "\n".join(overwrite_change_lines)[:1024], False))

        await self._send_audit_embed(
            after.guild,
            title="Channel Permissions Updated",
            description=(
                f"Permission overwrites changed for "
                f"{after.mention if hasattr(after, 'mention') else after.name} (`{after.id}`)."
            ),
            fields=fields,
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
        nickname_changed = before.nick != after.nick

        if nickname_changed:
            changed_fields.append(("Nickname", f"`{before.nick or 'None'}` ➜ `{after.nick or 'None'}`", False))

        before_role_ids = {role.id for role in before.roles}
        after_role_ids = {role.id for role in after.roles}
        roles_changed = before_role_ids != after_role_ids
        if roles_changed:
            added = [f"<@&{role.id}>" for role in after.roles if role.id not in before_role_ids]
            removed = [f"<@&{role.id}>" for role in before.roles if role.id not in after_role_ids]

            if added:
                changed_fields.append(("Roles Added", ", ".join(added), False))
            if removed:
                changed_fields.append(("Roles Removed", ", ".join(removed), False))

        if not changed_fields:
            return

        audit_actions: list[discord.AuditLogAction] = []
        if roles_changed:
            audit_actions.append(
                getattr(discord.AuditLogAction, "member_role_update", discord.AuditLogAction.member_update)
            )
        if nickname_changed or not audit_actions:
            audit_actions.append(discord.AuditLogAction.member_update)

        audit_entry = await self._find_recent_audit_entry_from_actions(
            after.guild,
            actions=audit_actions,
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
