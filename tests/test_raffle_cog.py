"""Unit tests for the raffle management cog."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

try:
    import discord
    from COGS.MiscRaffle import RAFFLE_LOG_CHANNEL_ID, RaffleCog, raffle_id_autocomplete
except Exception:
    discord = None
    RAFFLE_LOG_CHANNEL_ID = 1485484040055427132
    RaffleCog = None
    raffle_id_autocomplete = None


@unittest.skipIf(RaffleCog is None or discord is None or raffle_id_autocomplete is None, "discord.py is not installed in the test environment")
class RaffleCogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.storage_path = Path(self.tempdir.name) / "raffles.json"
        self.bot = MagicMock()
        self.cog = RaffleCog(self.bot, storage_path=self.storage_path)
        self.cog.server_config_store = SimpleNamespace(get_audit_channel_id=lambda: None)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    @staticmethod
    def _member(member_id: int) -> SimpleNamespace:
        """Build a lightweight member-like object with a stable display string for embeds."""

        class DummyMember(SimpleNamespace):
            def __str__(self) -> str:
                return "Player#0001"

        return DummyMember(id=member_id, mention=f"<@{member_id}>", send=AsyncMock())

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
        member = self._member(55)
        guild = SimpleNamespace(
            id=999,
            name="Guild",
            members=[member],
            get_member=lambda member_id: member if member_id == 55 else None,
        )
        interaction = SimpleNamespace(
            guild=guild,
            channel=SimpleNamespace(id=111, mention="#general"),
            user=SimpleNamespace(id=1, guild_permissions=member_permissions, mention="<@1>"),
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await self.cog.raffle_add.callback(self.cog, interaction, "ABC12345", "55", 1)

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
        member = self._member(55)
        member.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "closed"))
        guild = SimpleNamespace(
            id=999,
            name="Guild",
            members=[member],
            get_member=lambda member_id: member if member_id == 55 else None,
        )
        interaction = SimpleNamespace(
            guild=guild,
            channel=SimpleNamespace(id=111, mention="#general"),
            user=SimpleNamespace(id=1, guild_permissions=member_permissions, mention="<@1>"),
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await self.cog.raffle_add.callback(self.cog, interaction, "ABC12345", "55", 3)

        self.assertEqual(self.cog._raffles["ABC12345"]["entrants"]["55"]["entries"], 4)
        embed = response.send_message.await_args.kwargs["embed"]
        self.assertNotIn("<@55>", embed.description)
        self.assertEqual(embed.fields[0].name, "Raffle ID")
        self.assertEqual(embed.fields[1].name, "User Total Entries")

    async def test_add_skips_dm_for_unverified_user(self) -> None:
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
        member = self._member(55)
        guild = SimpleNamespace(
            id=999,
            name="Guild",
            members=[member],
            get_member=lambda member_id: member if member_id == 55 else None,
        )
        interaction = SimpleNamespace(
            guild=guild,
            channel=SimpleNamespace(id=111, mention="#general"),
            user=SimpleNamespace(id=1, guild_permissions=member_permissions, mention="<@1>"),
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
        )
        self.cog.verified_store = SimpleNamespace(
            is_verified=lambda discord_id: False,
            get_habbo_username=lambda discord_id: None,
            get_all_entries=lambda: [],
        )

        with patch.object(self.cog, "_send_entry_dm", AsyncMock(return_value=True)) as mock_dm:
            await self.cog.raffle_add.callback(self.cog, interaction, "ABC12345", "55", 1)

        mock_dm.assert_not_awaited()
        embed = response.send_message.await_args.kwargs["embed"]
        self.assertNotIn("<@55>", embed.description)
        self.assertEqual(len(embed.fields), 2)

    async def test_add_attempts_dm_for_verified_user(self) -> None:
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
        member = self._member(55)
        guild = SimpleNamespace(
            id=999,
            name="Guild",
            members=[member],
            get_member=lambda member_id: member if member_id == 55 else None,
        )
        interaction = SimpleNamespace(
            guild=guild,
            channel=SimpleNamespace(id=111, mention="#general"),
            user=SimpleNamespace(id=1, guild_permissions=member_permissions, mention="<@1>"),
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
        )
        self.cog.verified_store = SimpleNamespace(
            is_verified=lambda discord_id: True,
            get_habbo_username=lambda discord_id: None,
            get_all_entries=lambda: [],
        )

        with patch.object(self.cog, "_send_entry_dm", AsyncMock(return_value=True)) as mock_dm:
            await self.cog.raffle_add.callback(self.cog, interaction, "ABC12345", "55", 2)

        mock_dm.assert_awaited_once()
        embed = response.send_message.await_args.kwargs["embed"]
        self.assertNotIn("<@55>", embed.description)
        self.assertEqual(len(embed.fields), 2)

    async def test_add_resolves_member_from_plain_text_name(self) -> None:
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
        member = self._member(55)
        member.display_name = "PlayerOne"
        member.name = "PlayerOne"
        member.global_name = "PlayerOne"
        guild = SimpleNamespace(
            id=999,
            name="Guild",
            members=[member],
            get_member=lambda member_id: member if member_id == 55 else None,
        )
        interaction = SimpleNamespace(
            guild=guild,
            channel=SimpleNamespace(id=111, mention="#general"),
            user=SimpleNamespace(id=1, guild_permissions=member_permissions, mention="<@1>"),
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
        )
        self.cog.verified_store = SimpleNamespace(
            is_verified=lambda discord_id: True,
            get_habbo_username=lambda discord_id: None,
            get_all_entries=lambda: [],
        )

        await self.cog.raffle_add.callback(self.cog, interaction, "ABC12345", "PlayerOne", 1)

        self.assertIn("55", self.cog._raffles["ABC12345"]["entrants"])

    async def test_add_allows_free_text_user_not_in_server(self) -> None:
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
        guild = SimpleNamespace(
            id=999,
            name="Guild",
            members=[],
            get_member=lambda member_id: None,
            get_channel=lambda channel_id: None,
        )
        interaction = SimpleNamespace(
            guild=guild,
            channel=SimpleNamespace(id=111, mention="#general"),
            user=SimpleNamespace(id=1, guild_permissions=member_permissions, mention="<@1>"),
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
        )
        self.cog.verified_store = SimpleNamespace(
            is_verified=lambda discord_id: False,
            get_habbo_username=lambda discord_id: None,
            get_all_entries=lambda: [],
        )

        await self.cog.raffle_add.callback(self.cog, interaction, "ABC12345", "External Player", 2)

        self.assertIn("text:external player", self.cog._raffles["ABC12345"]["entrants"])
        self.assertEqual(self.cog._raffles["ABC12345"]["entrants"]["text:external player"]["entries"], 2)

    async def test_add_logs_dm_outcome_to_audit_channel(self) -> None:
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
        member = self._member(55)
        audit_channel = MagicMock(spec=discord.TextChannel)
        audit_channel.send = AsyncMock()
        guild = SimpleNamespace(
            id=999,
            name="Guild",
            members=[member],
            get_channel=lambda channel_id: audit_channel if channel_id == 777 else None,
            get_member=lambda member_id: member if member_id == 55 else None,
        )
        interaction = SimpleNamespace(
            guild=guild,
            channel=SimpleNamespace(id=111, mention="#general"),
            user=SimpleNamespace(id=1, guild_permissions=member_permissions, mention="<@1>"),
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
        )
        self.cog.verified_store = SimpleNamespace(
            is_verified=lambda discord_id: False,
            get_habbo_username=lambda discord_id: None,
            get_all_entries=lambda: [],
        )
        self.cog.server_config_store = SimpleNamespace(get_audit_channel_id=lambda: 777)

        await self.cog.raffle_add.callback(self.cog, interaction, "ABC12345", "55", 1)

        audit_channel.send.assert_awaited_once()
        audit_embed = audit_channel.send.await_args.kwargs["embed"]
        audit_fields = {field.name: field.value for field in audit_embed.fields}
        self.assertEqual(audit_fields["Entry DM Status"], "Skipped (not in VerifiedUsers.json)")

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
        member = self._member(55)
        guild = SimpleNamespace(
            id=999,
            name="Guild",
            members=[member],
            get_member=lambda member_id: member if member_id == 55 else None,
        )
        interaction = SimpleNamespace(
            guild=guild,
            channel=SimpleNamespace(id=111, mention="#general"),
            user=SimpleNamespace(id=1, guild_permissions=member_permissions, mention="<@1>"),
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
        )
        self.cog.verified_store = SimpleNamespace(
            is_verified=lambda discord_id: True,
            get_habbo_username=lambda discord_id: "Siren" if discord_id == "55" else None,
            get_all_entries=lambda: [],
        )

        with patch("COGS.raffle.fetch_habbo_profile", return_value={"figureString": "hr-1-1"}) as mock_fetch:
            await self.cog.raffle_add.callback(self.cog, interaction, "ABC12345", "55", 2)

        mock_fetch.assert_called_once_with("Siren")
        embed = response.send_message.await_args.kwargs["embed"]
        self.assertIn("figure=hr-1-1", embed.thumbnail.url)
        self.assertIn("action=std", embed.thumbnail.url)
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
        member = self._member(55)
        guild = SimpleNamespace(
            id=999,
            name="Guild",
            members=[member],
            get_member=lambda member_id: member if member_id == 55 else None,
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

        await self.cog.raffle_add.callback(self.cog, interaction, "MISSING", "55", 1)

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
        member = self._member(55)
        guild = SimpleNamespace(
            id=999,
            name="Guild",
            members=[member],
            get_member=lambda member_id: member if member_id == 55 else None,
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

        await self.cog.raffle_add.callback(self.cog, interaction, "ABC12345", "55", 2)

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

    async def test_list_formats_id_and_name_as_subheadings(self) -> None:
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
            channel=SimpleNamespace(id=111, mention="#general"),
            user=SimpleNamespace(id=1, guild_permissions=member_permissions, mention="<@1>"),
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await self.cog.raffle_list.callback(self.cog, interaction)

        embed = response.send_message.await_args.kwargs["embed"]
        self.assertEqual(embed.fields[0].name, "Raffle IDs")
        self.assertIn("`ABC12345`", embed.fields[0].value)
        self.assertEqual(embed.fields[1].name, "Raffle")
        self.assertIn("**ID**", embed.fields[1].value)
        self.assertIn("`ABC12345`", embed.fields[1].value)
        self.assertIn("**Raffle Name**", embed.fields[1].value)
        self.assertIn("Spring Event", embed.fields[1].value)

    async def test_list_includes_all_active_raffle_ids_in_single_field(self) -> None:
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
                "entrants": {"55": {"username": "Player#0001", "entries": 2}},
                "winners": [],
                "log_channel_id": RAFFLE_LOG_CHANNEL_ID,
                "log_message_id": None,
            },
            "DEF67890": {
                "raffle_id": "DEF67890",
                "name": "Summer Event",
                "description": None,
                "guild_id": 999,
                "channel_id": 111,
                "created_by": 10,
                "created_at": "2026-03-24T00:00:00+00:00",
                "active": True,
                "allow_multiple_entries": False,
                "entrants": {"77": {"username": "Player#0002", "entries": 1}},
                "winners": [],
                "log_channel_id": RAFFLE_LOG_CHANNEL_ID,
                "log_message_id": None,
            },
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

        await self.cog.raffle_list.callback(self.cog, interaction)

        embed = response.send_message.await_args.kwargs["embed"]
        self.assertEqual(embed.fields[0].name, "Raffle IDs")
        self.assertIn("• `ABC12345`", embed.fields[0].value)
        self.assertIn("• `DEF67890`", embed.fields[0].value)

    async def test_raffle_id_autocomplete_returns_only_active_recent_guild_raffles(self) -> None:
        self.cog._raffles = {
            "OLDER001": {
                "raffle_id": "OLDER001",
                "name": "Older Event",
                "description": None,
                "guild_id": 999,
                "channel_id": 111,
                "created_by": 10,
                "created_at": "2026-03-21T00:00:00+00:00",
                "active": True,
                "allow_multiple_entries": True,
                "entrants": {},
                "winners": [],
                "log_channel_id": RAFFLE_LOG_CHANNEL_ID,
                "log_message_id": None,
            },
            "NEWER002": {
                "raffle_id": "NEWER002",
                "name": "Newer Event",
                "description": None,
                "guild_id": 999,
                "channel_id": 111,
                "created_by": 10,
                "created_at": "2026-03-24T00:00:00+00:00",
                "active": False,
                "allow_multiple_entries": False,
                "entrants": {},
                "winners": [],
                "log_channel_id": RAFFLE_LOG_CHANNEL_ID,
                "log_message_id": None,
            },
            "OTHER003": {
                "raffle_id": "OTHER003",
                "name": "Other Guild Event",
                "description": None,
                "guild_id": 555,
                "channel_id": 111,
                "created_by": 10,
                "created_at": "2026-03-25T00:00:00+00:00",
                "active": True,
                "allow_multiple_entries": True,
                "entrants": {},
                "winners": [],
                "log_channel_id": RAFFLE_LOG_CHANNEL_ID,
                "log_message_id": None,
            },
        }
        interaction = SimpleNamespace(guild=SimpleNamespace(id=999), client=SimpleNamespace(get_cog=lambda _name: self.cog))

        choices = await raffle_id_autocomplete(interaction, "")

        # Closed raffles are intentionally excluded from autocomplete so staff
        # only see raffle IDs that can still accept moderation actions.
        self.assertEqual([choice.value for choice in choices], ["OLDER001"])
        self.assertIn("Active", choices[0].name)

    async def test_raffle_id_autocomplete_filters_by_name_or_id(self) -> None:
        self.cog._raffles = {
            "ABC12345": {
                "raffle_id": "ABC12345",
                "name": "Spring Event",
                "description": None,
                "guild_id": 999,
                "channel_id": 111,
                "created_by": 10,
                "created_at": "2026-03-24T00:00:00+00:00",
                "active": True,
                "allow_multiple_entries": True,
                "entrants": {},
                "winners": [],
                "log_channel_id": RAFFLE_LOG_CHANNEL_ID,
                "log_message_id": None,
            },
            "ZZZ99999": {
                "raffle_id": "ZZZ99999",
                "name": "Winter Event",
                "description": None,
                "guild_id": 999,
                "channel_id": 111,
                "created_by": 10,
                "created_at": "2026-03-25T00:00:00+00:00",
                "active": True,
                "allow_multiple_entries": True,
                "entrants": {},
                "winners": [],
                "log_channel_id": RAFFLE_LOG_CHANNEL_ID,
                "log_message_id": None,
            },
        }
        interaction = SimpleNamespace(guild=SimpleNamespace(id=999), client=SimpleNamespace(get_cog=lambda _name: self.cog))

        by_name = await raffle_id_autocomplete(interaction, "spring")
        by_id = await raffle_id_autocomplete(interaction, "99999")

        self.assertEqual(len(by_name), 1)
        self.assertEqual(by_name[0].value, "ABC12345")
        self.assertEqual(len(by_id), 1)
        self.assertEqual(by_id[0].value, "ZZZ99999")

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
        self.assertIn("action=std", winner_one_embed.thumbnail.url)
        self.assertIn("figure=hd-2-2", winner_two_embed.thumbnail.url)
        self.assertIn("action=std", winner_two_embed.thumbnail.url)
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
        self.assertIn("action=std", embed.thumbnail.url)

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
