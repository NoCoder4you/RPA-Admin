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
        PAY_RESET_ALLOWED_ROLE_ID,
        THIRD_PAYBAN_ALERT_ROLE_ID,
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
    PAY_RESET_ALLOWED_ROLE_ID = None
    THIRD_PAYBAN_ALERT_ROLE_ID = None


@unittest.skipIf(PayVoidCog is None, "discord.py is not installed in the test environment")
class PayDisciplineStoreTests(unittest.TestCase):
    """Validate separate pay void and payban JSON-backed state."""

    def test_third_weekly_void_creates_first_24_hour_payban_in_ban_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = PayDisciplineStore(temp_path / "payvoids.json", temp_path / "paybans.json")
            now = datetime(2026, 7, 7, 12, tzinfo=timezone.utc)

            first = store.record_void("HabboUser", 1, now, True)
            second = store.record_void("HabboUser", 1, now + timedelta(days=1), False)
            third = store.record_void("HabboUser", 1, now + timedelta(days=2), True)

            self.assertEqual(first.void_count, 1)
            self.assertEqual(second.void_count, 2)
            self.assertEqual(third.void_count, PAYVOID_THRESHOLD)
            self.assertEqual(third.payban_offence_count, 1)
            self.assertEqual(third.payban_until, now + timedelta(days=3))
            self.assertIn("habbouser", store.voids.data["members"])
            self.assertEqual(store.voids.data["members"]["habbouser"]["username"], "HabboUser")
            self.assertTrue(store.voids.data["members"]["habbouser"]["voids"][0]["actiontaken"])
            self.assertNotIn("reason", store.voids.data["members"]["habbouser"]["voids"][0])
            self.assertNotIn("reason", store.bans.data["members"]["habbouser"])
            self.assertEqual(store.bans.data["members"]["habbouser"]["offences"], 1)
            self.assertEqual(len(store.bans.data["members"]["habbouser"]["paybans"]), 1)

    def test_payban_duration_escalates_and_caps_at_72_hours(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = PayDisciplineStore(temp_path / "payvoids.json", temp_path / "paybans.json")
            now = datetime(2026, 7, 7, 12, tzinfo=timezone.utc)

            decisions = []
            for offence in range(4):
                base = now + timedelta(days=offence)
                store.record_void("HabboUser", 1, base, False)
                store.record_void("HabboUser", 1, base + timedelta(hours=1), False)
                decisions.append(store.record_void("HabboUser", 1, base + timedelta(hours=2), False))

            self.assertEqual(decisions[0].payban_until, now + timedelta(hours=26))
            self.assertEqual(decisions[1].payban_until, now + timedelta(days=1, hours=50))
            self.assertEqual(decisions[2].payban_until, now + timedelta(days=2, hours=74))
            self.assertEqual(decisions[3].payban_until, now + timedelta(days=3, hours=74))

    def test_reset_week_clears_voids_but_preserves_payban_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = PayDisciplineStore(temp_path / "payvoids.json", temp_path / "paybans.json")
            now = datetime(2026, 7, 7, 12, tzinfo=timezone.utc)
            store.record_void("HabboUser", 1, now, True)
            reset_monday = datetime(2026, 7, 13, 0, tzinfo=ZoneInfo("America/New_York"))

            store.reset_week(reset_monday)

            self.assertEqual(store.voids.data["members"], {})
            self.assertIn("habbouser", store.bans.data["members"])
            self.assertEqual(store.bans.data["members"]["habbouser"]["offences"], 0)
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

    def test_pay_command_group_is_globally_syncable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            cog = self._cog(PayDisciplineStore(temp_path / "payvoids.json", temp_path / "paybans.json"))

            self.assertEqual(cog.pay.name, "pay")
            self.assertIsNone(cog.pay._guild_ids)
            self.assertEqual({command.name for command in cog.pay.commands}, {"void", "reset"})

    def test_pay_subcommands_have_no_app_permission_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            cog = self._cog(PayDisciplineStore(temp_path / "payvoids.json", temp_path / "paybans.json"))

            self.assertEqual(cog.void.checks, [])
            self.assertEqual(cog.reset.checks, [])

    async def test_void_rejects_other_servers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            cog = self._cog(PayDisciplineStore(temp_path / "payvoids.json", temp_path / "paybans.json"))
            interaction = SimpleNamespace(
                guild=SimpleNamespace(id=123),
                user=SimpleNamespace(id=1),
                response=SimpleNamespace(send_message=AsyncMock()),
            )
            await cog.void.callback(cog, interaction, "Voidable User", "No")

            interaction.response.send_message.assert_awaited_once_with(
                "This command is only available in the RPA server.", ephemeral=True
            )

    async def test_void_accepts_habbo_username_that_is_not_a_server_member(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = PayDisciplineStore(temp_path / "payvoids.json", temp_path / "paybans.json")
            cog = self._cog(store)
            cog._now = MagicMock(return_value=datetime(2026, 7, 7, 12, tzinfo=timezone.utc))
            interaction = SimpleNamespace(
                guild=SimpleNamespace(id=RPA_SERVER_ID, members=[]),
                user=SimpleNamespace(id=1, display_name="Recorder Name"),
                response=SimpleNamespace(send_message=AsyncMock()),
            )

            await cog.void.callback(cog, interaction, "HabboOnly", "Yes")

            send_kwargs = interaction.response.send_message.await_args.kwargs
            self.assertEqual(send_kwargs["embed"].fields[0].value, "HabboOnly")
            self.assertEqual(send_kwargs["embed"].fields[1].name, "Voids")
            self.assertEqual(send_kwargs["embed"].fields[1].value, "1/3")
            self.assertEqual(send_kwargs["embed"].fields[2].name, "Action Taken")
            self.assertEqual(send_kwargs["embed"].fields[2].value, "Yes")
            self.assertEqual(send_kwargs["embed"].fields[3].name, "Paybans")
            self.assertEqual(send_kwargs["embed"].fields[3].value, "0/3")
            self.assertEqual(
                send_kwargs["embed"].footer.text,
                "Void Recorded By Recorder Name • 2026-07-07 12:00 UTC",
            )
            self.assertIn("habboonly", store.voids.data["members"])
            self.assertTrue(store.voids.data["members"]["habboonly"]["voids"][0]["actiontaken"])

    async def test_void_posts_embed_and_does_not_add_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = PayDisciplineStore(temp_path / "payvoids.json", temp_path / "paybans.json")
            cog = self._cog(store)
            cog._now = MagicMock(return_value=datetime(2026, 7, 7, 12, tzinfo=timezone.utc))

            interaction = SimpleNamespace(
                guild=SimpleNamespace(id=RPA_SERVER_ID, members=[]),
                user=SimpleNamespace(id=1, display_name="Recorder Name"),
                response=SimpleNamespace(send_message=AsyncMock()),
            )

            await cog.void.callback(cog, interaction, "Voidable User", "No")

            send_kwargs = interaction.response.send_message.await_args.kwargs
            self.assertIsNone(send_kwargs["content"])
            self.assertEqual(send_kwargs["embed"].title, "Pay Void Recorded")
            self.assertEqual(send_kwargs["embed"].fields[0].value, "Voidable User")
            self.assertEqual(send_kwargs["embed"].fields[1].name, "Voids")
            self.assertEqual(send_kwargs["embed"].fields[1].value, "1/3")
            self.assertEqual(send_kwargs["embed"].fields[2].name, "Action Taken")
            self.assertEqual(send_kwargs["embed"].fields[2].value, "No")
            self.assertEqual(send_kwargs["embed"].fields[3].name, "Paybans")
            self.assertEqual(send_kwargs["embed"].fields[3].value, "0/3")

    async def test_void_third_void_mentions_payban_role_without_assigning_role(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = PayDisciplineStore(temp_path / "payvoids.json", temp_path / "paybans.json")
            cog = self._cog(store)
            cog._now = MagicMock(return_value=datetime(2026, 7, 7, 12, tzinfo=timezone.utc))

            interaction = SimpleNamespace(
                guild=SimpleNamespace(id=RPA_SERVER_ID, members=[]),
                user=SimpleNamespace(id=1),
                response=SimpleNamespace(send_message=AsyncMock()),
            )

            await cog.void.callback(cog, interaction, "Voidable User", "No")
            await cog.void.callback(cog, interaction, "Voidable User", "No")
            await cog.void.callback(cog, interaction, "Voidable User", "No")

            send_kwargs = interaction.response.send_message.await_args.kwargs
            self.assertEqual(send_kwargs["content"], f"<@&{PAYBAN_MENTION_ROLE_ID}>")
            self.assertEqual(send_kwargs["embed"].title, "Payban Issued")
            self.assertEqual(send_kwargs["embed"].fields[1].name, "Voids")
            self.assertEqual(send_kwargs["embed"].fields[1].value, "3/3")
            self.assertEqual(send_kwargs["embed"].fields[2].name, "Action Taken")
            self.assertEqual(send_kwargs["embed"].fields[2].value, "No")
            self.assertEqual(send_kwargs["embed"].fields[3].name, "Paybans")
            self.assertEqual(send_kwargs["embed"].fields[3].value, "1/3")


    async def test_void_counter_rolls_back_to_one_after_ban(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = PayDisciplineStore(temp_path / "payvoids.json", temp_path / "paybans.json")
            cog = self._cog(store)
            cog._now = MagicMock(return_value=datetime(2026, 7, 7, 12, tzinfo=timezone.utc))
            interaction = SimpleNamespace(
                guild=SimpleNamespace(id=RPA_SERVER_ID, members=[]),
                user=SimpleNamespace(id=1),
                response=SimpleNamespace(send_message=AsyncMock()),
            )

            await cog.void.callback(cog, interaction, "Voidable User", "No")
            await cog.void.callback(cog, interaction, "Voidable User", "No")
            await cog.void.callback(cog, interaction, "Voidable User", "No")
            await cog.void.callback(cog, interaction, "Voidable User", "No")

            send_kwargs = interaction.response.send_message.await_args.kwargs
            self.assertIsNone(send_kwargs["content"])
            self.assertEqual(send_kwargs["embed"].fields[1].name, "Voids")
            self.assertEqual(send_kwargs["embed"].fields[1].value, "1/3")
            self.assertEqual(send_kwargs["embed"].fields[3].name, "Paybans")
            self.assertEqual(send_kwargs["embed"].fields[3].value, "1/3")

    async def test_third_payban_sends_escalation_alert_embed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = PayDisciplineStore(temp_path / "payvoids.json", temp_path / "paybans.json")
            cog = self._cog(store)
            cog._now = MagicMock(return_value=datetime(2026, 7, 7, 12, tzinfo=timezone.utc))
            alert_channel = SimpleNamespace(send=AsyncMock())
            cog.bot.get_channel.return_value = alert_channel
            interaction = SimpleNamespace(
                guild=SimpleNamespace(id=RPA_SERVER_ID, members=[]),
                user=SimpleNamespace(id=1),
                response=SimpleNamespace(send_message=AsyncMock()),
            )

            # Two historical paybans are kept in paybans.json indefinitely; the
            # next ban should be treated as the user's third lifetime payban.
            store.bans.data["members"] = {
                "voidable user": {
                    "username": "Voidable User",
                    "offences": 2,
                    "paybans": [
                        {"created_at": "2026-07-01T09:30:00+00:00"},
                        {"created_at": "2026-07-03T10:45:00+00:00"},
                    ],
                }
            }

            await cog.void.callback(cog, interaction, "Voidable User", "No")
            await cog.void.callback(cog, interaction, "Voidable User", "No")
            await cog.void.callback(cog, interaction, "Voidable User", "No")

            send_kwargs = interaction.response.send_message.await_args.kwargs
            self.assertEqual(send_kwargs["embed"].fields[3].name, "Paybans")
            self.assertEqual(send_kwargs["embed"].fields[3].value, "3/3")
            alert_channel.send.assert_awaited_once()
            alert_kwargs = alert_channel.send.await_args.kwargs
            self.assertEqual(alert_kwargs["content"], f"<@&{THIRD_PAYBAN_ALERT_ROLE_ID}>")
            self.assertEqual(alert_kwargs["embed"].title, "Third Payban Alert")
            self.assertEqual(alert_kwargs["embed"].fields[0].name, "Username")
            self.assertEqual(alert_kwargs["embed"].fields[0].value, "Voidable User")
            self.assertEqual(alert_kwargs["embed"].fields[1].name, "Pay Ban 1")
            self.assertEqual(alert_kwargs["embed"].fields[1].value, "2026-07-01 09:30 UTC")
            self.assertEqual(alert_kwargs["embed"].fields[2].name, "Pay Ban 2")
            self.assertEqual(alert_kwargs["embed"].fields[2].value, "2026-07-03 10:45 UTC")
            self.assertEqual(alert_kwargs["embed"].fields[3].name, "Pay Ban 3")
            self.assertEqual(alert_kwargs["embed"].fields[3].value, "2026-07-07 12:00 UTC")


    async def test_reset_rejects_users_without_reset_role(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = PayDisciplineStore(temp_path / "payvoids.json", temp_path / "paybans.json")
            store.bans.data["members"] = {"habboonly": {"username": "HabboOnly", "offences": 2}}
            cog = self._cog(store)
            interaction = SimpleNamespace(
                guild=SimpleNamespace(id=RPA_SERVER_ID),
                user=SimpleNamespace(id=1, roles=[]),
                response=SimpleNamespace(send_message=AsyncMock()),
            )

            await cog.reset.callback(cog, interaction, "HabboOnly")

            self.assertEqual(store.bans.data["members"]["habboonly"]["offences"], 2)
            interaction.response.send_message.assert_awaited_once_with(
                "You do not have permission to reset payban counters.", ephemeral=True
            )

    async def test_reset_clears_payban_counter_for_allowed_role(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = PayDisciplineStore(temp_path / "payvoids.json", temp_path / "paybans.json")
            store.bans.data["members"] = {
                "habboonly": {
                    "username": "HabboOnly",
                    "offences": 3,
                    "active_until": "2026-07-08T12:00:00+00:00",
                    "updated_at": "2026-07-07T12:00:00+00:00",
                }
            }
            cog = self._cog(store)
            interaction = SimpleNamespace(
                guild=SimpleNamespace(id=RPA_SERVER_ID),
                user=SimpleNamespace(id=1, roles=[SimpleNamespace(id=PAY_RESET_ALLOWED_ROLE_ID)]),
                response=SimpleNamespace(send_message=AsyncMock()),
            )

            await cog.reset.callback(cog, interaction, "HabboOnly")

            ban_record = store.bans.data["members"]["habboonly"]
            self.assertEqual(ban_record["offences"], 0)
            self.assertNotIn("active_until", ban_record)
            self.assertNotIn("updated_at", ban_record)
            self.assertEqual(ban_record["paybans"], [])
            interaction.response.send_message.assert_awaited_once_with(
                "Payban counter for `HabboOnly` has been reset.", ephemeral=True
            )

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
            channel.send.assert_awaited_once_with("Pay voids have been reset for the week.")

    async def test_weekly_reset_catches_up_after_monday_midnight_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = PayDisciplineStore(temp_path / "payvoids.json", temp_path / "paybans.json")
            store.record_void("HabboUser", 1, datetime(2026, 7, 12, 23, 0, tzinfo=timezone.utc), True)
            cog = self._cog(store)
            # 00:01 EST is already past the old exact-minute reset window; this
            # should still clear stale weekly state after downtime or loop drift.
            cog._now = MagicMock(return_value=datetime(2026, 7, 13, 4, 1, tzinfo=timezone.utc))
            channel = SimpleNamespace(send=AsyncMock())
            cog.bot.get_channel.return_value = channel

            await cog._weekly_reset_checker.coro(cog)

            self.assertEqual(store.voids.data["members"], {})
            self.assertTrue(store.has_reset_for(datetime(2026, 7, 13, 0, tzinfo=ZoneInfo("America/New_York"))))
            channel.send.assert_awaited_once_with("Pay voids have been reset for the week.")

    async def test_weekly_reset_does_not_repeat_after_current_week_was_reset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = PayDisciplineStore(temp_path / "payvoids.json", temp_path / "paybans.json")
            reset_monday = datetime(2026, 7, 13, 0, tzinfo=ZoneInfo("America/New_York"))
            store.reset_week(reset_monday)
            cog = self._cog(store)
            cog._now = MagicMock(return_value=datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc))
            channel = SimpleNamespace(send=AsyncMock())
            cog.bot.get_channel.return_value = channel

            await cog._weekly_reset_checker.coro(cog)

            channel.send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
