"""Unit tests for sterile channel bot-only message enforcement."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

try:
    from COGS.ServerSterileChannel import SterileChannelCog, SterileChannelStore
except ModuleNotFoundError:  # pragma: no cover - environment-dependent test skip
    SterileChannelCog = None
    SterileChannelStore = None


@unittest.skipIf(SterileChannelStore is None, "discord.py is not installed in the test environment")
class SterileChannelStoreTests(unittest.TestCase):
    """Validate sterile channel persistence semantics."""

    def test_add_and_remove_channel_round_trip(self) -> None:
        """Store should persist one channel and fully remove it when requested."""

        with tempfile.TemporaryDirectory() as temp_dir:
            store = SterileChannelStore(config_path=Path(temp_dir) / "SterileChannels.json")

            self.assertTrue(store.add_channel(100, 200))
            self.assertFalse(store.add_channel(100, 200))
            self.assertEqual(store.get_channels(100), {200})

            self.assertTrue(store.remove_channel(100, 200))
            self.assertFalse(store.remove_channel(100, 200))
            self.assertEqual(store.get_channels(100), set())


@unittest.skipIf(SterileChannelCog is None, "discord.py is not installed in the test environment")
class SterileChannelCogTests(unittest.IsolatedAsyncioTestCase):
    """Ensure only user messages in configured channels are deleted."""

    async def test_on_message_deletes_user_message_in_sterile_channel(self) -> None:
        """Non-bot messages in sterile channels should be removed."""

        cog = SterileChannelCog.__new__(SterileChannelCog)
        cog.bot = SimpleNamespace()
        cog.store = SimpleNamespace(get_channels=lambda _guild_id: {123})

        message = SimpleNamespace(
            author=SimpleNamespace(bot=False),
            guild=SimpleNamespace(id=999),
            channel=SimpleNamespace(id=123),
            delete=AsyncMock(),
        )

        await cog.on_message(message)

        message.delete.assert_awaited_once()

    async def test_on_message_ignores_bot_messages_even_in_sterile_channel(self) -> None:
        """Bot-authored messages should never be deleted by sterile enforcement."""

        cog = SterileChannelCog.__new__(SterileChannelCog)
        cog.bot = SimpleNamespace()
        cog.store = SimpleNamespace(get_channels=lambda _guild_id: {123})

        message = SimpleNamespace(
            author=SimpleNamespace(bot=True),
            guild=SimpleNamespace(id=999),
            channel=SimpleNamespace(id=123),
            delete=AsyncMock(),
        )

        await cog.on_message(message)

        message.delete.assert_not_awaited()

    async def test_on_message_ignores_user_message_outside_sterile_channel(self) -> None:
        """User messages in normal channels should be left untouched."""

        cog = SterileChannelCog.__new__(SterileChannelCog)
        cog.bot = SimpleNamespace()
        cog.store = SimpleNamespace(get_channels=lambda _guild_id: {456})

        message = SimpleNamespace(
            author=SimpleNamespace(bot=False),
            guild=SimpleNamespace(id=999),
            channel=SimpleNamespace(id=123),
            delete=AsyncMock(),
        )

        await cog.on_message(message)

        message.delete.assert_not_awaited()

    async def test_text_command_add_and_list_channels(self) -> None:
        """Text commands should store channels and report the configured sterile list."""

        cog = SterileChannelCog.__new__(SterileChannelCog)
        cog.bot = SimpleNamespace()
        cog.store = SimpleNamespace(add_channel=lambda _gid, _cid: True, get_channels=lambda _gid: {123, 456})

        ctx = SimpleNamespace(guild=SimpleNamespace(id=999), send=AsyncMock())
        channel_id = 123

        await SterileChannelCog.sterile_add.callback(cog, ctx, channel_id)
        await SterileChannelCog.sterile_list.callback(cog, ctx)

        self.assertEqual(ctx.send.await_count, 2)
        first_call_message = ctx.send.await_args_list[0].args[0]
        second_call_message = ctx.send.await_args_list[1].args[0]
        self.assertIn("now a sterile channel", first_call_message)
        self.assertIn("<#123>", second_call_message)
        self.assertIn("<#456>", second_call_message)

    async def test_text_command_remove_channel(self) -> None:
        """Removing a sterile channel through text command should return a success notice."""

        cog = SterileChannelCog.__new__(SterileChannelCog)
        cog.bot = SimpleNamespace()
        cog.store = SimpleNamespace(remove_channel=lambda _gid, _cid: True)

        ctx = SimpleNamespace(guild=SimpleNamespace(id=999), send=AsyncMock())
        channel_id = 123

        await SterileChannelCog.sterile_remove.callback(cog, ctx, channel_id)

        ctx.send.assert_awaited_once()
        self.assertIn("Removed sterile mode", ctx.send.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
