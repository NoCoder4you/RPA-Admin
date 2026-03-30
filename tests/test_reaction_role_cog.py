"""Unit tests for the reaction role cog helper behavior."""

from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock

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

    def test_build_reaction_role_message_mentions_role_and_instruction(self) -> None:
        role = SimpleNamespace(mention="<@&999>")
        text = self.cog._build_reaction_role_message(
            emoji="✅",
            role=role,
            message_text="Pick your team role below.",
        )

        self.assertIn("✨ **REACTION ROLE** ✨", text)
        self.assertIn("React with ✅ to get <@&999>.", text)
        self.assertIn("Remove your reaction to lose <@&999>.", text)
        self.assertIn("Pick your team role below.", text)


if __name__ == "__main__":
    unittest.main()
