"""Unit tests for the `/ban` moderation slash command cog."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

try:
    from COGS.BanCog import BanCog
except ModuleNotFoundError:
    BanCog = None


@unittest.skipIf(BanCog is None, "discord.py is not installed in the test environment")
class BanCogTests(unittest.IsolatedAsyncioTestCase):
    """Validate key moderation outcomes for the ban slash command."""

    async def test_ban_successfully_bans_target_member(self) -> None:
        bot = MagicMock()
        cog = BanCog(bot)

        target_member = SimpleNamespace(
            id=202,
            mention="<@202>",
            top_role=1,
            send=AsyncMock(),
            ban=AsyncMock(),
        )
        invoking_member = SimpleNamespace(id=101, top_role=5)
        bot_member = SimpleNamespace(top_role=10)

        interaction = SimpleNamespace(
            user=invoking_member,
            guild=SimpleNamespace(owner_id=999, me=bot_member, name="Test Guild"),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        await cog.ban.callback(cog, interaction, target_member, "major rule violation")

        target_member.send.assert_awaited_once_with(
            "You are being banned from **Test Guild** by **namespace(id=101, top_role=5)** for: **major rule violation**"
        )
        target_member.ban.assert_awaited_once()
        ban_reason = target_member.ban.await_args.kwargs.get("reason", "")
        self.assertTrue(ban_reason.endswith(" - major rule violation"))
        interaction.response.send_message.assert_awaited_once_with(
            "✅ Banned <@202> for reason: major rule violation\nThe user was notified via DM before the ban.",
            ephemeral=True,
        )

    async def test_ban_rejects_self_ban(self) -> None:
        bot = MagicMock()
        cog = BanCog(bot)

        target_member = SimpleNamespace(id=101, mention="<@101>", top_role=1, send=AsyncMock(), ban=AsyncMock())
        invoking_member = SimpleNamespace(id=101, top_role=5)

        interaction = SimpleNamespace(
            user=invoking_member,
            guild=SimpleNamespace(owner_id=999, me=SimpleNamespace(top_role=10), name="Test Guild"),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        await cog.ban.callback(cog, interaction, target_member, "bad behavior")

        target_member.ban.assert_not_awaited()
        target_member.send.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once_with("You cannot ban yourself.", ephemeral=True)

    async def test_ban_continues_when_dm_cannot_be_sent(self) -> None:
        bot = MagicMock()
        cog = BanCog(bot)

        target_member = SimpleNamespace(
            id=202,
            mention="<@202>",
            top_role=1,
            send=AsyncMock(side_effect=Exception("dm closed")),
            ban=AsyncMock(),
        )
        invoking_member = SimpleNamespace(id=101, top_role=5)

        interaction = SimpleNamespace(
            user=invoking_member,
            guild=SimpleNamespace(owner_id=999, me=SimpleNamespace(top_role=10), name="Test Guild"),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        await cog.ban.callback(cog, interaction, target_member, "major rule violation")

        target_member.ban.assert_awaited_once()
        interaction.response.send_message.assert_awaited_once_with(
            "✅ Banned <@202> for reason: major rule violation\nI could not DM the user before ban.",
            ephemeral=True,
        )


if __name__ == "__main__":
    unittest.main()
