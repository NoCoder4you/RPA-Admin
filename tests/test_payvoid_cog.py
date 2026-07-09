"""Unit tests for the `/void` discipline cog."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

try:
    import discord
    from discord.ext import commands
    from COGS.PayVoidCog import (
        PAYBAN_MENTION_ROLE_ID,
        PAYVOID_THRESHOLD,
        PayDisciplineStore,
        PayVoidCog,
        RPA_SERVER_ID,
    )
except ModuleNotFoundError:
    discord = None
    commands = None
    PayVoidCog = None
    PayDisciplineStore = None
    RPA_SERVER_ID = None
    PAYVOID_THRESHOLD = None
    PAYBAN_MENTION_ROLE_ID = None


@unittest.skipIf(PayVoidCog is None, "discord.py is not installed in the test environment")
class PayDisciplineStoreTests(unittest.TestCase):
    """Validate separate pay void and payban JSON-backed state."""

    def test_third_weekly_void_creates_first_24_hour_payban_in_ban_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = PayDisciplineStore(temp_path / "payvoids.json", temp_path / "paybans.json")
            now = datetime(2026, 7, 7, 12, tzinfo=timezone.utc)

            first = store.record_void(10, 1, now)
            second = store.record_void(10, 1, now + timedelta(days=1))
            third = store.record_void(10, 1, now + timedelta(days=2))

            self.assertEqual(first.void_count, 1)
            self.assertEqual(second.void_count, 2)
            self.assertEqual(third.void_count, PAYVOID_THRESHOLD)
            self.assertEqual(third.payban_offence_count, 1)
            self.assertEqual(third.payban_until, now + timedelta(days=3))
            self.assertIn("10", store.voids.data["members"])
            self.assertNotIn("reason", store.voids.data["members"]["10"]["voids"][0])
            self.assertNotIn("reason", store.bans.data["members"]["10"])
            self.assertEqual(store.bans.data["members"]["10"]["offences"], 1)

    def test_payban_duration_escalates_and_caps_at_72_hours(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = PayDisciplineStore(temp_path / "payvoids.json", temp_path / "paybans.json")
            now = datetime(2026, 7, 7, 12, tzinfo=timezone.utc)

            decisions = []
            for offence in range(4):
                base = now + timedelta(days=offence)
                store.record_void(10, 1, base)
                store.record_void(10, 1, base + timedelta(hours=1))
                decisions.append(store.record_void(10, 1, base + timedelta(hours=2)))

            self.assertEqual(decisions[0].payban_until, now + timedelta(hours=26))
            self.assertEqual(decisions[1].payban_until, now + timedelta(days=1, hours=50))
            self.assertEqual(decisions[2].payban_until, now + timedelta(days=2, hours=74))
            self.assertEqual(decisions[3].payban_until, now + timedelta(days=3, hours=74))

    def test_reset_week_clears_voids_and_bans_but_records_reset_monday(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = PayDisciplineStore(temp_path / "payvoids.json", temp_path / "paybans.json")
            now = datetime(2026, 7, 7, 12, tzinfo=timezone.utc)
            store.record_void(10, 1, now)
            reset_monday = datetime(2026, 7, 13, 0, tzinfo=ZoneInfo("America/New_York"))

            store.reset_week(reset_monday)

            self.assertEqual(store.voids.data["members"], {})
            self.assertEqual(store.bans.data["members"], {})
            self.assertTrue(store.has_reset_for(reset_monday))


@unittest.skipIf(PayVoidCog is None, "discord.py is not installed in the test environment")
class PayVoidCogTests(unittest.IsolatedAsyncioTestCase):
    """Validate slash command behavior without calling Discord."""

    def _cog(self, store: PayDisciplineStore) -> PayVoidCog:
        bot = MagicMock()
        bot.wait_until_ready = AsyncMock()
        with patch("COGS.PayVoidCog.PayVoidCog._weekly_reset_checker"):
            return PayVoidCog(bot, store=store)


    async def test_extension_loads_payvoid_cog(self) -> None:
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
        try:
            await bot.load_extension("COGS.PayVoidCog")
            self.assertIn("PayVoidCog", bot.cogs)
        finally:
            await bot.close()

    def test_void_command_is_globally_syncable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            cog = self._cog(PayDisciplineStore(temp_path / "payvoids.json", temp_path / "paybans.json"))

            self.assertIsNone(cog.void._guild_ids)

    def test_void_command_has_no_permission_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            cog = self._cog(PayDisciplineStore(temp_path / "payvoids.json", temp_path / "paybans.json"))

            self.assertEqual(cog.void.checks, [])

    async def test_void_rejects_other_servers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            cog = self._cog(PayDisciplineStore(temp_path / "payvoids.json", temp_path / "paybans.json"))
            interaction = SimpleNamespace(
                guild=SimpleNamespace(id=123),
                user=SimpleNamespace(id=1),
                response=SimpleNamespace(send_message=AsyncMock()),
            )
            member = SimpleNamespace(id=10, mention="<@10>")

            await cog.void.callback(cog, interaction, "Voidable User")

            interaction.response.send_message.assert_awaited_once_with(
                "This command is only available in the RPA server.", ephemeral=True
            )


    async def test_void_rejects_unknown_text_username(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            cog = self._cog(PayDisciplineStore(temp_path / "payvoids.json", temp_path / "paybans.json"))
            interaction = SimpleNamespace(
                guild=SimpleNamespace(id=RPA_SERVER_ID, members=[]),
                user=SimpleNamespace(id=1),
                response=SimpleNamespace(send_message=AsyncMock()),
            )

            await cog.void.callback(cog, interaction, "Missing User")

            interaction.response.send_message.assert_awaited_once_with(
                "I could not find a server member named `Missing User`. Please use their exact username or display name.",
                ephemeral=True,
            )

    async def test_void_posts_embed_and_does_not_add_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = PayDisciplineStore(temp_path / "payvoids.json", temp_path / "paybans.json")
            cog = self._cog(store)
            cog._now = MagicMock(return_value=datetime(2026, 7, 7, 12, tzinfo=timezone.utc))

            member = SimpleNamespace(id=10, mention="<@10>", display_name="Voidable User", add_roles=AsyncMock())
            interaction = SimpleNamespace(
                guild=SimpleNamespace(id=RPA_SERVER_ID, members=[member]),
                user=SimpleNamespace(id=1),
                response=SimpleNamespace(send_message=AsyncMock()),
            )

            await cog.void.callback(cog, interaction, "Voidable User")

            member.add_roles.assert_not_awaited()
            send_kwargs = interaction.response.send_message.await_args.kwargs
            self.assertIsNone(send_kwargs["content"])
            self.assertEqual(send_kwargs["embed"].title, "Pay Void Recorded")
            self.assertEqual(send_kwargs["embed"].fields[0].value, "Voidable User")
            self.assertEqual(send_kwargs["embed"].fields[1].value, "1")

    async def test_void_third_void_mentions_payban_role_without_assigning_role(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = PayDisciplineStore(temp_path / "payvoids.json", temp_path / "paybans.json")
            cog = self._cog(store)
            cog._now = MagicMock(return_value=datetime(2026, 7, 7, 12, tzinfo=timezone.utc))

            member = SimpleNamespace(id=10, mention="<@10>", display_name="Voidable User", add_roles=AsyncMock())
            interaction = SimpleNamespace(
                guild=SimpleNamespace(id=RPA_SERVER_ID, members=[member]),
                user=SimpleNamespace(id=1),
                response=SimpleNamespace(send_message=AsyncMock()),
            )

            await cog.void.callback(cog, interaction, "Voidable User")
            await cog.void.callback(cog, interaction, "Voidable User")
            await cog.void.callback(cog, interaction, "Voidable User")

            member.add_roles.assert_not_awaited()
            send_kwargs = interaction.response.send_message.await_args.kwargs
            self.assertEqual(send_kwargs["content"], f"<@&{PAYBAN_MENTION_ROLE_ID}>")
            self.assertEqual(send_kwargs["embed"].title, "Payban Issued")
            self.assertEqual(send_kwargs["embed"].fields[1].value, "3")

    async def test_weekly_reset_posts_reset_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = PayDisciplineStore(temp_path / "payvoids.json", temp_path / "paybans.json")
            cog = self._cog(store)
            cog._now = MagicMock(return_value=datetime(2026, 7, 13, 4, 0, tzinfo=timezone.utc))
            channel = SimpleNamespace(send=AsyncMock())
            cog.bot.get_channel.return_value = channel

            await cog._weekly_reset_checker.coro(cog)

            self.assertEqual(store.voids.data["members"], {})
            self.assertEqual(store.bans.data["members"], {})
            channel.send.assert_awaited_once_with("Pay voids and paybans have been reset for the week.")


if __name__ == "__main__":
    unittest.main()
