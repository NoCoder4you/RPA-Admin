"""Unit tests for the profanity-filter message listener cog."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

try:
    from COGS.MiscProfanity import ProfanityCog
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

    async def test_on_message_flags_for_review_without_deleting_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            words_path = Path(temp_dir) / "profanity_words.json"
            words_path.write_text(json.dumps(["shit"]), encoding="utf-8")

            cog = ProfanityCog(MagicMock(), blocked_words_path=words_path)

            log_channel = SimpleNamespace(send=AsyncMock())
            guild = SimpleNamespace(id=321, name="RPA", get_channel=MagicMock(return_value=log_channel))
            author = SimpleNamespace(id=123, bot=False, mention="<@123>", send=AsyncMock())
            message = SimpleNamespace(
                author=author,
                webhook_id=None,
                guild=guild,
                channel=SimpleNamespace(mention="#general"),
                content="You are full of sh1t",
                delete=AsyncMock(),
            )

            cog.server_config_store = SimpleNamespace(get_profanity_log_channel_id=MagicMock(return_value=999))

            await cog.on_message(message)

            message.delete.assert_not_awaited()
            author.send.assert_not_awaited()
            log_channel.send.assert_awaited_once()
            send_kwargs = log_channel.send.await_args.kwargs
            self.assertIn("view", send_kwargs)
            self.assertIsNotNone(send_kwargs["view"])
            log_embed = send_kwargs["embed"]
            self.assertEqual(log_embed.title, "Profanity Filter Flagged Message")
            self.assertEqual(log_embed.fields[1].name, "Server")
            self.assertIn("RPA", log_embed.fields[1].value)
            self.assertEqual(log_embed.fields[3].name, "Blocked Word")
            self.assertIn("`shit`", log_embed.fields[3].value)
            self.assertEqual(log_embed.fields[4].name, "Message Content")
            self.assertIn("You are full of sh1t", log_embed.fields[4].value)

    async def test_proceed_button_deletes_and_dms_then_logs_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            words_path = Path(temp_dir) / "profanity_words.json"
            words_path.write_text(json.dumps(["damn"]), encoding="utf-8")

            cog = ProfanityCog(MagicMock(), blocked_words_path=words_path)

            log_channel = SimpleNamespace(send=AsyncMock())
            guild = SimpleNamespace(id=321, name="RPA", get_channel=MagicMock(return_value=log_channel))
            author = SimpleNamespace(id=123, bot=False, mention="<@123>", send=AsyncMock())
            message = SimpleNamespace(
                author=author,
                webhook_id=None,
                guild=guild,
                channel=SimpleNamespace(mention="#general"),
                content="damn",
                delete=AsyncMock(),
            )

            cog.server_config_store = SimpleNamespace(get_profanity_log_channel_id=MagicMock(return_value=999))

            await cog.on_message(message)
            review_view = log_channel.send.await_args.kwargs["view"]
            proceed_button = next(item for item in review_view.children if item.label == "Proceed")
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=777, mention="<@777>"),
                response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock()),
            )
            await proceed_button.callback(interaction)

            message.delete.assert_awaited_once()
            author.send.assert_awaited_once()
            interaction.response.edit_message.assert_awaited_once()
            self.assertEqual(log_channel.send.await_count, 2)
            log_embed = log_channel.send.await_args_list[1].kwargs["embed"]
            self.assertEqual(log_embed.title, "Profanity Filter Action")
            self.assertIn("Proceed", log_embed.description)
            self.assertEqual(log_embed.fields[3].name, "Blocked Word")
            self.assertIn("`damn`", log_embed.fields[3].value)
            self.assertEqual(log_embed.fields[4].name, "Message Content")
            self.assertIn("damn", log_embed.fields[4].value)
            self.assertEqual(log_embed.fields[6].name, "User Notice")
            self.assertIn("delivered successfully", log_embed.fields[6].value)

    async def test_ignore_button_leaves_message_untouched_and_logs_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            words_path = Path(temp_dir) / "profanity_words.json"
            words_path.write_text(json.dumps(["damn"]), encoding="utf-8")

            cog = ProfanityCog(MagicMock(), blocked_words_path=words_path)

            log_channel = SimpleNamespace(send=AsyncMock())
            guild = SimpleNamespace(id=321, name="RPA", get_channel=MagicMock(return_value=log_channel))
            author = SimpleNamespace(id=123, bot=False, mention="<@123>", send=AsyncMock())
            message = SimpleNamespace(
                author=author,
                webhook_id=None,
                guild=guild,
                channel=SimpleNamespace(mention="#general"),
                content="damn",
                delete=AsyncMock(),
            )

            cog.server_config_store = SimpleNamespace(get_profanity_log_channel_id=MagicMock(return_value=999))

            await cog.on_message(message)
            review_view = log_channel.send.await_args.kwargs["view"]
            ignore_button = next(item for item in review_view.children if item.label == "Ignore")
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=777, mention="<@777>"),
                response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock()),
            )
            await ignore_button.callback(interaction)

            message.delete.assert_not_awaited()
            author.send.assert_not_awaited()
            interaction.response.edit_message.assert_awaited_once()
            self.assertEqual(log_channel.send.await_count, 2)
            log_embed = log_channel.send.await_args_list[1].kwargs["embed"]
            self.assertIn("Ignore", log_embed.description)

    async def test_on_message_edit_flags_when_profanity_is_added(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            words_path = Path(temp_dir) / "profanity_words.json"
            words_path.write_text(json.dumps(["fuck"]), encoding="utf-8")

            cog = ProfanityCog(MagicMock(), blocked_words_path=words_path)

            log_channel = SimpleNamespace(send=AsyncMock())
            guild = SimpleNamespace(id=321, name="RPA", get_channel=MagicMock(return_value=log_channel))
            author = SimpleNamespace(id=456, bot=False, mention="<@456>", send=AsyncMock())
            before = SimpleNamespace(content="hello there")
            after = SimpleNamespace(
                author=author,
                webhook_id=None,
                guild=guild,
                channel=SimpleNamespace(mention="#general", send=AsyncMock()),
                content="hello fuck",
                delete=AsyncMock(),
            )

            cog.server_config_store = SimpleNamespace(get_profanity_log_channel_id=MagicMock(return_value=999))

            await cog.on_message_edit(before, after)

            after.delete.assert_not_awaited()
            author.send.assert_not_awaited()
            log_channel.send.assert_awaited_once()

    async def test_on_message_edit_skips_when_content_did_not_change(self) -> None:
        cog = ProfanityCog(MagicMock())
        cog._handle_message_for_profanity = AsyncMock()

        before = SimpleNamespace(content="same")
        after = SimpleNamespace(content="same")

        await cog.on_message_edit(before, after)

        cog._handle_message_for_profanity.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
