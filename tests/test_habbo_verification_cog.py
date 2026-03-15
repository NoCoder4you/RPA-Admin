"""Unit tests for Habbo verification cog nickname synchronization behavior."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

try:
    import discord
    from COGS.habbo_verification import HabboVerificationCog
except ModuleNotFoundError as exc:  # pragma: no cover - environment-dependent test skip
    raise unittest.SkipTest(f"discord.py is not installed in this environment: {exc}")


class HabboVerificationCogNicknameTests(unittest.IsolatedAsyncioTestCase):
    """Validate nickname synchronization outcomes used by /verify flows."""

    async def test_sync_member_nickname_updates_to_verified_habbo_name(self) -> None:
        cog = HabboVerificationCog(bot=MagicMock())
        member = SimpleNamespace(nick="OldName", edit=AsyncMock())
        interaction = SimpleNamespace(guild=object(), user=member)

        status = await cog._sync_member_nickname(interaction, "Siren")

        self.assertEqual(status, "Nickname updated to verified Habbo username.")
        member.edit.assert_awaited_once_with(
            nick="Siren",
            reason="Habbo verification nickname sync",
        )

    async def test_sync_member_nickname_skips_when_no_guild_context(self) -> None:
        cog = HabboVerificationCog(bot=MagicMock())
        member = SimpleNamespace(nick="OldName", edit=AsyncMock())
        interaction = SimpleNamespace(guild=None, user=member)

        status = await cog._sync_member_nickname(interaction, "Siren")

        self.assertEqual(status, "Skipped (nickname can only be changed inside a server).")
        member.edit.assert_not_called()

    async def test_sync_member_nickname_handles_missing_permissions(self) -> None:
        cog = HabboVerificationCog(bot=MagicMock())
        member = SimpleNamespace(nick="OldName", edit=AsyncMock(side_effect=discord.Forbidden(MagicMock(), "missing perms")))
        interaction = SimpleNamespace(guild=object(), user=member)

        status = await cog._sync_member_nickname(interaction, "Siren")

        self.assertEqual(status, "Failed (bot lacks permission to manage this nickname).")


if __name__ == "__main__":
    unittest.main()
