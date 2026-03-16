"""Unit tests for the `/mute` moderation slash command cog."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

try:
    from COGS.MuteCog import MuteCog
except ModuleNotFoundError:
    MuteCog = None


@unittest.skipIf(MuteCog is None, "discord.py is not installed in the test environment")
class MuteCogTests(unittest.IsolatedAsyncioTestCase):
    """Validate moderation outcomes for the mute slash command."""

    async def test_mute_successfully_times_out_target_member(self) -> None:
        bot = MagicMock()
        cog = MuteCog(bot)

        target_member = SimpleNamespace(
            id=202,
            mention="<@202>",
            top_role=1,
            timeout=AsyncMock(),
        )
        invoking_member = SimpleNamespace(id=101, top_role=5, mention="<@101>")
        bot_member = SimpleNamespace(top_role=10)

        audit_channel = SimpleNamespace(send=AsyncMock())
        guild = SimpleNamespace(
            id=777,
            owner_id=999,
            me=bot_member,
            get_channel=MagicMock(return_value=audit_channel),
        )

        interaction = SimpleNamespace(
            user=invoking_member,
            guild=guild,
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            cog.mute_log_path = Path(tmp_dir) / "mute_timeouts.json"
            cog.server_config_store = SimpleNamespace(get_audit_channel_id=MagicMock(return_value=1234))

            await cog.mute.callback(cog, interaction, target_member, "10m", "cool off")

            target_member.timeout.assert_awaited_once()
            timeout_until = target_member.timeout.await_args.args[0]
            timeout_reason = target_member.timeout.await_args.kwargs.get("reason", "")

            self.assertIsInstance(timeout_until, datetime)
            self.assertGreater(timeout_until, datetime.now(timezone.utc))
            self.assertTrue(timeout_reason.endswith(" - cool off"))

            # Ensure a moderation embed is sent into the configured audit channel.
            audit_channel.send.assert_awaited_once()

            interaction.response.send_message.assert_awaited_once_with(
                "🔇 Muted <@202> for `10m`. Reason: cool off",
                ephemeral=True,
            )

            with cog.mute_log_path.open("r", encoding="utf-8") as mute_log_file:
                records = json.load(mute_log_file)

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["guild_id"], "777")
            self.assertEqual(records[0]["member_id"], "202")
            self.assertEqual(records[0]["moderator_id"], "101")
            self.assertEqual(records[0]["requested_length"], "10m")
            self.assertEqual(records[0]["reason"], "cool off")
            self.assertIn("start_time", records[0])
            self.assertIn("end_time", records[0])

    async def test_mute_rejects_invalid_duration_format(self) -> None:
        bot = MagicMock()
        cog = MuteCog(bot)

        target_member = SimpleNamespace(id=202, mention="<@202>", top_role=1, timeout=AsyncMock())
        invoking_member = SimpleNamespace(id=101, top_role=5, mention="<@101>")

        interaction = SimpleNamespace(
            user=invoking_member,
            guild=SimpleNamespace(
                id=777,
                owner_id=999,
                me=SimpleNamespace(top_role=10),
                get_channel=MagicMock(return_value=None),
            ),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        await cog.mute.callback(cog, interaction, target_member, "banana", "bad behavior")

        target_member.timeout.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once_with(
            "Invalid `lengthoftime`. Use formats like `10m`, `2h`, `3d`, or `1w`.",
            ephemeral=True,
        )


if __name__ == "__main__":
    unittest.main()
