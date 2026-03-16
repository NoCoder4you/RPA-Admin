"""Unit tests for one-time invite DM behavior after role assignment."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

try:
    from COGS.AutoInviteCog import AutoInviteCog
except ModuleNotFoundError:
    AutoInviteCog = None


@unittest.skipIf(AutoInviteCog is None, "discord.py is not installed in the test environment")
class AutoInviteCogTests(unittest.IsolatedAsyncioTestCase):
    """Validate that mapped role grants create and DM single-use invite links."""

    async def test_member_update_triggers_invite_for_new_mapped_role(self) -> None:
        bot = MagicMock()
        cog = AutoInviteCog(bot)
        cog.config_store = SimpleNamespace(
            get_main_server_id=lambda: 100,
            get_role_mapping=lambda role_id: {"target_server_id": 200} if role_id == 55 else None,
        )
        cog._send_single_use_invite = AsyncMock()

        before = SimpleNamespace(guild=SimpleNamespace(id=100), roles=[SimpleNamespace(id=1)])
        after = SimpleNamespace(guild=SimpleNamespace(id=100), roles=[SimpleNamespace(id=1), SimpleNamespace(id=55)])

        await cog.on_member_update(before, after)

        cog._send_single_use_invite.assert_awaited_once()
        self.assertEqual(cog._send_single_use_invite.await_args.kwargs["member"], after)

    async def test_member_update_ignores_changes_outside_main_server(self) -> None:
        bot = MagicMock()
        cog = AutoInviteCog(bot)
        cog.config_store = SimpleNamespace(
            get_main_server_id=lambda: 100,
            get_role_mapping=lambda role_id: {"target_server_id": 200},
        )
        cog._send_single_use_invite = AsyncMock()

        before = SimpleNamespace(guild=SimpleNamespace(id=999), roles=[])
        after = SimpleNamespace(guild=SimpleNamespace(id=999), roles=[SimpleNamespace(id=55)])

        await cog.on_member_update(before, after)

        cog._send_single_use_invite.assert_not_awaited()

    async def test_send_single_use_invite_creates_invite_and_dms_member(self) -> None:
        bot = MagicMock()
        cog = AutoInviteCog(bot)

        invite_channel = SimpleNamespace(create_invite=AsyncMock(return_value=SimpleNamespace(url="https://discord.gg/abc")))
        target_guild = SimpleNamespace(name="Target", get_channel=lambda _: None, text_channels=[invite_channel])
        bot.get_guild.return_value = target_guild

        member = SimpleNamespace(guild=SimpleNamespace(name="Main"), send=AsyncMock())

        await cog._send_single_use_invite(member=member, mapping={"target_server_id": 200})

        invite_channel.create_invite.assert_awaited_once()
        sent_embed = member.send.await_args.kwargs["embed"]
        self.assertEqual(sent_embed.title, "congratulations")
        self.assertIn("https://discord.gg/abc", sent_embed.description)


if __name__ == "__main__":
    unittest.main()
