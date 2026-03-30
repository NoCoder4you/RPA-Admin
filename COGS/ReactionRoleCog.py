from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands

from common_paths import json_file

logger = logging.getLogger(__name__)


class ReactionRoleCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.data_file: Path = json_file("ReactionRoles.json")
        self.reaction_roles: list[dict[str, Any]] = self._load_data()
        self._restore_ran = False

    async def cog_load(self) -> None:
        """Restore configured bot reactions when this cog is loaded."""

        await self._restore_bot_reactions()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Fallback restore hook so reactions are restored after reconnect/restart."""

        await self._restore_bot_reactions()

    def _load_data(self) -> list[dict[str, Any]]:
        """Load persisted reaction role entries from JSON.

        Returns an empty list when the file does not exist or contains invalid JSON.
        """

        if not self.data_file.exists():
            return []

        try:
            with self.data_file.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            logger.exception("Failed to load reaction role data from %s", self.data_file)
            return []

        if not isinstance(payload, list):
            logger.warning("Reaction role file root must be a list: %s", self.data_file)
            return []

        cleaned: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                cleaned.append(
                    {
                        "guild_id": int(item["guild_id"]),
                        "channel_id": int(item["channel_id"]),
                        "message_id": int(item["message_id"]),
                        "emoji": str(item["emoji"]),
                        "role_id": int(item["role_id"]),
                    }
                )
            except (KeyError, TypeError, ValueError):
                # Ignore malformed rows while keeping valid rows usable.
                continue
        return cleaned

    def _save_data(self) -> None:
        """Persist reaction role entries to disk safely."""

        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        with self.data_file.open("w", encoding="utf-8") as handle:
            json.dump(self.reaction_roles, handle, indent=2)

    @staticmethod
    def _normalize_emoji(emoji: str) -> str:
        """Normalize both unicode and custom emoji strings into comparable storage form."""

        raw = emoji.strip()
        custom_match = re.fullmatch(r"<a?:([A-Za-z0-9_]+):(\d+)>", raw)
        if custom_match:
            return f"{custom_match.group(1)}:{custom_match.group(2)}"
        return raw

    async def _restore_bot_reactions(self) -> None:
        """Ensure each configured message has exactly one bot reaction for its entry."""

        if self._restore_ran or not self.bot.is_ready():
            return
        self._restore_ran = True

        grouped: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
        for entry in self.reaction_roles:
            key = (entry["guild_id"], entry["channel_id"], entry["message_id"])
            grouped.setdefault(key, []).append(entry)

        for (guild_id, channel_id, message_id), entries in grouped.items():
            # Keep the latest entry for a message and discard extras so the bot only
            # ever shows one active reaction-role reaction on that message.
            active_entry = entries[-1]
            if len(entries) > 1:
                self.reaction_roles = [
                    item
                    for item in self.reaction_roles
                    if not (
                        item["guild_id"] == guild_id
                        and item["channel_id"] == channel_id
                        and item["message_id"] == message_id
                        and item is not active_entry
                    )
                ]
                self._save_data()

            await self._sync_message_reaction(active_entry)

    async def _sync_message_reaction(self, entry: dict[str, Any]) -> bool:
        """Remove stale bot reactions for a configured message and add the active one.

        Returns ``True`` when the target reaction add operation succeeded.
        """

        guild = self.bot.get_guild(entry["guild_id"])
        if guild is None:
            return False

        channel = guild.get_channel(entry["channel_id"])
        if channel is None or not isinstance(channel, discord.abc.Messageable):
            return False

        try:
            message = await channel.fetch_message(entry["message_id"])
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return False

        bot_user = self.bot.user
        if bot_user is None:
            return False

        # Remove any previous bot reaction(s) on this message first.
        for reaction in message.reactions:
            try:
                if reaction.me:
                    await message.remove_reaction(reaction.emoji, bot_user)
            except (discord.Forbidden, discord.HTTPException):
                continue

        try:
            await message.add_reaction(entry["emoji"])
            return True
        except (discord.HTTPException, TypeError):
            return False

    def _find_entry(
        self,
        *,
        guild_id: int,
        message_id: int,
        emoji: str | None = None,
        role_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Find a configured entry using message id and optional filters."""

        for entry in self.reaction_roles:
            if entry["guild_id"] != guild_id or entry["message_id"] != message_id:
                continue
            if emoji is not None and entry["emoji"] != emoji:
                continue
            if role_id is not None and entry["role_id"] != role_id:
                continue
            return entry
        return None

    def _missing_bot_permissions(
        self,
        *,
        channel: discord.TextChannel,
        me: discord.Member,
    ) -> list[str]:
        """Return a list of permission names the bot is missing in a channel."""

        perms = channel.permissions_for(me)
        missing_permissions: list[str] = []
        if not perms.view_channel:
            missing_permissions.append("View Channel")
        if not perms.read_message_history:
            missing_permissions.append("Read Message History")
        if not perms.add_reactions:
            missing_permissions.append("Add Reactions")
        if not perms.manage_roles:
            missing_permissions.append("Manage Roles")
        if not perms.send_messages:
            missing_permissions.append("Send Messages")
        return missing_permissions

    async def _upsert_reaction_role_for_message(
        self,
        *,
        guild: discord.Guild,
        channel: discord.TextChannel,
        message: discord.Message,
        emoji: str,
        role: discord.Role,
    ) -> tuple[bool, str]:
        """Create/replace the reaction-role mapping for one target message."""

        normalized_emoji = self._normalize_emoji(emoji)

        # Remove any previous entry for this message first, then add the new one.
        old_entry = self._find_entry(guild_id=guild.id, message_id=message.id)
        if old_entry is not None:
            self.reaction_roles.remove(old_entry)
            self._save_data()

        new_entry = {
            "guild_id": guild.id,
            "channel_id": channel.id,
            "message_id": message.id,
            "emoji": normalized_emoji,
            "role_id": role.id,
        }

        # Prevent duplicate message+emoji entries in case of hand-edited JSON.
        duplicate = self._find_entry(guild_id=guild.id, message_id=message.id, emoji=normalized_emoji)
        if duplicate is not None:
            return False, "That message + emoji pair is already configured."

        if not await self._sync_message_reaction(new_entry):
            # Attempt to restore old reaction mapping if the new config is invalid.
            if old_entry is not None:
                self.reaction_roles.append(old_entry)
                self._save_data()
                await self._sync_message_reaction(old_entry)
            return False, "Failed to add the configured bot reaction. Check channel/message/emoji validity."

        self.reaction_roles.append(new_entry)
        self._save_data()
        return True, f"Configured reaction role: message `{message.id}`, emoji `{normalized_emoji}`, role {role.mention}."

    def _build_reaction_role_message(
            self,
            *,
            emoji: str,
            role: discord.Role,
            message_text: str,
    ) -> str:
        """Build a minimal, eye-catching reaction-role prompt."""

        normalized_emoji = self._normalize_emoji(emoji)

        return (
            "╔════════════════════╗\n"
            "       ✨ **REACTION ROLE** ✨\n"
            "╚════════════════════╝\n\n"
            f"# {normalized_emoji}{normalized_emoji}{normalized_emoji} \n# {role.mention}\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"## {message_text}\n"
            f"━━━━━━━━━━━━━━━━━━"
        )

    async def _resolve_member(self, payload: discord.RawReactionActionEvent) -> discord.Member | None:
        """Get member for raw events, including uncached scenarios after restart."""

        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        if guild is None:
            return None

        member = guild.get_member(payload.user_id)
        if member is not None:
            return member

        try:
            return await guild.fetch_member(payload.user_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """Toggle role when a user adds the configured reaction, then remove their reaction."""

        if payload.guild_id is None:
            return

        member = await self._resolve_member(payload)
        if member is None or member.bot:
            return

        normalized = self._normalize_emoji(str(payload.emoji))
        entry = self._find_entry(guild_id=payload.guild_id, message_id=payload.message_id, emoji=normalized)
        if entry is None:
            return

        role = member.guild.get_role(entry["role_id"])
        if role is None:
            return

        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Reaction role toggled off")
            else:
                await member.add_roles(role, reason="Reaction role toggled on")
        except (discord.Forbidden, discord.HTTPException):
            logger.exception("Failed to toggle reaction role guild=%s user=%s", payload.guild_id, payload.user_id)
            return

        guild = self.bot.get_guild(entry["guild_id"])
        if guild is None:
            return

        channel = guild.get_channel(entry["channel_id"])
        if channel is None or not isinstance(channel, discord.abc.Messageable):
            return

        try:
            message = await channel.fetch_message(entry["message_id"])
            await message.remove_reaction(payload.emoji, member)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        return

    @commands.group(name="reactionrole", invoke_without_command=True)
    @commands.guild_only()
    async def reactionrole_group(self, ctx: commands.Context) -> None:
        """Base command group for reaction-role management."""

        await ctx.send("# Use: \nreactionrole add \nreactionrole create \nreactionrole remove \nreactionrole list")

    @reactionrole_group.command(name="add")
    @commands.has_permissions(manage_roles=True)
    @commands.guild_only()
    async def reactionrole_add(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        message_id: int,
        emoji: str,
        role: discord.Role,
    ) -> None:
        """Create or replace the reaction-role entry for a specific message."""

        if ctx.guild is None or ctx.me is None:
            await ctx.send("This command can only be used in a server.")
            return

        missing_permissions = self._missing_bot_permissions(channel=channel, me=ctx.me)

        if missing_permissions:
            await ctx.send(f"I am missing required permissions in {channel.mention}: {', '.join(missing_permissions)}")
            return

        if role >= ctx.me.top_role:
            await ctx.send("I cannot manage that role because it is above or equal to my top role.")
            return

        normalized_emoji = self._normalize_emoji(emoji)

        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            await ctx.send("Message not found in that channel.")
            return
        except discord.Forbidden:
            await ctx.send("I do not have permission to read that message.")
            return
        except discord.HTTPException:
            await ctx.send("Discord API error while fetching that message.")
            return

        success, response_text = await self._upsert_reaction_role_for_message(
            guild=ctx.guild,
            channel=channel,
            message=message,
            emoji=normalized_emoji,
            role=role,
        )
        await ctx.send(response_text)
        if not success:
            return

    @reactionrole_group.command(name="create")
    @commands.has_permissions(manage_roles=True)
    @commands.guild_only()
    async def reactionrole_create(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        emoji: str,
        role: discord.Role,
        *,
        message_text: str,
    ) -> None:
        """Create a new message and immediately configure it as a reaction-role message."""

        if ctx.guild is None or ctx.me is None:
            await ctx.send("This command can only be used in a server.")
            return

        missing_permissions = self._missing_bot_permissions(channel=channel, me=ctx.me)
        if missing_permissions:
            await ctx.send(f"I am missing required permissions in {channel.mention}: {', '.join(missing_permissions)}")
            return

        if role >= ctx.me.top_role:
            await ctx.send("I cannot manage that role because it is above or equal to my top role.")
            return

        try:
            # Let administrators provide the full message body; this becomes the
            # visible reaction-role prompt users react to.
            prompt = self._build_reaction_role_message(
                emoji=emoji,
                role=role,
                message_text=message_text,
            )
            # allowed_mentions ensures the role mention is actually rendered as a
            # mention (instead of inert text) and remains clear/eye-catching.
            message = await channel.send(
                prompt,
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
        except discord.Forbidden:
            await ctx.send(f"I do not have permission to send messages in {channel.mention}.")
            return
        except discord.HTTPException:
            await ctx.send("Discord API error while creating the reaction-role message.")
            return

        success, response_text = await self._upsert_reaction_role_for_message(
            guild=ctx.guild,
            channel=channel,
            message=message,
            emoji=emoji,
            role=role,
        )
        await ctx.send(response_text)
        if not success:
            return

    @reactionrole_group.command(name="remove")
    @commands.has_permissions(manage_roles=True)
    @commands.guild_only()
    async def reactionrole_remove(
        self,
        ctx: commands.Context,
        message_id: int,
        emoji: str | None = None,
        role: discord.Role | None = None,
    ) -> None:
        """Remove a reaction-role entry for a message."""

        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return

        normalized_emoji = self._normalize_emoji(emoji) if emoji else None
        role_id = role.id if role else None

        entry = self._find_entry(
            guild_id=ctx.guild.id,
            message_id=message_id,
            emoji=normalized_emoji,
            role_id=role_id,
        )
        if entry is None:
            await ctx.send("No matching reaction-role entry found.")
            return

        self.reaction_roles.remove(entry)
        self._save_data()

        guild = ctx.guild
        channel = guild.get_channel(entry["channel_id"])
        if isinstance(channel, discord.abc.Messageable) and self.bot.user is not None:
            try:
                message = await channel.fetch_message(entry["message_id"])
                await message.remove_reaction(entry["emoji"], self.bot.user)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                # Clean failure: keep DB removal even if Discord cleanup fails.
                pass

        await ctx.send(f"Removed reaction role entry for message `{message_id}`.")

    @reactionrole_group.command(name="list")
    @commands.has_permissions(manage_roles=True)
    @commands.guild_only()
    async def reactionrole_list(self, ctx: commands.Context) -> None:
        """List configured reaction-role entries for this server."""

        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return

        entries = [entry for entry in self.reaction_roles if entry["guild_id"] == ctx.guild.id]
        if not entries:
            await ctx.send("No reaction roles are configured for this server.")
            return

        lines = []
        for entry in entries:
            lines.append(
                f"• Message `{entry['message_id']}` in <#{entry['channel_id']}> | "
                f"Emoji `{entry['emoji']}` → <@&{entry['role_id']}>"
            )

        await ctx.send("Configured reaction roles:\n" + "\n".join(lines))


async def setup(bot: commands.Bot) -> None:
    """discord.py extension setup entrypoint."""

    await bot.add_cog(ReactionRoleCog(bot))