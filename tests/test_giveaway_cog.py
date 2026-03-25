"""Unit tests for the giveaway management cog."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

try:
    import discord
    from COGS.MiscGiveaway import GIVEAWAY_CHANNEL_ID, GiveawayCog, GiveawayRecord
except Exception:
    discord = None
    GIVEAWAY_CHANNEL_ID = 1479462940825489408
    GiveawayCog = None
    GiveawayRecord = None


@unittest.skipIf(GiveawayCog is None or discord is None, "discord.py is not installed in the test environment")
class GiveawayCogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.storage_path = Path(self.tempdir.name) / "giveaways.json"
        self.bot = MagicMock()
        self.bot.add_view = MagicMock()
        self.bot.get_channel = MagicMock(return_value=None)
        self.bot.fetch_channel = AsyncMock()
        self.bot.get_guild = MagicMock(return_value=None)
        self.cog = GiveawayCog(self.bot, storage_path=self.storage_path)
        self.cog._restored.set()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _record(self, **overrides):
        base = dict(
            message_id=123,
            channel_id=456,
            guild_id=789,
            prize="Nitro",
            host_id=321,
            end_time=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
            winner_count=1,
            entrants=[],
            ended=False,
        )
        base.update(overrides)
        return GiveawayRecord(**base)

    async def test_save_and_load_records_round_trip(self) -> None:
        record = self._record(entrants=[1, 2, 3], role_requirement_id=44)
        self.cog._giveaways[record.message_id] = record

        await self.cog._save_records()
        loaded = await self.cog._load_records_from_disk()

        self.assertIn(record.message_id, loaded)
        self.assertEqual(loaded[record.message_id].entrants, [1, 2, 3])
        self.assertEqual(loaded[record.message_id].role_requirement_id, 44)

    async def test_load_records_handles_corrupted_json(self) -> None:
        self.storage_path.write_text("{not valid json", encoding="utf-8")

        loaded = await self.cog._load_records_from_disk()

        self.assertEqual(loaded, {})
        self.assertTrue(self.storage_path.with_suffix(".corrupted.json").exists())

    def test_build_requirements_text_without_optional_requirements(self) -> None:
        record = self._record()

        requirements = self.cog._build_requirements_text(record)

        self.assertEqual(requirements, "No special requirements")

    def test_pick_winners_filters_members_that_fail_requirements(self) -> None:
        record = self._record(entrants=[1, 2, 3], winner_count=2, role_requirement_id=999)
        eligible_role = SimpleNamespace(id=999)
        member_one = SimpleNamespace(roles=[eligible_role], created_at=datetime.now(timezone.utc) - timedelta(days=100), joined_at=datetime.now(timezone.utc) - timedelta(days=100))
        member_two = SimpleNamespace(roles=[], created_at=datetime.now(timezone.utc) - timedelta(days=100), joined_at=datetime.now(timezone.utc) - timedelta(days=100))
        member_three = SimpleNamespace(roles=[eligible_role], created_at=datetime.now(timezone.utc) - timedelta(days=100), joined_at=datetime.now(timezone.utc) - timedelta(days=100))
        guild = SimpleNamespace(get_member=lambda user_id: {1: member_one, 2: member_two, 3: member_three}.get(user_id))

        with patch("COGS.GiveawayCog.random.sample", side_effect=lambda values, k: values[:k]):
            winners = self.cog._pick_winners(record, guild=guild)

        self.assertEqual(winners, [1, 3])

    async def test_handle_entry_prevents_duplicate_entries(self) -> None:
        record = self._record(entrants=[55])
        self.cog._giveaways[record.message_id] = record
        interaction = SimpleNamespace(
            guild=SimpleNamespace(),
            user=SimpleNamespace(id=55),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        await self.cog.handle_entry(interaction, record.message_id)

        interaction.response.send_message.assert_awaited_once()
        sent_embed = interaction.response.send_message.await_args.kwargs["embed"]
        self.assertEqual(sent_embed.title, "Already Entered")

    async def test_giveaway_list_shows_active_giveaways(self) -> None:
        active = self._record(message_id=1001, channel_id=GIVEAWAY_CHANNEL_ID, prize="VIP")
        ended = self._record(message_id=1002, prize="Ended", ended=True)
        self.cog._giveaways = {1001: active, 1002: ended}
        member_permissions = SimpleNamespace(manage_guild=True, manage_messages=False)
        interaction = SimpleNamespace(
            guild=SimpleNamespace(),
            user=SimpleNamespace(guild_permissions=member_permissions),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        await self.cog.giveaway_list.callback(self.cog, interaction)

        interaction.response.send_message.assert_awaited_once()
        embed = interaction.response.send_message.await_args.kwargs["embed"]
        self.assertEqual(embed.title, "Active Giveaways")
        self.assertEqual(len(embed.fields), 1)
        self.assertIn("1001", embed.fields[0].value)

    async def test_giveaway_start_rejects_invalid_duration(self) -> None:
        member_permissions = SimpleNamespace(manage_guild=False, manage_messages=True)
        interaction = SimpleNamespace(
            guild=SimpleNamespace(get_channel=lambda _channel_id: None, id=789),
            user=SimpleNamespace(id=321, guild_permissions=member_permissions),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        await self.cog.giveaway_start.callback(self.cog, interaction, "Prize", 0, 1, None, None, None)

        interaction.response.send_message.assert_awaited_once()
        embed = interaction.response.send_message.await_args.kwargs["embed"]
        self.assertEqual(embed.title, "Invalid Giveaway Settings")


if __name__ == "__main__":
    unittest.main()
