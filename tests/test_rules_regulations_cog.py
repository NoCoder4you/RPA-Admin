"""Unit tests for the text `rules` community rules command cog."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

try:
    from COGS.RulesRegulationsCog import (
        AWAITING_VERIFICATION_CHANNEL_ID,
        RulesRegulationsCog,
        WHITE_CHECK_MARK_EMOJI,
    )
except Exception:  # pragma: no cover - environment without discord.py
    RulesRegulationsCog = None
    AWAITING_VERIFICATION_CHANNEL_ID = 1479391662076723224
    WHITE_CHECK_MARK_EMOJI = "✅"


@unittest.skipIf(RulesRegulationsCog is None, "discord.py is not installed in the test environment")
class RulesRegulationsCogTests(unittest.IsolatedAsyncioTestCase):
    """Coverage for rules embed construction and dispatch behavior."""

    def test_build_rule_embeds_returns_expected_sections(self) -> None:
        """Ensure one embed is created per section plus one closing acknowledgement embed."""

        cog = RulesRegulationsCog(bot=MagicMock())

        embeds = cog._build_rule_embeds(
            thumbnail_url="https://cdn.example.com/bot.png",
            footer_text="RPA Assistant",
        )

        self.assertEqual(len(embeds), 10)
        self.assertEqual(embeds[0].title, "1) Zero Tolerance for Hate or Harassment")
        self.assertEqual(embeds[-1].title, "Agreement and Enforcement")
        self.assertEqual(embeds[0].thumbnail.url, "https://cdn.example.com/bot.png")
        self.assertEqual(embeds[-1].thumbnail.url, "https://cdn.example.com/bot.png")
        self.assertEqual(embeds[0].footer.text, "RPA Assistant")
        self.assertEqual(embeds[-1].footer.text, "RPA Assistant")

    async def test_rules_command_sends_one_message_per_rule_embed(self) -> None:
        """Validate the command sends each rule section as an individual embed message."""

        cog = RulesRegulationsCog(bot=MagicMock())
        cog.server_config_store = MagicMock()
        ctx = AsyncMock()
        ctx.me = MagicMock()
        ctx.me.display_avatar = MagicMock()
        ctx.me.display_avatar.url = "https://cdn.example.com/live-bot-avatar.png"
        ctx.me.display_name = "RPA Foundation Bot"

        sent_messages: list[AsyncMock] = []

        async def send_embed(*args, **kwargs):
            message = AsyncMock()
            message.id = 9000 + len(sent_messages)
            message.embed = kwargs["embed"]
            sent_messages.append(message)
            return message

        ctx.send.side_effect = send_embed

        await cog.rules.callback(cog, ctx)

        # There are 10 total embeds, and the text command sends one message per embed.
        self.assertEqual(ctx.send.await_count, 10)

        # Validate that each outbound embed inherits the bot avatar thumbnail.
        sent_embeds = [call.kwargs["embed"] for call in ctx.send.await_args_list]
        self.assertTrue(all(embed.thumbnail.url == "https://cdn.example.com/live-bot-avatar.png" for embed in sent_embeds))
        self.assertTrue(
            all(embed.footer.text == "Royal Protection Agency - RPA Foundation Bot" for embed in sent_embeds)
        )

        # The final agreement embed should receive the persistent white check mark trigger.
        sent_messages[-1].add_reaction.assert_awaited_once_with(WHITE_CHECK_MARK_EMOJI)
        cog.server_config_store.set_rules_acknowledgement_message_id.assert_called_once_with(sent_messages[-1].id)

    async def test_reaction_listener_ignores_non_configured_messages(self) -> None:
        """Ensure the reaction listener exits immediately unless the saved rules message was targeted."""

        bot = MagicMock()
        bot.user = MagicMock(id=999)
        cog = RulesRegulationsCog(bot=bot)
        cog.server_config_store = MagicMock()
        cog.server_config_store.get_rules_acknowledgement_message_id.return_value = 555

        payload = MagicMock(guild_id=123, user_id=111, message_id=444, channel_id=222, emoji=WHITE_CHECK_MARK_EMOJI)

        await cog.on_raw_reaction_add(payload)

        bot.get_guild.assert_not_called()
        bot.get_channel.assert_not_called()

    async def test_reaction_listener_removes_non_checkmark_reactions_from_rules_message(self) -> None:
        """Keep the configured rules acknowledgement post limited to the intended white check mark reaction."""

        bot = MagicMock()
        bot.user = MagicMock(id=999)

        member = MagicMock()
        guild = MagicMock()
        guild.get_member.return_value = member
        bot.get_guild.return_value = guild

        message = AsyncMock()
        channel = MagicMock()
        channel.fetch_message = AsyncMock(return_value=message)
        bot.get_channel.return_value = channel

        cog = RulesRegulationsCog(bot=bot)
        cog.server_config_store = MagicMock()
        cog.verified_store = MagicMock(is_verified=MagicMock(return_value=False))
        cog.server_config_store.get_rules_acknowledgement_message_id.return_value = 555

        payload = MagicMock(guild_id=123, user_id=111, message_id=555, channel_id=222, emoji="🔥")

        await cog.on_raw_reaction_add(payload)

        channel.fetch_message.assert_awaited_once_with(555)
        message.remove_reaction.assert_awaited_once_with("🔥", member)

    async def test_reaction_listener_grants_awaiting_verification_role_for_unverified_checkmark(self) -> None:
        """Grant the staging role only for members who acknowledged rules and are not yet verified."""

        bot = MagicMock()
        bot.user = MagicMock(id=999)

        role = SimpleNamespace(name="Awaiting Verification")
        member = MagicMock(roles=[], add_roles=AsyncMock())
        guild = MagicMock()
        guild.roles = [role]
        guild.get_member.return_value = member
        verification_channel = MagicMock(send=AsyncMock())
        guild.get_channel.return_value = verification_channel
        bot.get_guild.return_value = guild

        message = AsyncMock()
        channel = MagicMock()
        channel.fetch_message = AsyncMock(return_value=message)
        bot.get_channel.return_value = channel

        cog = RulesRegulationsCog(bot=bot)
        cog.server_config_store = MagicMock()
        cog.verified_store = MagicMock(is_verified=MagicMock(return_value=False))
        cog.server_config_store.get_rules_acknowledgement_message_id.return_value = 555

        payload = MagicMock(guild_id=123, user_id=111, message_id=555, channel_id=222, emoji=WHITE_CHECK_MARK_EMOJI)

        await cog.on_raw_reaction_add(payload)

        message.remove_reaction.assert_awaited_once_with(WHITE_CHECK_MARK_EMOJI, member)
        cog.verified_store.is_verified.assert_called_once_with("111")
        member.add_roles.assert_awaited_once_with(
            role,
            reason="Reacted with white check mark on rules acknowledgement message",
        )
        guild.get_channel.assert_called_once_with(AWAITING_VERIFICATION_CHANNEL_ID)
        verification_channel.send.assert_awaited_once()
        self.assertEqual(verification_channel.send.await_args.kwargs["content"], member.mention)
        self.assertEqual(
            verification_channel.send.await_args.kwargs["embed"].title,
            "Awaiting Verification",
        )

    async def test_reaction_listener_skips_role_for_verified_members_and_only_cleans_up_reaction(self) -> None:
        """Verified users should not be re-staged after acknowledging the rules message."""

        bot = MagicMock()
        bot.user = MagicMock(id=999)

        role = SimpleNamespace(name="Awaiting Verification")
        member = MagicMock(roles=[], add_roles=AsyncMock())
        guild = MagicMock()
        guild.roles = [role]
        guild.get_member.return_value = member
        guild.get_channel.return_value = MagicMock(send=AsyncMock())
        bot.get_guild.return_value = guild

        message = AsyncMock()
        channel = MagicMock()
        channel.fetch_message = AsyncMock(return_value=message)
        bot.get_channel.return_value = channel

        cog = RulesRegulationsCog(bot=bot)
        cog.server_config_store = MagicMock()
        cog.verified_store = MagicMock(is_verified=MagicMock(return_value=True))
        cog.server_config_store.get_rules_acknowledgement_message_id.return_value = 555

        payload = MagicMock(guild_id=123, user_id=111, message_id=555, channel_id=222, emoji=WHITE_CHECK_MARK_EMOJI)

        await cog.on_raw_reaction_add(payload)

        message.remove_reaction.assert_awaited_once_with(WHITE_CHECK_MARK_EMOJI, member)
        cog.verified_store.is_verified.assert_called_once_with("111")
        member.add_roles.assert_not_awaited()

    async def test_send_awaiting_verification_embed_skips_when_channel_is_missing(self) -> None:
        """Avoid raising errors if the configured verification help channel is unavailable."""

        cog = RulesRegulationsCog(bot=MagicMock())
        guild = MagicMock()
        guild.get_channel.return_value = None
        member = MagicMock(mention="<@111>")

        await cog._send_awaiting_verification_embed(guild=guild, member=member)

        guild.get_channel.assert_called_once_with(AWAITING_VERIFICATION_CHANNEL_ID)


if __name__ == "__main__":
    unittest.main()
