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
        embed.add_field(name="Timestamp (UTC)", value=self._utc_now_iso(), inline=False)

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

        await self._send_audit_embed(
            guild,
            title="Member Banned",
            description=f"{user.mention if hasattr(user, 'mention') else user} (`{user.id}`) was banned.",
            color=discord.Color.red(),
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.abc.User) -> None:
        """Log unbans to keep moderation reversals visible in the audit trail."""

        await self._send_audit_embed(
            guild,
            title="Member Unbanned",
            description=f"{user.mention if hasattr(user, 'mention') else user} (`{user.id}`) was unbanned.",
            color=discord.Color.green(),
        )

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        """Log newly created channels so structural changes are visible to admins."""

        await self._send_audit_embed(
            channel.guild,
            title="Channel Created",
            description=f"{channel.mention if hasattr(channel, 'mention') else channel.name} (`{channel.id}`) was created.",
            fields=[("Type", str(channel.type), True)],
            color=discord.Color.green(),
        )

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        """Log channel deletions to preserve evidence of destructive changes."""

        await self._send_audit_embed(
            channel.guild,
            title="Channel Deleted",
            description=f"{channel.name} (`{channel.id}`) was deleted.",
            fields=[("Type", str(channel.type), True)],
            color=discord.Color.red(),
        )

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        """Log role creation so privilege model changes are historically discoverable."""

        await self._send_audit_embed(
            role.guild,
            title="Role Created",
            description=f"Role **{role.name}** (`{role.id}`) was created.",
            color=discord.Color.green(),
        )

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        """Log role deletion for forensic visibility around permission restructuring."""

        await self._send_audit_embed(
            role.guild,
            title="Role Deleted",
            description=f"Role **{role.name}** (`{role.id}`) was deleted.",
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

        await self._send_audit_embed(
            after.guild,
            title="Channel Permissions Updated",
            description=(
                f"Permission overwrites changed for "
                f"{after.mention if hasattr(after, 'mention') else after.name} (`{after.id}`)."
            ),
            color=discord.Color.gold(),
        )

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        """Log role permission updates so changes in privilege are auditable."""

        if before.permissions == after.permissions:
            return

        await self._send_audit_embed(
            after.guild,
            title="Role Permissions Updated",
            description=f"Permissions changed for role **{after.name}** (`{after.id}`).",
            fields=[
                ("Before", str(before.permissions.value), True),
                ("After", str(after.permissions.value), True),
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
