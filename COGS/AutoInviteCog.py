from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands

from common_paths import json_file


class AutoInviteConfigStore:
    """Load and expose role-to-server invite rules stored in serverconfig.json."""

    def __init__(self, *, config_path: str | Path | None = None) -> None:
        # Keep auto-invite settings in the shared server configuration file so all
        # server-specific settings remain in one predictable location.
        self.config_path = Path(config_path) if config_path else json_file("serverconfig.json")

    def _load_raw(self) -> dict[str, Any]:
        """Return raw JSON config data, or an empty mapping on any read/parse failure."""

        if not self.config_path.exists():
            return {}

        try:
            with self.config_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict):
                return payload
        except (json.JSONDecodeError, OSError):
            return {}

        return {}

    def get_main_server_id(self) -> int | None:
        """Return the configured source server ID where role changes should be watched."""

        config = self._load_raw()
        auto_invite = config.get("auto_invite")
        if not isinstance(auto_invite, dict):
            return None
        return self._safe_int(auto_invite.get("main_server_id"))

    def get_role_mappings(self, role_id: int) -> list[dict[str, Any]]:
        """Return all invite mappings for the provided role ID.

        The config supports multiple destination servers per role, which allows a
        single role grant to DM one or more unique invites.
        """

        config = self._load_raw()
        auto_invite = config.get("auto_invite")
        if not isinstance(auto_invite, dict):
            return []

        role_invites = auto_invite.get("role_invites", [])
        if not isinstance(role_invites, list):
            return []

        matches: list[dict[str, Any]] = []
        for mapping in role_invites:
            if not isinstance(mapping, dict):
                continue
            if self._safe_int(mapping.get("role_id")) == role_id:
                matches.append(mapping)
        return matches

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

        # Restrict checks to the configured main server if an ID is provided.
        configured_main_server_id = self.config_store.get_main_server_id()
        if configured_main_server_id is not None and after.guild.id != configured_main_server_id:
            return

        old_role_ids = {role.id for role in before.roles}
        new_role_ids = {role.id for role in after.roles}
        added_role_ids = new_role_ids - old_role_ids

        # Process each newly granted role and send invites for all matching server mappings.
        for role_id in added_role_ids:
            for mapping in self.config_store.get_role_mappings(role_id):
                await self._send_single_use_invite(member=after, mapping=mapping)

    async def _send_single_use_invite(self, *, member: discord.Member, mapping: dict[str, Any]) -> None:
        """Create a one-use invite for the target server and DM it to the member."""

        target_server_id = AutoInviteConfigStore._safe_int(mapping.get("target_server_id"))
        if target_server_id is None:
            return

        target_guild = self.bot.get_guild(target_server_id)
        if target_guild is None:
            return

        invite_channel = self._resolve_invite_channel(target_guild, mapping.get("target_channel_id"))
        if invite_channel is None:
            return

        target_server_name = mapping.get("target_server_name")
        if not isinstance(target_server_name, str) or not target_server_name.strip():
            # Fall back to the live guild name so the DM still explains which server the invite targets.
            target_server_name = getattr(target_guild, "name", "the target server")

        try:
            invite = await invite_channel.create_invite(
                max_uses=1,
                unique=True,
                reason=f"Auto invite for role assignment in {member.guild.name}",
            )
            embed = self._build_invite_embed(invite_url=invite.url, target_server_name=target_server_name)
            await member.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            # Fail silently so role updates still work even when invite/DM permissions fail.
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
            if isinstance(preferred_channel, discord.abc.GuildChannel) and hasattr(preferred_channel, "create_invite"):
                return preferred_channel

        # Fallback: first text channel where invite creation is possible.
        for channel in getattr(target_guild, "text_channels", []):
            if hasattr(channel, "create_invite"):
                return channel

        return None

    def _build_invite_embed(self, *, invite_url: str, target_server_name: str) -> discord.Embed:
        """Build the DM embed containing a clear destination name and invite link."""

        return discord.Embed(
            title="Your server invite is ready",
            description=(
                f"You received a qualifying role, so here is your unique invite for **{target_server_name}**.\n"
                f"{invite_url}"
            ),
            color=discord.Color.green(),
        )


async def setup(bot: commands.Bot) -> None:
    """discord.py extension entrypoint."""

    await bot.add_cog(AutoInviteCog(bot))
