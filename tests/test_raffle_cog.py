"""Unit tests for the raffle management cog."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

try:
    import discord
    from COGS.raffle import RAFFLE_LOG_CHANNEL_ID, RaffleCog
except Exception:
    discord = None
    RAFFLE_LOG_CHANNEL_ID = 1485484040055427132
    RaffleCog = None


@unittest.skipIf(RaffleCog is None or discord is None, "discord.py is not installed in the test environment")
class RaffleCogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.storage_path = Path(self.tempdir.name) / "raffles.json"
        self.bot = MagicMock()
        self.cog = RaffleCog(self.bot, storage_path=self.storage_path)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    async def test_load_creates_storage_file_when_missing(self) -> None:
        await self.cog._load_raffles()

        self.assertTrue(self.storage_path.exists())
        self.assertEqual(self.cog._raffles, {})

    async def test_add_rejects_duplicate_single_entry(self) -> None:
        self.cog._raffles = {
            "ABC12345": {
                "raffle_id": "ABC12345",
                "name": "Spring Event",
                "description": None,
                "guild_id": 999,
                "channel_id": 111,
                "created_by": 10,
                "created_at": "2026-03-23T00:00:00+00:00",
                "active": True,
                "allow_multiple_entries": False,
                "entrants": {"55": {"username": "Player#0001", "entries": 1}},
                "winners": [],
                "log_channel_id": RAFFLE_LOG_CHANNEL_ID,
                "log_message_id": None,
            }
        }
        member_permissions = SimpleNamespace(manage_guild=True, administrator=False)
        response = SimpleNamespace(is_done=lambda: False, send_message=AsyncMock())
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=999, name="Guild"),
            channel=SimpleNamespace(id=111, mention="#general"),
            user=SimpleNamespace(id=1, guild_permissions=member_permissions, mention="<@1>"),
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
        )
        member = SimpleNamespace(id=55, mention="<@55>")

        await self.cog.raffle_add.callback(self.cog, interaction, "ABC12345", member, 1)

        embed = response.send_message.await_args.kwargs["embed"]
        self.assertEqual(embed.title, "Entry Exists")

    async def test_add_allows_multiple_entries_and_reports_dm_failure(self) -> None:
        self.cog._raffles = {
            "ABC12345": {
                "raffle_id": "ABC12345",
                "name": "Spring Event",
                "description": None,
                "guild_id": 999,
                "channel_id": 111,
                "created_by": 10,
                "created_at": "2026-03-23T00:00:00+00:00",
                "active": True,
                "allow_multiple_entries": True,
                "entrants": {"55": {"username": "Player#0001", "entries": 1}},
                "winners": [],
                "log_channel_id": RAFFLE_LOG_CHANNEL_ID,
                "log_message_id": None,
            }
        }
        member_permissions = SimpleNamespace(manage_guild=True, administrator=False)
        response = SimpleNamespace(is_done=lambda: False, send_message=AsyncMock())
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=999, name="Guild"),
            channel=SimpleNamespace(id=111, mention="#general"),
            user=SimpleNamespace(id=1, guild_permissions=member_permissions, mention="<@1>"),
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
        )
        member = SimpleNamespace(id=55, mention="<@55>", send=AsyncMock(side_effect=discord.Forbidden(MagicMock(), "closed")))

        await self.cog.raffle_add.callback(self.cog, interaction, "ABC12345", member, 3)

        self.assertEqual(self.cog._raffles["ABC12345"]["entrants"]["55"]["entries"], 4)
        embed = response.send_message.await_args.kwargs["embed"]
        self.assertIn("could not be delivered", embed.fields[2].value)

    async def test_add_uses_verified_habbo_thumbnail_on_staff_embed(self) -> None:
        self.cog._raffles = {
            "ABC12345": {
                "raffle_id": "ABC12345",
                "name": "Spring Event",
                "description": None,
                "guild_id": 999,
                "channel_id": 111,
                "created_by": 10,
                "created_at": "2026-03-23T00:00:00+00:00",
                "active": True,
                "allow_multiple_entries": True,
                "entrants": {},
                "winners": [],
                "log_channel_id": RAFFLE_LOG_CHANNEL_ID,
                "log_message_id": None,
            }
        }
        member_permissions = SimpleNamespace(manage_guild=True, administrator=False)
        response = SimpleNamespace(is_done=lambda: False, send_message=AsyncMock())
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=999, name="Guild"),
            channel=SimpleNamespace(id=111, mention="#general"),
            user=SimpleNamespace(id=1, guild_permissions=member_permissions, mention="<@1>"),
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
        )
        member = SimpleNamespace(id=55, mention="<@55>", send=AsyncMock())
        self.cog.verified_store = SimpleNamespace(get_habbo_username=lambda discord_id: "Siren" if discord_id == "55" else None)

        with patch("COGS.raffle.fetch_habbo_profile", return_value={"figureString": "hr-1-1"}) as mock_fetch:
            await self.cog.raffle_add.callback(self.cog, interaction, "ABC12345", member, 2)

        mock_fetch.assert_called_once_with("Siren")
        embed = response.send_message.await_args.kwargs["embed"]
        self.assertIn("figure=hr-1-1", embed.thumbnail.url)
        self.assertFalse(response.send_message.await_args.kwargs["ephemeral"])

    async def test_missing_permissions_do_not_mirror_to_raffle_channel(self) -> None:
        member_permissions = SimpleNamespace(manage_guild=False, administrator=False)
        response = SimpleNamespace(is_done=lambda: False, send_message=AsyncMock())
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=999, name="Guild"),
            channel=SimpleNamespace(id=111, mention="#general"),
            user=SimpleNamespace(id=1, guild_permissions=member_permissions, mention="<@1>"),
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
        )
        log_channel = SimpleNamespace(send=AsyncMock())
        self.bot.get_channel.return_value = log_channel

        await self.cog.raffle_list.callback(self.cog, interaction)

        embed = response.send_message.await_args.kwargs["embed"]
        self.assertEqual(embed.title, "Missing Permissions")
        log_channel.send.assert_not_awaited()

    async def test_raffle_not_found_does_not_mirror_to_raffle_channel(self) -> None:
        member_permissions = SimpleNamespace(manage_guild=True, administrator=False)
        response = SimpleNamespace(is_done=lambda: False, send_message=AsyncMock())
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=999, name="Guild"),
            channel=SimpleNamespace(id=111, mention="#general"),
            user=SimpleNamespace(id=1, guild_permissions=member_permissions, mention="<@1>"),
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
        )
        member = SimpleNamespace(id=55, mention="<@55>", send=AsyncMock())
        log_channel = SimpleNamespace(send=AsyncMock())
        self.bot.get_channel.return_value = log_channel

        await self.cog.raffle_add.callback(self.cog, interaction, "MISSING", member, 1)

        embed = response.send_message.await_args.kwargs["embed"]
        self.assertEqual(embed.title, "Raffle Not Found")
        log_channel.send.assert_not_awaited()

    async def test_add_mirrors_embed_to_raffle_channel(self) -> None:
        self.cog._raffles = {
            "ABC12345": {
                "raffle_id": "ABC12345",
                "name": "Spring Event",
                "description": None,
                "guild_id": 999,
                "channel_id": 111,
                "created_by": 10,
                "created_at": "2026-03-23T00:00:00+00:00",
                "active": True,
                "allow_multiple_entries": True,
                "entrants": {},
                "winners": [],
                "log_channel_id": RAFFLE_LOG_CHANNEL_ID,
                "log_message_id": 9999,
            }
        }
        member_permissions = SimpleNamespace(manage_guild=True, administrator=False)
        response = SimpleNamespace(is_done=lambda: False, send_message=AsyncMock())
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=999, name="Guild"),
            channel=SimpleNamespace(id=111, mention="#general"),
            user=SimpleNamespace(id=1, guild_permissions=member_permissions, mention="<@1>"),
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
        )
        member = SimpleNamespace(id=55, mention="<@55>", send=AsyncMock())
        log_channel = SimpleNamespace(send=AsyncMock())
        self.bot.get_channel.return_value = log_channel

        await self.cog.raffle_add.callback(self.cog, interaction, "ABC12345", member, 2)

        log_channel.send.assert_awaited_once()

    async def test_list_avoids_same_channel_duplicate_when_public_response_is_used(self) -> None:
        self.cog._raffles = {
            "ABC12345": {
                "raffle_id": "ABC12345",
                "name": "Spring Event",
                "description": None,
                "guild_id": 999,
                "channel_id": RAFFLE_LOG_CHANNEL_ID,
                "created_by": 10,
                "created_at": "2026-03-23T00:00:00+00:00",
                "active": True,
                "allow_multiple_entries": True,
                "entrants": {"55": {"username": "Player#0001", "entries": 2}},
                "winners": [],
                "log_channel_id": RAFFLE_LOG_CHANNEL_ID,
                "log_message_id": None,
            }
        }
        member_permissions = SimpleNamespace(manage_guild=True, administrator=False)
        response = SimpleNamespace(is_done=lambda: False, send_message=AsyncMock())
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=999, name="Guild"),
            channel=SimpleNamespace(id=RAFFLE_LOG_CHANNEL_ID, mention="#raffles"),
            user=SimpleNamespace(id=1, guild_permissions=member_permissions, mention="<@1>"),
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
        )
        log_channel = SimpleNamespace(send=AsyncMock())
        self.bot.get_channel.return_value = log_channel

        await self.cog.raffle_list.callback(self.cog, interaction)

        self.assertFalse(response.send_message.await_args.kwargs["ephemeral"])
        log_channel.send.assert_not_awaited()

    async def test_draw_auto_closes_raffle_and_dms_each_winner(self) -> None:
        self.cog._raffles = {
            "ABC12345": {
                "raffle_id": "ABC12345",
                "name": "Spring Event",
                "description": None,
                "guild_id": 999,
                "channel_id": 111,
                "created_by": 10,
                "created_at": "2026-03-23T00:00:00+00:00",
                "active": True,
                "allow_multiple_entries": True,
                "entrants": {
                    "55": {"username": "PlayerOne#0001", "entries": 3},
                    "77": {"username": "PlayerTwo#0001", "entries": 2},
                },
                "winners": [],
                "log_channel_id": RAFFLE_LOG_CHANNEL_ID,
                "log_message_id": None,
            }
        }
        member_permissions = SimpleNamespace(manage_guild=True, administrator=False)
        response = SimpleNamespace(is_done=lambda: False, send_message=AsyncMock())
        winner_one = SimpleNamespace(id=55, mention="<@55>", send=AsyncMock())
        winner_two = SimpleNamespace(id=77, mention="<@77>", send=AsyncMock())
        guild = SimpleNamespace(
            id=999,
            name="Guild",
            get_member=lambda member_id: {55: winner_one, 77: winner_two}.get(member_id),
        )
        interaction = SimpleNamespace(
            guild=guild,
            channel=SimpleNamespace(id=111, mention="#general"),
            user=SimpleNamespace(id=1, guild_permissions=member_permissions, mention="<@1>"),
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
        )
        log_channel = SimpleNamespace(send=AsyncMock())
        self.bot.get_channel.return_value = log_channel
        self.cog.verified_store = SimpleNamespace(get_habbo_username=lambda discord_id: {"55": "Siren", "77": "Nova"}.get(discord_id))

        with patch("COGS.raffle.random.choice", side_effect=[55, 77]), patch(
            "COGS.raffle.fetch_habbo_profile",
            side_effect=[{"figureString": "hr-1-1"}, {"figureString": "hd-2-2"}],
        ):
            await self.cog.raffle_draw.callback(self.cog, interaction, "ABC12345", 2)

        raffle = self.cog._raffles["ABC12345"]
        self.assertFalse(raffle["active"])
        self.assertEqual(raffle["winners"], [55, 77])
        winner_one.send.assert_awaited_once()
        winner_two.send.assert_awaited_once()
        winner_one_embed = winner_one.send.await_args.kwargs["embed"]
        winner_two_embed = winner_two.send.await_args.kwargs["embed"]
        self.assertEqual(winner_one_embed.fields[1].value, "1 of 2")
        self.assertEqual(winner_two_embed.fields[1].value, "2 of 2")
        self.assertIn("figure=hr-1-1", winner_one_embed.thumbnail.url)
        self.assertIn("figure=hd-2-2", winner_two_embed.thumbnail.url)
        response_embed = response.send_message.await_args.kwargs["embed"]
        response_fields = {field.name: field.value for field in response_embed.fields}
        self.assertEqual(response_fields["Raffle Status"], "Closed automatically after draw")
        self.assertEqual(response_fields["Winner DM Status"], "Sent 2/2 winner DM(s).")
        self.assertEqual(log_channel.send.await_count, 3)
        mirrored_embeds = [call.kwargs["embed"] for call in log_channel.send.await_args_list]
        self.assertEqual([embed.title for embed in mirrored_embeds], ["Raffle Winner", "Raffle Winner", "Winner Drawn"])
        self.assertEqual(mirrored_embeds[0].fields[1].value, "1 of 2")
        self.assertEqual(mirrored_embeds[1].fields[1].value, "2 of 2")

    async def test_send_winner_dm_reports_failure_when_dms_are_closed(self) -> None:
        member = SimpleNamespace(id=55, send=AsyncMock(side_effect=discord.Forbidden(MagicMock(), "closed")))
        self.cog.verified_store = SimpleNamespace(get_habbo_username=lambda discord_id: "Siren" if discord_id == "55" else None)

        with patch("COGS.raffle.fetch_habbo_profile", return_value={"figureString": "hr-1-1"}) as mock_fetch:
            result = await self.cog._send_winner_dm(
                member,
                raffle={"name": "Spring Event", "raffle_id": "ABC12345", "entrants": {"55": {"username": "PlayerOne", "entries": 3}}},
                guild_name="Guild",
                placement=1,
                total_winners=2,
            )

        self.assertFalse(result)
        mock_fetch.assert_called_once_with("Siren")

    async def test_send_entry_dm_uses_subheadings_and_habbo_thumbnail(self) -> None:
        member = SimpleNamespace(id=55, send=AsyncMock())
        self.cog.verified_store = SimpleNamespace(get_habbo_username=lambda discord_id: "Siren" if discord_id == "55" else None)

        with patch("COGS.raffle.fetch_habbo_profile", return_value={"figureString": "hr-1-1"}) as mock_fetch:
            result = await self.cog._send_entry_dm(
                member,
                raffle_name="Spring Event",
                guild_name="Guild",
                added_by=SimpleNamespace(mention="<@1>"),
                entry_count=4,
            )

        self.assertTrue(result)
        mock_fetch.assert_called_once_with("Siren")
        embed = member.send.await_args.kwargs["embed"]
        self.assertEqual([field.name for field in embed.fields], ["Raffle Name", "Total Entries", "Added By"])
        self.assertIn("figure=hr-1-1", embed.thumbnail.url)

    def test_pick_unique_weighted_winners_returns_unique_users(self) -> None:
        raffle = {
            "entrants": {
                "1": {"username": "One", "entries": 3},
                "2": {"username": "Two", "entries": 1},
                "3": {"username": "Three", "entries": 2},
            }
        }

        with patch("COGS.raffle.random.choice", side_effect=[1, 3]):
            winners = self.cog._pick_unique_weighted_winners(raffle, 2)

        self.assertEqual(winners, [1, 3])
        self.assertEqual(len(set(winners)), 2)

    async def test_entries_preview_limits_large_raffles(self) -> None:
        entrants = {str(i): {"username": f"User{i}", "entries": 1} for i in range(25)}
        self.cog._raffles = {
            "ABC12345": {
                "raffle_id": "ABC12345",
                "name": "Big Event",
                "description": "Desc",
                "guild_id": 999,
                "channel_id": 111,
                "created_by": 10,
                "created_at": "2026-03-23T00:00:00+00:00",
                "active": True,
                "allow_multiple_entries": True,
                "entrants": entrants,
                "winners": [],
                "log_channel_id": RAFFLE_LOG_CHANNEL_ID,
                "log_message_id": None,
            }
        }
        member_permissions = SimpleNamespace(manage_guild=True, administrator=False)
        response = SimpleNamespace(is_done=lambda: False, send_message=AsyncMock())
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=999, name="Guild"),
            channel=SimpleNamespace(id=111, mention="#general"),
            user=SimpleNamespace(id=1, guild_permissions=member_permissions, mention="<@1>"),
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await self.cog.raffle_entries.callback(self.cog, interaction, "ABC12345")

        embed = response.send_message.await_args.kwargs["embed"]
        self.assertIn("and 5 more user(s)", embed.fields[-1].value)

    async def test_create_sends_log_embed_and_stores_message_id(self) -> None:
        member_permissions = SimpleNamespace(manage_guild=True, administrator=False)
        response = SimpleNamespace(is_done=lambda: False, send_message=AsyncMock())
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=999, name="Guild"),
            channel=SimpleNamespace(id=111, mention="#audit-log"),
            user=SimpleNamespace(id=1, guild_permissions=member_permissions, mention="<@1>"),
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
        )
        logged_message = SimpleNamespace(id=5555)
        log_channel = SimpleNamespace(send=AsyncMock(return_value=logged_message))
        self.bot.get_channel.return_value = log_channel

        await self.cog.raffle_create.callback(self.cog, interaction, "test123", True, "idek")

        created_raffle = next(iter(self.cog._raffles.values()))
        self.assertEqual(created_raffle["log_channel_id"], RAFFLE_LOG_CHANNEL_ID)
        self.assertEqual(created_raffle["log_message_id"], 5555)
        log_channel.send.assert_awaited_once()
        embed = response.send_message.await_args.kwargs["embed"]
        self.assertEqual(embed.title, "Raffle Created")
        self.assertEqual(embed.fields[-1].name, "Log Channel")

    async def test_create_skips_prelog_send_when_already_in_log_channel(self) -> None:
        member_permissions = SimpleNamespace(manage_guild=True, administrator=False)
        response = SimpleNamespace(is_done=lambda: False, send_message=AsyncMock())
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=999, name="Guild"),
            channel=SimpleNamespace(id=RAFFLE_LOG_CHANNEL_ID, mention="#raffle"),
            user=SimpleNamespace(id=1, guild_permissions=member_permissions, mention="<@1>"),
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
        )
        log_channel = SimpleNamespace(send=AsyncMock())
        self.bot.get_channel.return_value = log_channel

        await self.cog.raffle_create.callback(self.cog, interaction, "test123", True, "idek")

        created_raffle = next(iter(self.cog._raffles.values()))
        self.assertEqual(created_raffle["log_channel_id"], RAFFLE_LOG_CHANNEL_ID)
        self.assertIsNone(created_raffle["log_message_id"])
        self.assertFalse(response.send_message.await_args.kwargs["ephemeral"])
        log_channel.send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
