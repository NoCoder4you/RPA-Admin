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

    def test_display_emoji_returns_unicode_or_custom_markup(self) -> None:
        guild = MagicMock()
        guild.get_emoji.return_value = None

        self.assertEqual(self.cog._display_emoji(guild=guild, stored_emoji="✅"), "✅")
        self.assertEqual(self.cog._display_emoji(guild=guild, stored_emoji="party:12345"), "<:party:12345>")

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

    def test_entries_for_message_returns_all_rows_for_same_message(self) -> None:
        self.cog.reaction_roles = [
            {"guild_id": 1, "channel_id": 10, "message_id": 99, "emoji": "✅", "role_id": 100},
            {"guild_id": 1, "channel_id": 10, "message_id": 99, "emoji": "🎉", "role_id": 101},
            {"guild_id": 1, "channel_id": 10, "message_id": 98, "emoji": "📢", "role_id": 102},
        ]

        same_message = self.cog._entries_for_message(guild_id=1, message_id=99)

        self.assertEqual(len(same_message), 2)
        self.assertEqual({entry["emoji"] for entry in same_message}, {"✅", "🎉"})

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

    def test_build_reaction_role_embeds_mentions_role_and_instruction(self) -> None:
        role = SimpleNamespace(mention="<@&999>")
        embeds = self.cog._build_reaction_role_embeds(
            emoji="✅",
            role=role,
        )
        self.assertEqual(embeds[0].title, "Reaction Roles")
        self.assertEqual(embeds[0].footer.text, "React to toggle your roles.")
        text = embeds[0].description or ""

        self.assertIn("React to this message to assign yourself roles and gain channel access.", text)
        self.assertIn("✅ = <@&999>", text)
        self.assertNotIn("Role mapping", text)
        self.assertNotIn("Remove your reaction", text)
        self.assertNotIn("Details", text)

    def test_build_reaction_role_embeds_returns_single_embed(self) -> None:
        role = SimpleNamespace(mention="<@&999>")
        embeds = self.cog._build_reaction_role_embeds(
            emoji="✅",
            role=role,
        )

        self.assertEqual(len(embeds), 1)


if __name__ == "__main__":
    unittest.main()
