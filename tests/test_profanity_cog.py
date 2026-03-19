"""Unit tests for the profanity-filter message listener cog."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

try:
    from COGS.ProfanityCog import ProfanityCog
except ModuleNotFoundError:
    ProfanityCog = None


@unittest.skipIf(ProfanityCog is None, "discord.py is not installed in the test environment")
class ProfanityCogTests(unittest.IsolatedAsyncioTestCase):
    """Validate profanity detection, deletion, and notification flow."""

    def test_load_blocked_words_reads_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            words_path = Path(temp_dir) / "profanity_words.json"
            words_path.write_text(json.dumps(["curse", "sh1t", "f.u.c.k"]), encoding="utf-8")

            cog = ProfanityCog(MagicMock(), blocked_words_path=words_path)

            # Loaded words are normalized once so message checks stay consistent.
            self.assertEqual(cog.blocked_words, {"curse", "shit", "fuck"})

    def test_contains_profanity_matches_common_variations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            words_path = Path(temp_dir) / "profanity_words.json"
            words_path.write_text(json.dumps(["fuck", "shit", "bitch"]), encoding="utf-8")

            cog = ProfanityCog(MagicMock(), blocked_words_path=words_path)

            self.assertTrue(cog.contains_profanity("This is f.u.c.k."))
            self.assertTrue(cog.contains_profanity("What the sh1t"))
            self.assertTrue(cog.contains_profanity("You are a biiiiitch"))
            self.assertFalse(cog.contains_profanity("Friendly and professional chat only."))

    async def test_on_message_deletes_and_notifies_user_and_log_channel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            words_path = Path(temp_dir) / "profanity_words.json"
            words_path.write_text(json.dumps(["shit"]), encoding="utf-8")

            cog = ProfanityCog(MagicMock(), blocked_words_path=words_path)

            user_notice_channel = SimpleNamespace(mention="#general", send=AsyncMock())
            log_channel = SimpleNamespace(send=AsyncMock())
            guild = SimpleNamespace(id=321, name="RPA", get_channel=MagicMock(return_value=log_channel))
            author = SimpleNamespace(id=123, bot=False, mention="<@123>")
            message = SimpleNamespace(
                author=author,
                webhook_id=None,
                guild=guild,
                channel=user_notice_channel,
                content="You are full of sh1t",
                delete=AsyncMock(),
            )

            cog.server_config_store = SimpleNamespace(get_profanity_log_channel_id=MagicMock(return_value=999))

            await cog.on_message(message)

            message.delete.assert_awaited_once()
            user_notice_channel.send.assert_awaited_once()
            user_embed = user_notice_channel.send.await_args.kwargs["embed"]
            self.assertEqual(user_embed.title, "Profanity Filter")
            self.assertIn("has been deleted", user_embed.description)

            log_channel.send.assert_awaited_once()
            log_embed = log_channel.send.await_args.kwargs["embed"]
            self.assertEqual(log_embed.title, "Profanity Filter Triggered")
            self.assertEqual(log_embed.fields[1].name, "Server")
            self.assertIn("RPA", log_embed.fields[1].value)

    async def test_on_message_skips_when_no_profanity_channel_is_configured(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            words_path = Path(temp_dir) / "profanity_words.json"
            words_path.write_text(json.dumps(["damn"]), encoding="utf-8")

            cog = ProfanityCog(MagicMock(), blocked_words_path=words_path)

            user_notice_channel = SimpleNamespace(mention="#general", send=AsyncMock())
            guild = SimpleNamespace(id=321, name="RPA", get_channel=MagicMock(return_value=None))
            author = SimpleNamespace(id=123, bot=False, mention="<@123>")
            message = SimpleNamespace(
                author=author,
                webhook_id=None,
                guild=guild,
                channel=user_notice_channel,
                content="damn",
                delete=AsyncMock(),
            )

            cog.server_config_store = SimpleNamespace(get_profanity_log_channel_id=MagicMock(return_value=None))

            await cog.on_message(message)

            message.delete.assert_awaited_once()
            user_notice_channel.send.assert_awaited_once()
            guild.get_channel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
