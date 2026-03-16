"""Unit tests for Habbo verification cog nickname synchronization behavior."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

try:
    import discord
    from COGS.ServerVerifyRPA import HabboVerificationCog
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


@unittest.skipIf(HabboVerificationCog is None, "discord.py is not installed in the test environment")
class HabboVerificationCogReactionRoleTests(unittest.IsolatedAsyncioTestCase):
    """Validate reaction-based Awaiting Verification role assignment flow."""

    async def test_reaction_add_assigns_awaiting_verification_role(self) -> None:
        # Build a lightweight cog test double without running full bot startup logic.
        bot = SimpleNamespace()
        cog = HabboVerificationCog.__new__(HabboVerificationCog)
        cog.bot = bot
        cog.server_config_store = SimpleNamespace(get_verification_reaction_message_id=lambda: 1481010999157981256)

        role = SimpleNamespace(name="Awaiting Verification")
        member = SimpleNamespace(roles=[], add_roles=AsyncMock())
        guild = SimpleNamespace(roles=[role], get_member=lambda _uid: member, fetch_member=AsyncMock())
        bot.user = SimpleNamespace(id=999)
        bot.get_guild = lambda _gid: guild

        payload = SimpleNamespace(
            guild_id=123,
            user_id=555,
            message_id=1481010999157981256,
            emoji="✅",
        )

        await cog.on_raw_reaction_add(payload)

        member.add_roles.assert_awaited_once()

    async def test_reaction_add_skips_when_message_id_does_not_match(self) -> None:
        # Ensure role assignment is gated to the exact configured verification message ID.
        bot = SimpleNamespace()
        cog = HabboVerificationCog.__new__(HabboVerificationCog)
        cog.bot = bot
        cog.server_config_store = SimpleNamespace(get_verification_reaction_message_id=lambda: 1481010999157981256)

        role = SimpleNamespace(name="Awaiting Verification")
        member = SimpleNamespace(roles=[], add_roles=AsyncMock())
        guild = SimpleNamespace(roles=[role], get_member=lambda _uid: member, fetch_member=AsyncMock())
        bot.user = SimpleNamespace(id=999)
        bot.get_guild = lambda _gid: guild

        payload = SimpleNamespace(
            guild_id=123,
            user_id=555,
            message_id=111,
            emoji="✅",
        )

        await cog.on_raw_reaction_add(payload)

        member.add_roles.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
