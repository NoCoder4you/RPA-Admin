from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands


class AutoInviteConfigStore:
    """Load and expose role-to-server invite rules for the auto-invite cog."""

    def __init__(self, *, config_path: str | Path | None = None) -> None:
        base_path = Path(__file__).resolve().parent.parent
        self.config_path = Path(config_path) if config_path else base_path / "JSON" / "AutoInviteConfig.json"

    def _load_raw(self) -> dict[str, Any]:
        """Return raw JSON config data, or a safe empty structure on any read/parse failure."""

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
        """Return configured main server ID where role updates should be watched."""

        value = self._load_raw().get("main_server_id")
        return value if isinstance(value, int) else None

    def get_role_mapping(self, role_id: int) -> dict[str, Any] | None:
        """Return invite mapping for a given role ID if one exists."""

        role_invites = self._load_raw().get("role_invites", [])
        if not isinstance(role_invites, list):
            return None

        for mapping in role_invites:
            if not isinstance(mapping, dict):
                continue
            if mapping.get("role_id") == role_id:
                return mapping
        return None


class AutoInviteCog(commands.Cog):
    """Send one-time invite links when users gain specific roles in the main server."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config_store = AutoInviteConfigStore()

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        """Detect newly added roles and DM a one-time invite for any mapped role."""

        # Restrict checks to the configured main server if an ID is provided.
        configured_main_server_id = self.config_store.get_main_server_id()
        if configured_main_server_id is not None and after.guild.id != configured_main_server_id:
            return

        old_role_ids = {role.id for role in before.roles}
        new_role_ids = {role.id for role in after.roles}
        added_role_ids = new_role_ids - old_role_ids

        # Process each newly granted role and send invites for all matching mappings.
        for role_id in added_role_ids:
            mapping = self.config_store.get_role_mapping(role_id)
            if mapping is None:
                continue
            await self._send_single_use_invite(member=after, mapping=mapping)

    async def _send_single_use_invite(self, *, member: discord.Member, mapping: dict[str, Any]) -> None:
        """Create a one-use invite for the target server and DM it to the member."""

        target_server_id = mapping.get("target_server_id")
        if not isinstance(target_server_id, int):
            return

        target_guild = self.bot.get_guild(target_server_id)
        if target_guild is None:
            return

        invite_channel = self._resolve_invite_channel(target_guild, mapping.get("target_channel_id"))
        if invite_channel is None:
            return

        try:
            invite = await invite_channel.create_invite(
                max_uses=1,
                unique=True,
                reason=f"Auto invite for role assignment in {member.guild.name}",
            )
            embed = self._build_invite_embed(invite_url=invite.url)
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

        if isinstance(preferred_channel_id, int):
            preferred_channel = target_guild.get_channel(preferred_channel_id)
            if isinstance(preferred_channel, discord.abc.GuildChannel) and hasattr(preferred_channel, "create_invite"):
                return preferred_channel

        # Fallback: first text channel where invite creation is possible.
        for channel in getattr(target_guild, "text_channels", []):
            if hasattr(channel, "create_invite"):
                return channel

        return None

    def _build_invite_embed(self, *, invite_url: str) -> discord.Embed:
        """Build the required DM embed with a fixed congratulatory title and invite link."""

        return discord.Embed(
            title="congratulations",
            description=f"please use this link to join x server\n{invite_url}",
            color=discord.Color.green(),
        )


async def setup(bot: commands.Bot) -> None:
    """discord.py extension entrypoint."""

    await bot.add_cog(AutoInviteCog(bot))
