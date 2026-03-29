"""Tests for the pay announcement scheduler cog."""

from __future__ import annotations

import sys
import types
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

def _install_discord_stubs() -> bool:
    """Install a tiny discord/discord.ext stub so tests run without discord.py."""

    if "discord" in sys.modules:
        return False

    discord_module = types.ModuleType("discord")
    ext_module = types.ModuleType("discord.ext")
    commands_module = types.ModuleType("discord.ext.commands")
    tasks_module = types.ModuleType("discord.ext.tasks")

    class _FakeCog:
        """Minimal stand-in for discord.ext.commands.Cog used by class inheritance."""

    class _FakeBot:
        """Minimal Bot type used only for type hints in these tests."""

    class _LoopDescriptor:
        """Mimic discord.ext.tasks.loop descriptor API used by the cog."""

        def __init__(self, func):
            self._func = func
            self._before_loop = None

        def __get__(self, instance, owner):
            if instance is None:
                return self
            return _BoundLoop(self, instance)

        def before_loop(self, coroutine):
            self._before_loop = coroutine
            return coroutine

        def start(self):
            return None

        def cancel(self):
            return None

    class _BoundLoop:
        def __init__(self, descriptor, instance):
            self._descriptor = descriptor
            self._instance = instance

        async def __call__(self, *args, **kwargs):
            return await self._descriptor._func(self._instance, *args, **kwargs)

        def start(self):
            return None

        def cancel(self):
            return None

    def _loop(*_args, **_kwargs):
        def decorator(func):
            return _LoopDescriptor(func)
        return decorator

    commands_module.Cog = _FakeCog
    commands_module.Bot = _FakeBot
    tasks_module.loop = _loop
    ext_module.commands = commands_module
    ext_module.tasks = tasks_module
    discord_module.ext = ext_module

    sys.modules["discord"] = discord_module
    sys.modules["discord.ext"] = ext_module
    sys.modules["discord.ext.commands"] = commands_module
    sys.modules["discord.ext.tasks"] = tasks_module
    return True


_STUBS_INSTALLED = _install_discord_stubs()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from COGS.PayAnnounceCog import EASTERN_TZ, PayAnnounceCog

# Remove temporary stubs so they do not impact other test modules when the
# full test suite is collected in the same Python process.
if _STUBS_INSTALLED:
    for module_name in ("discord.ext.tasks", "discord.ext.commands", "discord.ext", "discord"):
        sys.modules.pop(module_name, None)


class PayAnnounceCogTests(unittest.IsolatedAsyncioTestCase):
    """Verify schedule matching and message rendering behavior."""

    def setUp(self) -> None:
        # Avoid starting the background task loop during unit tests.
        self._start_patcher = unittest.mock.patch.object(PayAnnounceCog._pay_schedule_checker, "start")
        self._start_mock = self._start_patcher.start()
        self.addCleanup(self._start_patcher.stop)

    def test_due_window_matches_expected_prestart_minute(self) -> None:
        due = PayAnnounceCog._due_window(datetime(2026, 3, 29, 11, 45, tzinfo=EASTERN_TZ))
        self.assertEqual(due, "12:00 PM")

    def test_due_window_is_none_for_non_schedule_minute(self) -> None:
        due = PayAnnounceCog._due_window(datetime(2026, 3, 29, 11, 44, tzinfo=EASTERN_TZ))
        self.assertIsNone(due)

    def test_load_announcement_channel_id_from_explicit_config_path(self) -> None:
        bot = MagicMock()
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "serverconfig.json"
            config_path.write_text('{\"payannounce_channel_id\": \"12345\"}', encoding="utf-8")
            cog = PayAnnounceCog(bot, config_path=config_path)

        self.assertEqual(cog.announcement_channel_id, 12345)
        self.assertEqual(cog.pay_role_id, "1487622625537560657")

    def test_load_single_shared_pay_role_id_from_config(self) -> None:
        bot = MagicMock()
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "serverconfig.json"
            config_path.write_text(
                '{\"channels\": {\"payannounce\": \"12345\"}, \"roles\": {\"payannounce\": \"98765\"}}',
                encoding="utf-8",
            )
            cog = PayAnnounceCog(bot, config_path=config_path)

        self.assertEqual(cog.pay_role_id, "98765")

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

        cog.pay_role_id = "42"
        await cog._send_announcement("12:00 PM")

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

        cog.pay_role_id = "84"
        await cog._send_announcement("1:00 PM")

        channel.send.assert_awaited_once()
        sent_text = channel.send.await_args.args[0]
        self.assertIn("<:RPA:1484696606111699166>", sent_text)
        self.assertIn("<@&84>", sent_text)

    async def test_send_announcement_publishes_when_channel_is_news(self) -> None:
        bot = MagicMock()
        cog = PayAnnounceCog(bot)
        cog.announcement_channel_id = 123

        sent_message = SimpleNamespace(publish=AsyncMock())
        channel = SimpleNamespace(
            guild=SimpleNamespace(
                me=SimpleNamespace(guild_permissions=SimpleNamespace(use_external_emojis=False))
            ),
            is_news=MagicMock(return_value=True),
            send=AsyncMock(return_value=sent_message),
        )
        bot.get_channel.return_value = channel

        await cog._send_announcement("6:00 PM")

        channel.send.assert_awaited_once()
        sent_message.publish.assert_awaited_once()
