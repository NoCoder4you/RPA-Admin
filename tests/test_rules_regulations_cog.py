"""Unit tests for the text `rules` community rules command cog."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock

try:
    from COGS.RulesRegulationsCog import RulesRegulationsCog
except Exception:  # pragma: no cover - environment without discord.py
    RulesRegulationsCog = None


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
        ctx = AsyncMock()
        ctx.me = MagicMock()
        ctx.me.display_avatar = MagicMock()
        ctx.me.display_avatar.url = "https://cdn.example.com/live-bot-avatar.png"
        ctx.me.display_name = "RPA Foundation Bot"

        await cog.rules.callback(cog, ctx)

        # There are 10 total embeds, and the text command sends one message per embed.
        self.assertEqual(ctx.send.await_count, 10)

        # Validate that each outbound embed inherits the bot avatar thumbnail.
        sent_embeds = [call.kwargs["embed"] for call in ctx.send.await_args_list]
        self.assertTrue(all(embed.thumbnail.url == "https://cdn.example.com/live-bot-avatar.png" for embed in sent_embeds))
        self.assertTrue(all(embed.footer.text == "RPA Foundation Bot" for embed in sent_embeds))


if __name__ == "__main__":
    unittest.main()
