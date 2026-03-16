"""Unit tests for the `/purge` moderation slash command cog."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

try:
    from COGS.PurgeCog import PurgeCog
except ModuleNotFoundError:
    PurgeCog = None


@unittest.skipIf(PurgeCog is None, "discord.py is not installed in the test environment")
class PurgeCogTests(unittest.IsolatedAsyncioTestCase):
    """Validate key moderation outcomes for the purge slash command."""

    async def test_purge_deletes_user_messages_only(self) -> None:
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

        await cog.purge.callback(cog, interaction, "users", 25)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        channel.purge.assert_awaited_once()
        purge_check = channel.purge.await_args.kwargs["check"]

        # Regular users should match the `users` filter.
        user_message = SimpleNamespace(author=SimpleNamespace(bot=False), webhook_id=None)
        self.assertTrue(purge_check(user_message))

        # Bots and webhooks should be ignored in the `users` filter mode.
        bot_message = SimpleNamespace(author=SimpleNamespace(bot=True), webhook_id=None)
        webhook_message = SimpleNamespace(author=SimpleNamespace(bot=False), webhook_id=123)
        self.assertFalse(purge_check(bot_message))
        self.assertFalse(purge_check(webhook_message))

        interaction.followup.send.assert_awaited_once_with(
            "✅ Deleted **2** message(s) using filter **users** from the last **25** message(s).",
            ephemeral=True,
        )

    async def test_purge_deletes_bot_messages_only(self) -> None:
        bot = MagicMock()
        cog = PurgeCog(bot)

        channel = SimpleNamespace(purge=AsyncMock(return_value=[object()]))
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            channel=channel,
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await cog.purge.callback(cog, interaction, "bots", 10)

        purge_check = channel.purge.await_args.kwargs["check"]

        # Bot-authored and webhook messages should match in `bots` mode.
        bot_message = SimpleNamespace(author=SimpleNamespace(bot=True), webhook_id=None)
        webhook_message = SimpleNamespace(author=SimpleNamespace(bot=False), webhook_id=987)
        self.assertTrue(purge_check(bot_message))
        self.assertTrue(purge_check(webhook_message))

        # Human-authored non-webhook messages should not match.
        user_message = SimpleNamespace(author=SimpleNamespace(bot=False), webhook_id=None)
        self.assertFalse(purge_check(user_message))

    async def test_purge_requires_guild_channel_context(self) -> None:
        bot = MagicMock()
        cog = PurgeCog(bot)

        interaction = SimpleNamespace(
            guild=None,
            channel=None,
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await cog.purge.callback(cog, interaction, "all", 5)

        interaction.response.send_message.assert_awaited_once_with(
            "This command can only be used in a server text channel.",
            ephemeral=True,
        )
        interaction.response.defer.assert_not_awaited()
        interaction.followup.send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
