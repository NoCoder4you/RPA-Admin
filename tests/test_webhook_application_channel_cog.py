"""Unit tests for the webhook-driven application channel creation cog."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

try:
    from COGS.WebhookApplicationChannelCog import ChannelCreateRequest, WebhookApplicationChannelCog
except ModuleNotFoundError:
    WebhookApplicationChannelCog = None
    ChannelCreateRequest = None


@unittest.skipIf(WebhookApplicationChannelCog is None, "discord.py is not installed in the test environment")
class WebhookApplicationChannelCogTests(unittest.IsolatedAsyncioTestCase):
    """Validate webhook parsing, channel naming, and channel creation flow."""

    def test_parse_channel_create_request_reads_expected_webhook_command(self) -> None:
        request = WebhookApplicationChannelCog.parse_channel_create_request("RPA channelcreate ia John Doe")

        self.assertEqual(request, ChannelCreateRequest(unit_prefix="IA", username="John Doe"))

    def test_parse_channel_create_request_rejects_non_matching_content(self) -> None:
        request = WebhookApplicationChannelCog.parse_channel_create_request("RPA somethingelse IA John Doe")

        self.assertIsNone(request)

    def test_build_channel_name_normalizes_username_for_discord(self) -> None:
        channel_name = WebhookApplicationChannelCog.build_channel_name("John Doe!!!")

        self.assertEqual(channel_name, "john-doe")

    def test_build_application_embed_provides_resources_instead_of_questions(self) -> None:
        embed = WebhookApplicationChannelCog.build_application_embed(
            ChannelCreateRequest(unit_prefix="HR", username="Jane Smith")
        )

        self.assertEqual(embed.title, "HR Application Resources")
        self.assertEqual(embed.fields[2].name, "Purpose")
        self.assertIn("not for the bot to ask application questions", embed.fields[2].value)
        self.assertEqual(embed.fields[3].name, "Resource Step 1")

    async def test_on_message_creates_channel_and_posts_application_embed(self) -> None:
        cog = WebhookApplicationChannelCog(MagicMock())

        created_channel = SimpleNamespace(send=AsyncMock())
        guild = SimpleNamespace(create_text_channel=AsyncMock(return_value=created_channel))
        source_channel = SimpleNamespace(category=SimpleNamespace(name="Applications"))
        message = SimpleNamespace(
            webhook_id=12345,
            content="RPA channelcreate HR Jane Smith",
            guild=guild,
            channel=source_channel,
        )

        await cog.on_message(message)

        guild.create_text_channel.assert_awaited_once()
        create_call = guild.create_text_channel.await_args
        self.assertEqual(create_call.args[0], "jane-smith")
        self.assertIs(create_call.kwargs["category"], source_channel.category)
        self.assertIn("HR applicant Jane Smith", create_call.kwargs["reason"])

        created_channel.send.assert_awaited_once()
        sent_embed = created_channel.send.await_args.kwargs["embed"]
        self.assertEqual(sent_embed.title, "HR Application Resources")
        self.assertEqual(sent_embed.fields[0].name, "Applicant Username")
        self.assertEqual(sent_embed.fields[0].value, "Jane Smith")
        self.assertEqual(sent_embed.fields[1].name, "Unit Prefix")
        self.assertEqual(sent_embed.fields[1].value, "HR")
        self.assertEqual(sent_embed.fields[2].name, "Purpose")

    async def test_on_message_ignores_non_webhook_messages(self) -> None:
        cog = WebhookApplicationChannelCog(MagicMock())

        guild = SimpleNamespace(create_text_channel=AsyncMock())
        message = SimpleNamespace(
            webhook_id=None,
            content="RPA channelcreate HR Jane Smith",
            guild=guild,
            channel=SimpleNamespace(category=None),
        )

        await cog.on_message(message)

        guild.create_text_channel.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
