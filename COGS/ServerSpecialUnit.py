from __future__ import annotations

import discord
from discord.ext import commands

from habbo_verification_core import SpecialUnitStore


class SpecialUnitCog(commands.Cog):
    """Grant configured special-unit roles when eligible users join a unit server."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Keep unit join rules in JSON so staff can manage server mappings without code changes.
        self.special_unit_store = SpecialUnitStore()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Assign the configured special-unit role if the member holds the main-server role."""

        unit_config = self.special_unit_store.get_unit_config(member.guild.id)
        if unit_config is None:
            # Ignore joins for guilds that are not configured as special-unit servers.
            return

        main_guild = self.bot.get_guild(unit_config.main_server_id)
        if main_guild is None:
            return

        main_member = main_guild.get_member(member.id)
        if main_member is None:
            # Only mirror access for users who are currently in the main server as well.
            return

        required_main_role = main_guild.get_role(unit_config.main_server_role_id)
        target_special_role = member.guild.get_role(unit_config.special_unit_role_id)
        if required_main_role is None or target_special_role is None:
            return

        if required_main_role not in getattr(main_member, "roles", []):
            return

        if target_special_role in getattr(member, "roles", []):
            return

        await member.add_roles(
            target_special_role,
            reason=(
                "Special unit auto-role: member has the required main server role"
            ),
        )


async def setup(bot: commands.Bot) -> None:
    """discord.py extension entry point."""

    await bot.add_cog(SpecialUnitCog(bot))
