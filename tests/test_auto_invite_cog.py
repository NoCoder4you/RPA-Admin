"""Unit tests for one-time invite DM behavior after role assignment."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

try:
    from COGS.AutoInviteCog import AutoInviteCog, AutoInviteConfigStore
except ModuleNotFoundError:
    AutoInviteCog = None
    AutoInviteConfigStore = None


@unittest.skipIf(AutoInviteCog is None, "discord.py is not installed in the test environment")
class AutoInviteCogTests(unittest.IsolatedAsyncioTestCase):
    """Validate that mapped role grants create and DM single-use invite links."""

    async def test_member_update_triggers_invites_for_each_new_mapped_role(self) -> None:
        bot = MagicMock()
        cog = AutoInviteCog(bot)
        cog.config_store = SimpleNamespace(
            get_role_mappings=lambda *, main_server_id, role_id: (
                [{"target_server_id": 200}, {"target_server_id": 201}] if (main_server_id, role_id) == (100, 55) else []
            ),
        )
        cog._send_single_use_invite = AsyncMock()

        before = SimpleNamespace(guild=SimpleNamespace(id=100), roles=[SimpleNamespace(id=1, name="Member")])
        after = SimpleNamespace(
            guild=SimpleNamespace(id=100),
            roles=[SimpleNamespace(id=1, name="Member"), SimpleNamespace(id=55, name="Operators")],
        )

        await cog.on_member_update(before, after)

        self.assertEqual(cog._send_single_use_invite.await_count, 2)
        self.assertEqual(cog._send_single_use_invite.await_args_list[0].kwargs["member"], after)
        self.assertEqual(cog._send_single_use_invite.await_args_list[0].kwargs["triggering_role"].name, "Operators")
        self.assertEqual(cog._send_single_use_invite.await_args_list[1].kwargs["mapping"]["target_server_id"], 201)

    async def test_member_update_ignores_changes_outside_main_server(self) -> None:
        bot = MagicMock()
        cog = AutoInviteCog(bot)
        cog.config_store = SimpleNamespace(
            get_role_mappings=lambda *, main_server_id, role_id: [],
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

        await cog._send_single_use_invite(
            member=member,
            mapping={"target_server_id": 200, "target_server_name": "Operations Hub"},
            triggering_role=SimpleNamespace(name="Operations"),
        )

        invite_channel.create_invite.assert_awaited_once_with(
            max_uses=1,
            max_age=0,
            unique=True,
            reason="Auto invite for role assignment in Main",
        )
        sent_embed = member.send.await_args.kwargs["embed"]
        self.assertEqual(sent_embed.title, "Your server invite is ready")
        self.assertIn("https://discord.gg/abc", sent_embed.description)
        self.assertIn("Operations Hub", sent_embed.description)
        self.assertIn("Operations", sent_embed.description)
        self.assertIn("single-use", sent_embed.description)
        self.assertIn("stays valid until you redeem it", sent_embed.description)

    async def test_send_single_use_invite_skips_blocked_channel_and_uses_allowed_channel(self) -> None:
        bot = MagicMock()
        cog = AutoInviteCog(bot)

        blocked_channel = MagicMock()
        blocked_channel.create_invite = AsyncMock()
        blocked_channel.permissions_for.return_value = SimpleNamespace(create_instant_invite=False)

        allowed_channel = MagicMock()
        allowed_channel.create_invite = AsyncMock(return_value=SimpleNamespace(url="https://discord.gg/allowed"))
        allowed_channel.permissions_for.return_value = SimpleNamespace(create_instant_invite=True)

        target_guild = SimpleNamespace(
            name="Target",
            me=SimpleNamespace(id=123),
            get_channel=lambda channel_id: blocked_channel if channel_id == 999 else None,
            text_channels=[blocked_channel, allowed_channel],
        )
        bot.get_guild.return_value = target_guild

        member = SimpleNamespace(guild=SimpleNamespace(name="Main"), send=AsyncMock())

        await cog._send_single_use_invite(
            member=member,
            mapping={"target_server_id": 200, "target_channel_id": 999},
            triggering_role=SimpleNamespace(name="Operations"),
        )

        blocked_channel.create_invite.assert_not_awaited()
        allowed_channel.create_invite.assert_awaited_once()
        member.send.assert_awaited_once()


@unittest.skipIf(AutoInviteConfigStore is None, "discord.py is not installed in the test environment")
class AutoInviteConfigStoreTests(unittest.TestCase):
    """Validate InterlinkedRoles-backed auto-invite configuration loading."""

    def test_reads_main_server_and_multiple_role_mappings_from_interlinkedroles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "InterlinkedRoles.json"
            file_path.write_text(
                json.dumps(
                    [
                        {
                            "main_server_id": "100",
                            "main_server_role_id": "55",
                            "special_unit_server_id": "200",
                            "target_channel_id": "300",
                        },
                        {
                            "main_server_id": 100,
                            "main_server_role_id": 55,
                            "special_unit_server_id": 201,
                        },
                        {
                            "main_server_id": 101,
                            "main_server_role_id": 55,
                            "special_unit_server_id": 999,
                        },
                        {
                            "main_server_id": 100,
                            "main_server_role_id": 77,
                            "special_unit_server_id": 202,
                        },
                    ]
                ),
                encoding="utf-8",
            )

            store = AutoInviteConfigStore(config_path=file_path)

            self.assertEqual(store.get_main_server_id(), 100)
            self.assertEqual(len(store.get_role_mappings(main_server_id=100, role_id=55)), 2)
            self.assertEqual(store.get_role_mappings(main_server_id=100, role_id=55)[0]["target_channel_id"], "300")
            self.assertEqual(store.get_role_mappings(main_server_id=100, role_id=77)[0]["target_server_id"], 202)
            self.assertEqual(store.get_role_mappings(main_server_id=999, role_id=55), [])


if __name__ == "__main__":
    unittest.main()
