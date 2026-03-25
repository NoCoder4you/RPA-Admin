"""Unit tests for the `/ban` moderation slash command cog."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

try:
    import discord
    from COGS.MiscBan import BanCog
except ModuleNotFoundError:
    discord = None
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

        target_member.send.assert_awaited_once()
        dm_embed = target_member.send.await_args.kwargs["embed"]
        self.assertIsInstance(dm_embed, discord.Embed)
        self.assertIn("You are being banned from **Test Guild**.", dm_embed.description)
        self.assertIn("**Reason:** major rule violation", dm_embed.description)

        target_member.ban.assert_awaited_once()
        ban_reason = target_member.ban.await_args.kwargs.get("reason", "")
        self.assertTrue(ban_reason.endswith(" - major rule violation"))

        interaction.response.send_message.assert_awaited_once()
        send_kwargs = interaction.response.send_message.await_args.kwargs
        self.assertTrue(send_kwargs["ephemeral"])
        success_embed = send_kwargs["embed"]
        self.assertEqual(success_embed.title, "Member Banned")
        self.assertEqual(success_embed.description, "✅ Banned <@202>")
        self.assertEqual(success_embed.fields[0].name, "Reason")
        self.assertEqual(success_embed.fields[0].value, "major rule violation")
        self.assertEqual(success_embed.fields[1].name, "DM Status")
        self.assertEqual(success_embed.fields[1].value, "The user was notified via DM before the ban.")

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
        interaction.response.send_message.assert_awaited_once()
        send_kwargs = interaction.response.send_message.await_args.kwargs
        self.assertTrue(send_kwargs["ephemeral"])
        success_embed = send_kwargs["embed"]
        self.assertEqual(success_embed.fields[1].value, "I could not DM the user before ban.")


if __name__ == "__main__":
    unittest.main()
