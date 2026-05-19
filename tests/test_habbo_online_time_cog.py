"""Unit tests for the `/onlinetime` Habbo slash command cog."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

try:
    import discord
    from COGS.HabboOnlineTimeCog import HabboOnlineTimeCog
except ModuleNotFoundError:
    discord = None
    HabboOnlineTimeCog = None


@unittest.skipIf(HabboOnlineTimeCog is None, "discord.py is not installed in the test environment")
class HabboOnlineTimeCogTests(unittest.IsolatedAsyncioTestCase):
    def _employee_member(self, user_id: int = 123) -> SimpleNamespace:
        return SimpleNamespace(
            id=user_id,
            roles=[SimpleNamespace(name="RPA Employee")],
            __str__=lambda self: "Tester#0001",
        )

    async def test_rejects_user_without_employee_role(self) -> None:
        cog = HabboOnlineTimeCog(MagicMock())
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            user=SimpleNamespace(id=123, roles=[SimpleNamespace(name="Guest")]),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        await cog.onlinetime.callback(cog, interaction, None)

        interaction.response.send_message.assert_awaited_once_with(
            "You do not have permission to use `/onlinetime`.", ephemeral=True
        )

    async def test_missing_verified_user_requires_manual_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            verified_path = Path(tmp_dir) / "VerifiedUsers.json"
            verified_path.write_text(json.dumps([]), encoding="utf-8")
            cog = HabboOnlineTimeCog(MagicMock(), verified_users_path=verified_path)

            interaction = SimpleNamespace(
                guild=SimpleNamespace(id=1),
                user=self._employee_member(),
                response=SimpleNamespace(defer=AsyncMock()),
                followup=SimpleNamespace(send=AsyncMock()),
            )

            await cog.onlinetime.callback(cog, interaction, None)

            interaction.followup.send.assert_awaited_once_with(
                "You are not verified yet. Please provide a Habbo username manually.",
                ephemeral=True,
            )

    async def test_successful_lookup_sends_embed(self) -> None:
        cog = HabboOnlineTimeCog(MagicMock())
        cog._fetch_habbo_profile = AsyncMock(
            return_value={
                "name": "Siren",
                "totalOnlineTime": 183600,
                "figureString": "hr-100-1.hd-180-1",
                "lastAccessTime": "2026-05-17T19:16:13.000+0000",
            }
        )

        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            user=self._employee_member(),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await cog.onlinetime.callback(cog, interaction, "Siren")

        interaction.response.defer.assert_awaited_once_with(ephemeral=False, thinking=True)
        interaction.followup.send.assert_awaited_once()
        send_kwargs = interaction.followup.send.await_args.kwargs
        self.assertFalse(send_kwargs["ephemeral"])
        embed = send_kwargs["embed"]
        self.assertIsInstance(embed, discord.Embed)
        self.assertEqual(embed.fields[0].name, "Habbo Username")
        self.assertEqual(embed.fields[0].value, "Siren")
        self.assertEqual(embed.fields[1].name, "Total time online")
        self.assertEqual(embed.fields[1].value, "51 hours, 0 minutes")
        self.assertEqual(embed.fields[2].name, "Last access time")
        self.assertTrue(embed.fields[2].value.startswith("<t:"))
        self.assertTrue(embed.fields[2].value.endswith(":R>"))
        self.assertEqual(embed.fields[3].name, "Current time")
        self.assertTrue(embed.fields[3].value.startswith("<t:"))
        self.assertTrue(embed.fields[3].value.endswith(":R>"))

    async def test_extract_online_time_falls_back_to_last_access(self) -> None:
        cog = HabboOnlineTimeCog(MagicMock())
        last_access = (datetime.now(timezone.utc) - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%S.%f%z")
        profile = {"lastAccessTime": last_access}

        seconds = cog._extract_online_time_seconds(profile)

        self.assertIsNotNone(seconds)
        # Allow a small tolerance because test execution time is non-zero.
        self.assertTrue(29 * 3600 <= int(seconds) <= 31 * 3600)


if __name__ == "__main__":
    unittest.main()
