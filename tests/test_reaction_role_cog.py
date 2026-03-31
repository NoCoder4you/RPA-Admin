"""Unit tests for the reaction role cog helper behavior."""

from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

try:
    from COGS.ReactionRoleCog import ReactionRoleCog
except ModuleNotFoundError:
    ReactionRoleCog = None


@unittest.skipIf(ReactionRoleCog is None, "discord.py is not installed in the test environment")
class ReactionRoleCogTests(unittest.TestCase):
    """Validate local helper logic for reaction role persistence/matching."""

    def setUp(self) -> None:
        self.bot = MagicMock()
        self.cog = ReactionRoleCog(self.bot)

    def test_normalize_emoji_handles_custom_and_unicode(self) -> None:
        self.assertEqual(self.cog._normalize_emoji("✅"), "✅")
        self.assertEqual(self.cog._normalize_emoji("<:rpa:123456>"), "rpa:123456")
        self.assertEqual(self.cog._normalize_emoji("<a:dance:555>"), "dance:555")

    def test_find_entry_filters_by_optional_fields(self) -> None:
        self.cog.reaction_roles = [
            {
                "guild_id": 1,
                "channel_id": 2,
                "message_id": 3,
                "emoji": "✅",
                "role_id": 4,
            }
        ]

        match = self.cog._find_entry(guild_id=1, message_id=3, emoji="✅")
        self.assertIsNotNone(match)

        no_match = self.cog._find_entry(guild_id=1, message_id=3, emoji="❌")
        self.assertIsNone(no_match)

    def test_save_and_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "ReactionRoles.json"
            self.cog.data_file = file_path
            self.cog.reaction_roles = [
                {
                    "guild_id": 100,
                    "channel_id": 200,
                    "message_id": 300,
                    "emoji": "check:123",
                    "role_id": 400,
                }
            ]

            self.cog._save_data()
            self.cog.reaction_roles = []
            loaded = self.cog._load_data()

            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["message_id"], 300)
            self.assertEqual(loaded[0]["emoji"], "check:123")

    def test_missing_bot_permissions_reports_all_required_flags(self) -> None:
        channel = MagicMock()
        channel.permissions_for.return_value = SimpleNamespace(
            view_channel=False,
            read_message_history=False,
            add_reactions=False,
            manage_roles=False,
            send_messages=False,
        )
        me = MagicMock()

        missing = self.cog._missing_bot_permissions(channel=channel, me=me)

        self.assertEqual(
            missing,
            [
                "View Channel",
                "Read Message History",
                "Add Reactions",
                "Manage Roles",
                "Send Messages",
            ],
        )

    def test_build_reaction_role_embeds_keeps_instruction_and_mapping_only(self) -> None:
        role = SimpleNamespace(mention="<@&999>")
        embeds = self.cog._build_reaction_role_embeds(
            emoji="✅",
            role=role,
            message_text="Pick your team role below.",
        )
        text = embeds[0].description or ""

        self.assertEqual(
            text,
            "React to this message to assign yourself roles and gain channel access.\n\n✅ = <@&999>\n",
        )

    def test_build_reaction_role_embeds_splits_when_description_is_too_large(self) -> None:
        role = SimpleNamespace(mention="<@&999>")
        very_long = "\n".join([f"Line {index} {'x' * 120}" for index in range(120)])

        embeds = self.cog._build_reaction_role_embeds(
            emoji="✅",
            role=role,
            message_text=very_long,
        )

        self.assertGreater(len(embeds), 1)
        for embed in embeds:
            self.assertLessEqual(len(embed.description or ""), 4096)

    def test_on_raw_reaction_add_toggles_existing_role_off(self) -> None:
        role = SimpleNamespace(id=4)
        member = MagicMock()
        member.bot = False
        member.roles = [role]
        member.guild = MagicMock()
        member.guild.get_role.return_value = role
        member.guild.get_channel.return_value = None
        member.remove_roles = AsyncMock()
        member.add_roles = AsyncMock()

        payload = SimpleNamespace(
            guild_id=1,
            channel_id=2,
            message_id=3,
            user_id=5,
            emoji="✅",
        )

        self.cog._resolve_member = AsyncMock(return_value=member)
        self.cog._find_entry = MagicMock(return_value={"role_id": 4})

        import asyncio

        asyncio.run(self.cog.on_raw_reaction_add(payload))

        member.remove_roles.assert_awaited_once()
        member.add_roles.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
