"""Tests for the pay announcement scheduler cog."""

from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

try:
    from COGS.PayAnnounceCog import EASTERN_TZ, PayAnnounceCog
except ModuleNotFoundError:
    EASTERN_TZ = None
    PayAnnounceCog = None


@unittest.skipIf(PayAnnounceCog is None, "discord.py is not installed in the test environment")
class PayAnnounceCogTests(unittest.IsolatedAsyncioTestCase):
    """Verify schedule matching and message rendering behavior."""

    def setUp(self) -> None:
        # Avoid starting the background task loop during unit tests.
        self._start_patcher = unittest.mock.patch.object(PayAnnounceCog._pay_schedule_checker, "start")
        self._start_mock = self._start_patcher.start()
        self.addCleanup(self._start_patcher.stop)

    def test_due_window_matches_expected_prestart_minute(self) -> None:
        due = PayAnnounceCog._due_window(datetime(2026, 3, 29, 11, 45, tzinfo=EASTERN_TZ))
        self.assertEqual(due, ("12:00 PM", "1378512513729040507"))

    def test_due_window_is_none_for_non_schedule_minute(self) -> None:
        due = PayAnnounceCog._due_window(datetime(2026, 3, 29, 11, 44, tzinfo=EASTERN_TZ))
        self.assertIsNone(due)

    async def test_send_announcement_uses_unicode_emoji_when_external_not_allowed(self) -> None:
        bot = MagicMock()
        cog = PayAnnounceCog(bot)
        cog.announcement_channel_id = 123

        channel = SimpleNamespace(
            guild=SimpleNamespace(
                me=SimpleNamespace(guild_permissions=SimpleNamespace(use_external_emojis=False))
            ),
            send=AsyncMock(),
        )
        bot.get_channel.return_value = channel

        await cog._send_announcement("12:00 PM", "42")

        channel.send.assert_awaited_once()
        sent_text = channel.send.await_args.args[0]
        self.assertIn("💰 Pay Time: 12:00 PM 💰", sent_text)
        self.assertIn("<@&42>", sent_text)

    async def test_send_announcement_uses_external_emoji_when_allowed(self) -> None:
        bot = MagicMock()
        cog = PayAnnounceCog(bot)
        cog.announcement_channel_id = 123

        channel = SimpleNamespace(
            guild=SimpleNamespace(
                me=SimpleNamespace(guild_permissions=SimpleNamespace(use_external_emojis=True))
            ),
            send=AsyncMock(),
        )
        bot.get_channel.return_value = channel

        await cog._send_announcement("1:00 PM", "84")

        channel.send.assert_awaited_once()
        sent_text = channel.send.await_args.args[0]
        self.assertIn("<:Pay:1305265714042765483>", sent_text)
        self.assertIn("<@&84>", sent_text)
