"""Unit tests for grouped `/purge` moderation slash command subcommands."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

try:
    from COGS.MiscPurge import PurgeCog
except ModuleNotFoundError:
    PurgeCog = None


@unittest.skipIf(PurgeCog is None, "discord.py is not installed in the test environment")
class PurgeCogTests(unittest.IsolatedAsyncioTestCase):
    """Validate key moderation outcomes for grouped purge slash commands."""

    async def test_purge_users_deletes_human_messages_only(self) -> None:
        bot = MagicMock()
        cog = PurgeCog(bot)

        deleted_messages = [object(), object()]
        channel = SimpleNamespace(purge=AsyncMock(return_value=deleted_messages))
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            channel=channel,
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await cog.purge_users.callback(cog, interaction, 500)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        channel.purge.assert_awaited_once()
        self.assertEqual(channel.purge.await_args.kwargs["limit"], 500)
        purge_check = channel.purge.await_args.kwargs["check"]

        # Regular users should match the `users` filter.
        user_message = SimpleNamespace(author=SimpleNamespace(bot=False), webhook_id=None)
        self.assertTrue(purge_check(user_message))

        # Bots and webhooks should be ignored in the `users` subcommand.
        bot_message = SimpleNamespace(author=SimpleNamespace(bot=True), webhook_id=None)
        webhook_message = SimpleNamespace(author=SimpleNamespace(bot=False), webhook_id=123)
        self.assertFalse(purge_check(bot_message))
        self.assertFalse(purge_check(webhook_message))

        interaction.followup.send.assert_awaited_once_with(
            "✅ Deleted **2** message(s) for **human user messages** from the last **500** message(s).",
            ephemeral=True,
        )

    async def test_purge_bots_deletes_bot_and_webhook_messages(self) -> None:
        bot = MagicMock()
        cog = PurgeCog(bot)

        channel = SimpleNamespace(purge=AsyncMock(return_value=[object()]))
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            channel=channel,
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await cog.purge_bots.callback(cog, interaction, 10)

        purge_check = channel.purge.await_args.kwargs["check"]

        # Bot-authored and webhook messages should match in `bots` mode.
        bot_message = SimpleNamespace(author=SimpleNamespace(bot=True), webhook_id=None)
        webhook_message = SimpleNamespace(author=SimpleNamespace(bot=False), webhook_id=987)
        self.assertTrue(purge_check(bot_message))
        self.assertTrue(purge_check(webhook_message))

        # Human-authored non-webhook messages should not match.
        user_message = SimpleNamespace(author=SimpleNamespace(bot=False), webhook_id=None)
        self.assertFalse(purge_check(user_message))

    async def test_purge_member_deletes_specific_member_messages_only(self) -> None:
        bot = MagicMock()
        cog = PurgeCog(bot)

        channel = SimpleNamespace(purge=AsyncMock(return_value=[object(), object(), object()]))
        target_member = SimpleNamespace(id=777, mention="<@777>")

        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            channel=channel,
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await cog.purge_member.callback(cog, interaction, target_member, 200)

        purge_check = channel.purge.await_args.kwargs["check"]

        # Only the selected member's messages should be deleted.
        selected_member_message = SimpleNamespace(author=SimpleNamespace(id=777))
        other_member_message = SimpleNamespace(author=SimpleNamespace(id=888))
        self.assertTrue(purge_check(selected_member_message))
        self.assertFalse(purge_check(other_member_message))

        interaction.followup.send.assert_awaited_once_with(
            "✅ Deleted **3** message(s) for **messages from <@777>** from the last **200** message(s).",
            ephemeral=True,
        )

    async def test_purge_all_deletes_everything_in_window(self) -> None:
        bot = MagicMock()
        cog = PurgeCog(bot)

        channel = SimpleNamespace(purge=AsyncMock(return_value=[object()]))
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            channel=channel,
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await cog.purge_all.callback(cog, interaction, 15)

        purge_check = channel.purge.await_args.kwargs["check"]

        # In `all` mode, every message in the inspected range should be eligible.
        any_message = SimpleNamespace(author=SimpleNamespace(bot=False), webhook_id=None)
        self.assertTrue(purge_check(any_message))

    async def test_purge_requires_guild_channel_context(self) -> None:
        bot = MagicMock()
        cog = PurgeCog(bot)

        interaction = SimpleNamespace(
            guild=None,
            channel=None,
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await cog.purge_users.callback(cog, interaction, 5)

        interaction.response.send_message.assert_awaited_once_with(
            "This command can only be used in a server text channel.",
            ephemeral=True,
        )
        interaction.response.defer.assert_not_awaited()
        interaction.followup.send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
