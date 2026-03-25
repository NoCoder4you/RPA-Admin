"""Unit tests for the `/kick` moderation slash command cog."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

try:
    import discord
    from COGS.MiscKick import KickCog
except ModuleNotFoundError:
    discord = None
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
            send=AsyncMock(),
        )
        invoking_member = SimpleNamespace(id=101, top_role=5)
        bot_member = SimpleNamespace(top_role=10)

        interaction = SimpleNamespace(
            user=invoking_member,
            guild=SimpleNamespace(owner_id=999, me=bot_member, name="Test Guild"),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        await cog.kick.callback(cog, interaction, target_member, "rule violation")

        target_member.kick.assert_awaited_once()
        kick_reason = target_member.kick.await_args.kwargs.get("reason", "")
        self.assertTrue(kick_reason.endswith(" - rule violation"))
        target_member.send.assert_awaited_once()
        dm_embed = target_member.send.await_args.kwargs["embed"]
        self.assertIsInstance(dm_embed, discord.Embed)
        self.assertIn("You are being kicked from **Test Guild**.", dm_embed.description)
        self.assertIn("**Reason:** rule violation", dm_embed.description)

        interaction.response.send_message.assert_awaited_once()
        send_kwargs = interaction.response.send_message.await_args.kwargs
        self.assertTrue(send_kwargs["ephemeral"])
        success_embed = send_kwargs["embed"]
        self.assertEqual(success_embed.title, "Member Kicked")
        self.assertEqual(success_embed.description, "✅ Kicked <@202>")
        self.assertEqual(success_embed.fields[0].name, "Reason")
        self.assertEqual(success_embed.fields[0].value, "rule violation")
        self.assertEqual(success_embed.fields[1].name, "DM Status")
        self.assertEqual(success_embed.fields[1].value, "I sent them a DM with the reason before kicking.")

    async def test_kick_rejects_self_kick(self) -> None:
        bot = MagicMock()
        cog = KickCog(bot)

        target_member = SimpleNamespace(id=101, mention="<@101>", top_role=1, kick=AsyncMock(), send=AsyncMock())
        invoking_member = SimpleNamespace(id=101, top_role=5)

        interaction = SimpleNamespace(
            user=invoking_member,
            guild=SimpleNamespace(owner_id=999, me=SimpleNamespace(top_role=10)),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        await cog.kick.callback(cog, interaction, target_member, "bad behavior")

        target_member.kick.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once_with("You cannot kick yourself.", ephemeral=True)

    async def test_kick_continues_when_dm_fails(self) -> None:
        bot = MagicMock()
        cog = KickCog(bot)

        target_member = SimpleNamespace(
            id=202,
            mention="<@202>",
            top_role=1,
            send=AsyncMock(side_effect=RuntimeError("dm failed")),
            kick=AsyncMock(),
        )
        invoking_member = SimpleNamespace(id=101, top_role=5)
        bot_member = SimpleNamespace(top_role=10)

        interaction = SimpleNamespace(
            user=invoking_member,
            guild=SimpleNamespace(owner_id=999, me=bot_member, name="Test Guild"),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        await cog.kick.callback(cog, interaction, target_member, "rule violation")

        target_member.kick.assert_awaited_once()
        interaction.response.send_message.assert_awaited_once()
        send_kwargs = interaction.response.send_message.await_args.kwargs
        self.assertTrue(send_kwargs["ephemeral"])
        success_embed = send_kwargs["embed"]
        self.assertEqual(
            success_embed.fields[1].value,
            "I could not DM them first (likely due to their privacy settings).",
        )


if __name__ == "__main__":
    unittest.main()
