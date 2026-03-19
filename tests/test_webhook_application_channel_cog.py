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

    def test_parse_channel_create_request_rejects_unknown_prefix(self) -> None:
        request = WebhookApplicationChannelCog.parse_channel_create_request("RPA channelcreate HR Jane Smith")

        self.assertIsNone(request)

    def test_build_channel_name_normalizes_prefix_and_username_for_discord(self) -> None:
        channel_name = WebhookApplicationChannelCog.build_channel_name("ET", "John Doe!!!")

        self.assertEqual(channel_name, "et-john-doe")

    async def test_get_archived_webhook_message_returns_latest_embed_message(self) -> None:
        archive_channel = SimpleNamespace()
        bot = SimpleNamespace(get_channel=lambda channel_id: archive_channel if channel_id == 999 else None)
        cog = WebhookApplicationChannelCog(bot)
        cog.server_config_store = SimpleNamespace(get_webhook_archive_channel_id=lambda: 999)
        latest_embed_message = SimpleNamespace(embeds=[SimpleNamespace(copy=lambda: "copied")])

        async def history(*, limit: int):
            self.assertEqual(limit, 25)
            for archived_message in [SimpleNamespace(embeds=[]), latest_embed_message]:
                yield archived_message

        archive_channel.history = history

        archived_message = await cog._get_archived_webhook_message()

        self.assertIs(archived_message, latest_embed_message)

    async def test_get_archived_webhook_message_returns_none_without_configured_archive_channel(self) -> None:
        cog = WebhookApplicationChannelCog(SimpleNamespace(get_channel=lambda _channel_id: None))
        cog.server_config_store = SimpleNamespace(get_webhook_archive_channel_id=lambda: None)

        archived_message = await cog._get_archived_webhook_message()

        self.assertIsNone(archived_message)

    async def test_send_archived_webhook_message_prefers_native_forward(self) -> None:
        archived_message = SimpleNamespace(forward=AsyncMock(), embeds=[SimpleNamespace(copy=lambda: "copied")], content="")
        cog = WebhookApplicationChannelCog(MagicMock())
        cog._get_archived_webhook_message = AsyncMock(return_value=archived_message)
        created_channel = SimpleNamespace(send=AsyncMock())

        forwarded = await cog._send_archived_webhook_message(created_channel)

        self.assertTrue(forwarded)
        archived_message.forward.assert_awaited_once_with(created_channel)
        created_channel.send.assert_not_awaited()

    async def test_send_archived_webhook_message_falls_back_to_embed_copy_when_forward_missing(self) -> None:
        copied_embed = object()
        archived_message = SimpleNamespace(
            embeds=[SimpleNamespace(copy=lambda: copied_embed)],
            content="Archived resource message",
        )
        cog = WebhookApplicationChannelCog(MagicMock())
        cog._get_archived_webhook_message = AsyncMock(return_value=archived_message)
        created_channel = SimpleNamespace(send=AsyncMock())

        forwarded = await cog._send_archived_webhook_message(created_channel)

        self.assertTrue(forwarded)
        created_channel.send.assert_awaited_once_with(content="Archived resource message", embed=copied_embed)

    async def test_delete_channel_create_message_ignores_delete_failures(self) -> None:
        cog = WebhookApplicationChannelCog(MagicMock())
        message = SimpleNamespace(delete=AsyncMock(side_effect=AttributeError()))

        await cog._delete_channel_create_message(message)

        message.delete.assert_awaited_once_with()

    async def test_on_message_creates_prefixed_channel_and_forwards_archived_webhook_message(self) -> None:
        cog = WebhookApplicationChannelCog(MagicMock())
        cog._send_archived_webhook_message = AsyncMock(return_value=True)

        created_channel = SimpleNamespace(delete=AsyncMock())
        guild = SimpleNamespace(create_text_channel=AsyncMock(return_value=created_channel))
        source_channel = SimpleNamespace(category=SimpleNamespace(name="Applications"))
        message = SimpleNamespace(
            webhook_id=12345,
            content="RPA channelcreate ET Jane Smith",
            guild=guild,
            channel=source_channel,
            delete=AsyncMock(),
        )

        await cog.on_message(message)

        guild.create_text_channel.assert_awaited_once()
        create_call = guild.create_text_channel.await_args
        self.assertEqual(create_call.args[0], "et-jane-smith")
        self.assertIs(create_call.kwargs["category"], source_channel.category)
        self.assertIn("ET applicant Jane Smith", create_call.kwargs["reason"])

        cog._send_archived_webhook_message.assert_awaited_once_with(created_channel)
        message.delete.assert_awaited_once_with()
        created_channel.delete.assert_not_awaited()

    async def test_on_message_deletes_channel_when_archive_forward_fails(self) -> None:
        cog = WebhookApplicationChannelCog(MagicMock())
        cog._send_archived_webhook_message = AsyncMock(return_value=False)

        created_channel = SimpleNamespace(delete=AsyncMock())
        guild = SimpleNamespace(create_text_channel=AsyncMock(return_value=created_channel))
        message = SimpleNamespace(
            webhook_id=12345,
            content="RPA channelcreate ET Jane Smith",
            guild=guild,
            channel=SimpleNamespace(category=None),
            delete=AsyncMock(),
        )

        await cog.on_message(message)

        created_channel.delete.assert_awaited_once_with(
            reason="Failed to forward archived webhook resources after channel creation"
        )
        message.delete.assert_not_awaited()

    async def test_on_message_ignores_non_webhook_messages(self) -> None:
        cog = WebhookApplicationChannelCog(MagicMock())

        guild = SimpleNamespace(create_text_channel=AsyncMock())
        message = SimpleNamespace(
            webhook_id=None,
            content="RPA channelcreate ET Jane Smith",
            guild=guild,
            channel=SimpleNamespace(category=None),
        )

        await cog.on_message(message)

        guild.create_text_channel.assert_not_awaited()

    async def test_on_message_ignores_unknown_prefix_commands(self) -> None:
        cog = WebhookApplicationChannelCog(MagicMock())

        guild = SimpleNamespace(create_text_channel=AsyncMock())
        message = SimpleNamespace(
            webhook_id=9876,
            content="RPA channelcreate HR Jane Smith",
            guild=guild,
            channel=SimpleNamespace(category=None),
        )

        await cog.on_message(message)

        guild.create_text_channel.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
