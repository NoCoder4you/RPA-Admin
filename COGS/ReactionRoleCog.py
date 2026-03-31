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
CUSTOM_EMOJI_PATTERN = re.compile(r"^([A-Za-z0-9_]+):(\d+)$")
REACTION_ROLE_EMBED_TITLE = "Reaction Roles"
REACTION_ROLE_EMBED_FOOTER = "React to toggle your roles."


class ReactionRoleCog(commands.Cog):
    """Manage one reaction-role mapping per message using raw reaction events."""

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

    def _display_emoji(self, *, guild: discord.Guild, stored_emoji: str) -> str:
        """Convert stored emoji values to a display-ready string for embeds."""

        custom_match = CUSTOM_EMOJI_PATTERN.fullmatch(stored_emoji)
        if not custom_match:
            return stored_emoji

        emoji_id = int(custom_match.group(2))
        custom_emoji = guild.get_emoji(emoji_id)
        if custom_emoji is not None:
            return str(custom_emoji)
        # Fallback to Discord custom emoji text format if the emoji object is not cached.
        return f"<:{custom_match.group(1)}:{emoji_id}>"

    async def _refresh_reaction_role_embed(
        self,
        *,
        guild: discord.Guild,
        channel: discord.TextChannel,
        message: discord.Message,
    ) -> None:
        """Update the target message embed so it reflects all configured mappings."""

        entries = self._entries_for_message(guild_id=guild.id, message_id=message.id)
        if not entries:
            return

        mapping_lines = []
        for entry in entries:
            mapping_lines.append(f"{self._display_emoji(guild=guild, stored_emoji=entry['emoji'])} = <@&{entry['role_id']}>")

        description = (
            "React to this message to assign yourself roles and gain channel access.\n\n"
            + "\n".join(mapping_lines)
            + "\n"
        )
        embed = discord.Embed(
            title=REACTION_ROLE_EMBED_TITLE,
            description=description,
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=REACTION_ROLE_EMBED_FOOTER)

        try:
            await message.edit(embed=embed, allowed_mentions=discord.AllowedMentions(roles=True))
        except (discord.Forbidden, discord.HTTPException):
            # Keep role mapping functionality even when cosmetic embed updates fail.
            logger.exception(
                "Failed to refresh reaction role embed guild=%s channel=%s message=%s",
                guild.id,
                channel.id,
                message.id,
            )

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
            # Keep at most one entry per message+emoji pair, but allow multiple
            # different emojis on one message so roles remain independently toggleable.
            deduped_by_emoji: dict[str, dict[str, Any]] = {}
            for entry in entries:
                deduped_by_emoji[entry["emoji"]] = entry

            deduped_entries = list(deduped_by_emoji.values())
            if len(deduped_entries) != len(entries):
                self.reaction_roles = [
                    item
                    for item in self.reaction_roles
                    if not (
                        item["guild_id"] == guild_id
                        and item["channel_id"] == channel_id
                        and item["message_id"] == message_id
                    )
                ] + deduped_entries
                self._save_data()

            await self._sync_message_reactions(
                guild_id=guild_id,
                channel_id=channel_id,
                message_id=message_id,
            )

    def _entries_for_message(self, *, guild_id: int, message_id: int) -> list[dict[str, Any]]:
        """Return all reaction-role entries configured for one message."""

        return [
            entry
            for entry in self.reaction_roles
            if entry["guild_id"] == guild_id and entry["message_id"] == message_id
        ]

    def _other_entries_for_message(
        self,
        *,
        guild_id: int,
        message_id: int,
        keep_emoji: str,
    ) -> list[dict[str, Any]]:
        """Return configured message entries except the selected emoji entry."""

        return [
            entry
            for entry in self._entries_for_message(guild_id=guild_id, message_id=message_id)
            if entry["emoji"] != keep_emoji
        ]

    async def _sync_message_reactions(
        self,
        *,
        guild_id: int,
        channel_id: int,
        message_id: int,
    ) -> bool:
        """Ensure all configured emojis for a message are present as bot reactions."""

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return False

        channel = guild.get_channel(channel_id)
        if channel is None or not isinstance(channel, discord.abc.Messageable):
            return False

        try:
            message = await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return False

        entries = self._entries_for_message(guild_id=guild_id, message_id=message_id)
        if not entries:
            return True

        added_any = False
        try:
            for entry in entries:
                await message.add_reaction(entry["emoji"])
                added_any = True
            return added_any
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

    async def _add_reaction_role_for_message(
        self,
        *,
        guild: discord.Guild,
        channel: discord.TextChannel,
        message: discord.Message,
        emoji: str,
        role: discord.Role,
    ) -> tuple[bool, str]:
        """Create a reaction-role mapping for one target message without overwriting others."""

        normalized_emoji = self._normalize_emoji(emoji)

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

        self.reaction_roles.append(new_entry)
        self._save_data()
        if not await self._sync_message_reactions(guild_id=guild.id, channel_id=channel.id, message_id=message.id):
            # Roll back persistence when the reaction cannot be applied.
            self.reaction_roles.remove(new_entry)
            self._save_data()
            return False, "Failed to add the configured bot reaction. Check channel/message/emoji validity."

        await self._refresh_reaction_role_embed(guild=guild, channel=channel, message=message)

        return True, f"Configured reaction role: message `{message.id}`, emoji `{normalized_emoji}`, role {role.mention}."

    def _build_reaction_role_embeds(
        self,
        *,
        emoji: str,
        role: discord.Role,
    ) -> list[discord.Embed]:
        """Build the minimal reaction-role embed requested by server admins."""

        normalized_emoji = self._normalize_emoji(emoji)
        embed = discord.Embed(
            title=REACTION_ROLE_EMBED_TITLE,
            description=(
                "React to this message to assign yourself roles and gain channel access.\n\n"
                f"{normalized_emoji} = {role.mention}\n"
            ),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=REACTION_ROLE_EMBED_FOOTER)
        return [embed]

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

    async def _toggle_member_role(self, *, member: discord.Member, role: discord.Role) -> str:
        """Toggle a role for a member and return the performed action."""

        has_role = any(existing_role.id == role.id for existing_role in member.roles)
        if has_role:
            await member.remove_roles(role, reason="Reaction role toggled off")
            return "removed"

        await member.add_roles(role, reason="Reaction role toggled on")
        return "added"

    async def _remove_member_reaction(self, *, payload: discord.RawReactionActionEvent, emoji: str) -> None:
        """Remove a specific reaction from the member on the target message."""

        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        if guild is None:
            return

        channel = guild.get_channel(payload.channel_id)
        if channel is None or not isinstance(channel, discord.abc.Messageable):
            return

        try:
            message = await channel.fetch_message(payload.message_id)
            await message.remove_reaction(emoji, discord.Object(id=payload.user_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

    async def _enforce_single_member_reaction(
        self,
        *,
        payload: discord.RawReactionActionEvent,
        selected_emoji: str,
    ) -> None:
        """Ensure the reacting member keeps only one configured reaction on a message."""

        if payload.guild_id is None:
            return

        for entry in self._other_entries_for_message(
            guild_id=payload.guild_id,
            message_id=payload.message_id,
            keep_emoji=selected_emoji,
        ):
            await self._remove_member_reaction(payload=payload, emoji=entry["emoji"])

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """Grant role when a user adds the configured reaction."""

        if payload.guild_id is None:
            return
        if self.bot.user is not None and payload.user_id == self.bot.user.id:
            # Ignore the bot's own reaction events so role toggling only tracks members.
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
            # Treat reaction add as a toggle event:
            # - if member does not have role yet -> add it
            # - if member already has role -> remove it
            action = await self._toggle_member_role(member=member, role=role)
            if action == "added":
                # Keep only one configured reaction per member for this message.
                await self._enforce_single_member_reaction(
                    payload=payload,
                    selected_emoji=normalized,
                )
            else:
                # Toggle-off path: remove the member's same reaction so a second click
                # immediately reflects the role removal in the UI.
                await self._remove_member_reaction(payload=payload, emoji=normalized)
        except (discord.Forbidden, discord.HTTPException):
            logger.exception("Failed to toggle reaction role guild=%s user=%s", payload.guild_id, payload.user_id)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        """Remove role when a user removes the configured reaction."""

        if payload.guild_id is None:
            return
        if self.bot.user is not None and payload.user_id == self.bot.user.id:
            # Prevent bot-driven cleanup reaction removals from affecting member roles.
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
            await member.remove_roles(role, reason="Reaction role removed")
        except (discord.Forbidden, discord.HTTPException):
            logger.exception("Failed to remove reaction role guild=%s user=%s", payload.guild_id, payload.user_id)

    @commands.group(name="reactionrole", invoke_without_command=True)
    @commands.guild_only()
    async def reactionrole_group(self, ctx: commands.Context) -> None:
        """Base command group for reaction-role management."""

        await ctx.send("Use: reactionrole add | reactionrole create | reactionrole remove | reactionrole list")

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
        """Add a reaction-role entry for a specific message without overriding others."""

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

        success, response_text = await self._add_reaction_role_for_message(
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
            # Create a minimal prompt embed and post it as the reaction-role message.
            embeds = self._build_reaction_role_embeds(
                emoji=emoji,
                role=role,
            )
            # Post the first embed and attach the reaction-role mapping to that
            # message. When content exceeds embed limits, send continuation embeds.
            message = await channel.send(
                embed=embeds[0],
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
            for extra_embed in embeds[1:]:
                await channel.send(
                    embed=extra_embed,
                    allowed_mentions=discord.AllowedMentions(roles=True),
                )
        except discord.Forbidden:
            await ctx.send(f"I do not have permission to send messages in {channel.mention}.")
            return
        except discord.HTTPException:
            await ctx.send("Discord API error while creating the reaction-role message.")
            return

        success, response_text = await self._add_reaction_role_for_message(
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
                await self._refresh_reaction_role_embed(guild=guild, channel=channel, message=message)
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
