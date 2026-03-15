"""Unit tests for the `/kick` moderation slash command cog."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

try:
    from COGS.KickCog import KickCog
except ModuleNotFoundError:
    KickCog = None


@unittest.skipIf(KickCog is None, "discord.py is not installed in the test environment")
class KickCogTests(unittest.IsolatedAsyncioTestCase):
    """Validate key moderation outcomes for the kick slash command."""

    async def test_kick_successfully_kicks_target_member(self) -> None:
        bot = MagicMock()
        cog = KickCog(bot)

        target_member = SimpleNamespace(
            id=202,
            mention="<@202>",
            top_role=1,
            kick=AsyncMock(),
        )
        invoking_member = SimpleNamespace(id=101, top_role=5)
        bot_member = SimpleNamespace(top_role=10)

        interaction = SimpleNamespace(
            user=invoking_member,
            guild=SimpleNamespace(owner_id=999, me=bot_member),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        await cog.kick.callback(cog, interaction, target_member, "rule violation")

        target_member.kick.assert_awaited_once()
        kick_reason = target_member.kick.await_args.kwargs.get("reason", "")
        self.assertTrue(kick_reason.endswith(" - rule violation"))
        interaction.response.send_message.assert_awaited_once_with(
            "✅ Kicked <@202> for reason: rule violation",
            ephemeral=True,
        )

    async def test_kick_rejects_self_kick(self) -> None:
        bot = MagicMock()
        cog = KickCog(bot)

        target_member = SimpleNamespace(id=101, mention="<@101>", top_role=1, kick=AsyncMock())
        invoking_member = SimpleNamespace(id=101, top_role=5)

        interaction = SimpleNamespace(
            user=invoking_member,
            guild=SimpleNamespace(owner_id=999, me=SimpleNamespace(top_role=10)),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        await cog.kick.callback(cog, interaction, target_member, "bad behavior")

        target_member.kick.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once_with("You cannot kick yourself.", ephemeral=True)


if __name__ == "__main__":
    unittest.main()
