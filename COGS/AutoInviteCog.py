from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands

from common_paths import json_file

logger = logging.getLogger(__name__)


class AutoInviteConfigStore:
    """Load and expose role-to-server invite rules stored in InterlinkedRoles.json."""

    def __init__(self, *, config_path: str | Path | None = None) -> None:
        # Reuse the interlinked-role mapping file so one source of truth decides which
        # main-server role should unlock access to which linked server.
        self.config_path = Path(config_path) if config_path else json_file("InterlinkedRoles.json")

    def _load_raw(self) -> list[dict[str, Any]]:
        """Return raw JSON config rows, or an empty list on any read/parse failure."""

        if not self.config_path.exists():
            logger.warning("AutoInvite config file is missing: %s", self.config_path)
            return []

        try:
            with self.config_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, list):
                return [entry for entry in payload if isinstance(entry, dict)]
            if isinstance(payload, dict):
                # Accept a single-object payload as a convenience for hand-edited files.
                # The normal repository format is still a list of link definitions.
                return [payload]
        except (json.JSONDecodeError, OSError):
            logger.exception("Failed to read AutoInvite config file: %s", self.config_path)
            return []

        logger.warning("AutoInvite config has unsupported JSON root type in %s", self.config_path)
        return []

    def get_role_mappings(self, *, main_server_id: int, role_id: int) -> list[dict[str, Any]]:
        """Return invite mappings for a role inside a specific main server.

        InterlinkedRoles.json already stores the relationship between the main server
        role and the destination linked server, so we transform those rows into the
        smaller mapping shape that the invite sender expects.
        """

        matches: list[dict[str, Any]] = []
        for entry in self._load_raw():
            if self._safe_int(entry.get("main_server_id")) != main_server_id:
                continue
            if self._safe_int(entry.get("main_server_role_id")) != role_id:
                continue

            target_server_id = self._safe_int(entry.get("special_unit_server_id"))
            if target_server_id is None:
                continue

            matches.append(
                {
                    "role_id": role_id,
                    "target_server_id": target_server_id,
                    "target_channel_id": entry.get("target_channel_id"),
                }
            )
        logger.info(
            "AutoInvite mapping lookup for main_server_id=%s role_id=%s returned %s mapping(s)",
            main_server_id,
            role_id,
            len(matches),
        )
        return matches

    def get_main_server_id(self) -> int | None:
        """Return the first configured main server ID, if any.

        The runtime listener now filters per-entry via ``get_role_mappings``; this
        helper remains available for compatibility with tests and any future callers
        that want a quick answer for the primary configured main server.
        """

        for entry in self._load_raw():
            main_server_id = self._safe_int(entry.get("main_server_id"))
            if main_server_id is not None:
                return main_server_id
        return None

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        """Convert common JSON scalar values to integers when possible."""

        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return None


class AutoInviteCog(commands.Cog):
    """Send one-time invite links when users gain specific roles in the main server."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config_store = AutoInviteConfigStore()

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        """Detect newly added roles and DM one-time invites for any mapped role."""

        old_role_ids = {role.id for role in before.roles}
        added_roles = [role for role in after.roles if role.id not in old_role_ids]
        logger.info(
            "AutoInvite member update guild_id=%s user_id=%s added_roles=%s",
            after.guild.id,
            after.id,
            [role.id for role in added_roles],
        )

        # Process each newly granted role and send invites for all matching server mappings.
        for role in added_roles:
            for mapping in self.config_store.get_role_mappings(main_server_id=after.guild.id, role_id=role.id):
                logger.info(
                    "AutoInvite processing role_id=%s for user_id=%s target_server_id=%s",
                    role.id,
                    after.id,
                    mapping.get("target_server_id"),
                )
                await self._send_single_use_invite(member=after, mapping=mapping, triggering_role=role)

    async def _send_single_use_invite(
        self,
        *,
        member: discord.Member,
        mapping: dict[str, Any],
        triggering_role: discord.abc.Snowflake | None = None,
    ) -> None:
        """Create a one-use invite for the target server and DM it to the member."""

        target_server_id = AutoInviteConfigStore._safe_int(mapping.get("target_server_id"))
        if target_server_id is None:
            logger.warning("AutoInvite skipped because mapping has invalid target_server_id: %s", mapping)
            return

        target_guild = self.bot.get_guild(target_server_id)
        if target_guild is None:
            logger.warning("AutoInvite skipped because bot is not in target guild id=%s", target_server_id)
            return

        invite_channel = self._resolve_invite_channel(target_guild, mapping.get("target_channel_id"))
        if invite_channel is None:
            logger.warning("AutoInvite skipped because no invite-capable channel was found in guild id=%s", target_server_id)
            return

        target_server_name = mapping.get("target_server_name")
        if not isinstance(target_server_name, str) or not target_server_name.strip():
            # Fall back to the live guild name so the DM still explains which server the invite targets.
            target_server_name = getattr(target_guild, "name", "the target server")

        try:
            invite = await invite_channel.create_invite(
                max_uses=1,
                # Keep the invite valid until the member actually uses it. The link is
                # still effectively temporary because Discord invalidates it after the
                # first successful join thanks to ``max_uses=1``.
                max_age=0,
                unique=True,
                reason=f"Auto invite for role assignment in {member.guild.name}",
            )
            embed = self._build_invite_embed(
                invite_url=invite.url,
                target_server_name=target_server_name,
                triggering_role_name=getattr(triggering_role, "name", None),
            )
            await member.send(embed=embed)
            logger.info(
                "AutoInvite DM sent user_id=%s target_server_id=%s channel_id=%s invite_url=%s",
                member.id,
                target_server_id,
                getattr(invite_channel, "id", None),
                invite.url,
            )
        except (discord.Forbidden, discord.HTTPException):
            # Fail silently so role updates still work even when invite/DM permissions fail.
            logger.exception(
                "AutoInvite failed while creating/sending invite user_id=%s target_server_id=%s",
                member.id,
                target_server_id,
            )
            return

    def _resolve_invite_channel(
        self,
        target_guild: discord.Guild,
        preferred_channel_id: Any,
    ) -> discord.abc.GuildChannel | None:
        """Resolve the best channel to create invites from, preferring configured channel ID."""

        preferred_channel_id_int = AutoInviteConfigStore._safe_int(preferred_channel_id)
        if preferred_channel_id_int is not None:
            preferred_channel = target_guild.get_channel(preferred_channel_id_int)
            if (
                isinstance(preferred_channel, discord.abc.GuildChannel)
                and hasattr(preferred_channel, "create_invite")
                and self._can_create_invite_in_channel(target_guild, preferred_channel)
            ):
                logger.info(
                    "AutoInvite using preferred channel_id=%s guild_id=%s",
                    preferred_channel_id_int,
                    target_guild.id,
                )
                return preferred_channel

        # Fallback: first text channel where the bot can actually create invite links.
        for channel in getattr(target_guild, "text_channels", []):
            if hasattr(channel, "create_invite") and self._can_create_invite_in_channel(target_guild, channel):
                logger.info(
                    "AutoInvite using fallback channel_id=%s guild_id=%s",
                    getattr(channel, "id", None),
                    target_guild.id,
                )
                return channel

        logger.warning("AutoInvite found no invite-capable channel guild_id=%s", target_guild.id)
        return None

    @staticmethod
    def _can_create_invite_in_channel(target_guild: discord.Guild, channel: discord.abc.GuildChannel) -> bool:
        """Return True when bot permissions allow invite creation in the channel.

        Some servers expose channels where invites are disabled for the bot. If we pick
        one of those channels, invite creation raises ``discord.Forbidden`` and no DM
        is sent. Proactively filtering channels here avoids that false-negative path.
        """

        bot_member = getattr(target_guild, "me", None)
        if bot_member is None or not hasattr(channel, "permissions_for"):
            return True

        try:
            permissions = channel.permissions_for(bot_member)
        except Exception:
            # If permission introspection fails unexpectedly, keep behavior permissive
            # and let Discord enforce permissions at invite creation time.
            return True

        return bool(getattr(permissions, "create_instant_invite", False))

    def _build_invite_embed(
        self,
        *,
        invite_url: str,
        target_server_name: str,
        triggering_role_name: str | None,
    ) -> discord.Embed:
        """Build the DM embed containing a clear destination name and invite link."""

        role_context = ""
        if triggering_role_name:
            role_context = f" from your **{triggering_role_name}** role"

        return discord.Embed(
            title="Your server invite is ready",
            description=(
                f"You received a qualifying role{role_context}, so here is your unique invite for **{target_server_name}**.\n"
                f"{invite_url}\n\n"
                "This invite is single-use and stays valid until you redeem it."
            ),
            color=discord.Color.green(),
        )


async def setup(bot: commands.Bot) -> None:
    """discord.py extension entrypoint."""

    await bot.add_cog(AutoInviteCog(bot))
