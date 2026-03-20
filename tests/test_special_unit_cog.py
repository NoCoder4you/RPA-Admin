"""Unit tests for special-unit join role synchronization."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

try:
    from COGS.SpecialUnitCog import SpecialUnitCog
except ModuleNotFoundError:  # pragma: no cover - environment-dependent test skip
    SpecialUnitCog = None


@unittest.skipIf(SpecialUnitCog is None, "discord.py is not installed in the test environment")
class SpecialUnitCogTests(unittest.IsolatedAsyncioTestCase):
    """Ensure special-unit role sync only happens for correctly configured joins."""

    async def test_member_join_adds_special_unit_role_when_main_role_is_present(self) -> None:
        """Members joining a configured unit guild should inherit the mapped unit role."""

        cog = SpecialUnitCog.__new__(SpecialUnitCog)
        cog.bot = SimpleNamespace()
        cog.special_unit_store = SimpleNamespace(
            get_unit_config=lambda guild_id: SimpleNamespace(
                special_unit_server_id=guild_id,
                main_server_id=100,
                main_server_role_id=200,
                special_unit_role_id=300,
            )
        )

        main_role = SimpleNamespace(id=200)
        target_role = SimpleNamespace(id=300)
        main_member = SimpleNamespace(roles=[main_role])
        main_guild = SimpleNamespace(
            get_member=lambda member_id: main_member if member_id == 42 else None,
            get_role=lambda role_id: main_role if role_id == 200 else None,
        )
        cog.bot.get_guild = lambda guild_id: main_guild if guild_id == 100 else None

        special_guild = SimpleNamespace(id=555, get_role=lambda role_id: target_role if role_id == 300 else None)
        member = SimpleNamespace(id=42, guild=special_guild, roles=[], add_roles=AsyncMock())

        await cog.on_member_join(member)

        member.add_roles.assert_awaited_once_with(
            target_role,
            reason="Special unit auto-role: member has the required main server role",
        )

    async def test_member_join_skips_when_member_lacks_main_server_role(self) -> None:
        """No special-unit role should be added when the main-server requirement is missing."""

        cog = SpecialUnitCog.__new__(SpecialUnitCog)
        cog.bot = SimpleNamespace()
        cog.special_unit_store = SimpleNamespace(
            get_unit_config=lambda guild_id: SimpleNamespace(
                special_unit_server_id=guild_id,
                main_server_id=100,
                main_server_role_id=200,
                special_unit_role_id=300,
            )
        )

        main_role = SimpleNamespace(id=200)
        target_role = SimpleNamespace(id=300)
        main_member = SimpleNamespace(roles=[])
        main_guild = SimpleNamespace(
            get_member=lambda member_id: main_member if member_id == 42 else None,
            get_role=lambda role_id: main_role if role_id == 200 else None,
        )
        cog.bot.get_guild = lambda guild_id: main_guild if guild_id == 100 else None

        special_guild = SimpleNamespace(id=555, get_role=lambda role_id: target_role if role_id == 300 else None)
        member = SimpleNamespace(id=42, guild=special_guild, roles=[], add_roles=AsyncMock())

        await cog.on_member_join(member)

        member.add_roles.assert_not_awaited()

    async def test_member_join_skips_for_unconfigured_special_unit_server(self) -> None:
        """Guilds that are not listed in InterlinkedRoles.json should be ignored entirely."""

        cog = SpecialUnitCog.__new__(SpecialUnitCog)
        cog.bot = SimpleNamespace(get_guild=lambda _guild_id: None)
        cog.special_unit_store = SimpleNamespace(get_unit_config=lambda _guild_id: None)

        member = SimpleNamespace(
            id=42,
            guild=SimpleNamespace(id=999, get_role=lambda _role_id: None),
            roles=[],
            add_roles=AsyncMock(),
        )

        await cog.on_member_join(member)

        member.add_roles.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
