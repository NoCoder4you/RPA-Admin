"""Unit tests for the anonymous `/embedmaker` slash command cog."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

try:
    from COGS.ServerEmbedMaker import EmbedMakerCog, EmbedMakerModal
except Exception:  # pragma: no cover - environment without discord.py
    EmbedMakerCog = None
    EmbedMakerModal = None


@unittest.skipIf(EmbedMakerCog is None, "discord.py is not installed in the test environment")
class EmbedMakerCogTests(unittest.IsolatedAsyncioTestCase):
    """Validate anonymous embed construction and slash-command behavior."""

    def test_build_embed_applies_requested_branding(self) -> None:
        """The anonymous embed should always use the bot thumbnail and required footer."""

        cog = EmbedMakerCog(bot=MagicMock())

        embed = cog._build_embed(
            title="Facility Alert",
            description="All guards report to briefing.",
            thumbnail_url="https://cdn.example.com/bot.png",
            color_hex="#112233",
        )

        self.assertEqual(embed.title, "Facility Alert")
        self.assertEqual(embed.description, "All guards report to briefing.")
        self.assertEqual(embed.thumbnail.url, "https://cdn.example.com/bot.png")
        self.assertEqual(embed.footer.text, "Royal Protection Agency - Royal Guard")
        self.assertEqual(embed.color.value, 0x112233)

    async def test_embedmaker_opens_modal_for_staff_input(self) -> None:
        """The slash command should open the modal instead of collecting long text in slash options."""

        cog = EmbedMakerCog(bot=MagicMock())
        interaction = SimpleNamespace(
            guild=SimpleNamespace(),
            channel=SimpleNamespace(),
            response=SimpleNamespace(send_modal=AsyncMock()),
        )

        await cog.embedmaker.callback(cog, interaction)

        interaction.response.send_modal.assert_awaited_once()
        sent_modal = interaction.response.send_modal.await_args.args[0]
        self.assertIsInstance(sent_modal, EmbedMakerModal)

    async def test_embedmaker_modal_posts_embed_and_confirms_privately(self) -> None:
        """Submitting the modal should send the embed to the channel and keep the author anonymous."""

        cog = EmbedMakerCog(bot=MagicMock())
        channel = SimpleNamespace(send=AsyncMock())
        interaction = SimpleNamespace(
            guild=SimpleNamespace(me=SimpleNamespace(display_avatar=SimpleNamespace(url="https://cdn.example.com/live-bot.png"))),
            channel=channel,
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        modal = EmbedMakerModal(cog)
        modal.embed_title._value = "Shift Update"
        modal.description._value = "Line up at the gate."
        modal.color._value = "#abcdef"

        await modal.on_submit(interaction)

        channel.send.assert_awaited_once()
        sent_embed = channel.send.await_args.kwargs["embed"]
        self.assertEqual(sent_embed.title, "Shift Update")
        self.assertEqual(sent_embed.description, "Line up at the gate.")
        self.assertEqual(sent_embed.thumbnail.url, "https://cdn.example.com/live-bot.png")
        self.assertEqual(sent_embed.footer.text, "Royal Protection Agency - Royal Guard")
        self.assertEqual(sent_embed.color.value, 0xABCDEF)
        interaction.response.send_message.assert_awaited_once_with("✅ Anonymous embed sent.", ephemeral=True)

    async def test_embedmaker_modal_rejects_invalid_hex_color(self) -> None:
        """Invalid color input should return an ephemeral validation error instead of sending the embed."""

        cog = EmbedMakerCog(bot=MagicMock())
        channel = SimpleNamespace(send=AsyncMock())
        interaction = SimpleNamespace(
            guild=SimpleNamespace(me=SimpleNamespace(display_avatar=SimpleNamespace(url="https://cdn.example.com/live-bot.png"))),
            channel=channel,
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        modal = EmbedMakerModal(cog)
        modal.embed_title._value = "Alert"
        modal.description._value = "Message"
        modal.color._value = "nothex"

        await modal.on_submit(interaction)

        channel.send.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once_with(
            "Please provide a valid 6-digit hex color such as `#ff0000`.",
            ephemeral=True,
        )

    async def test_embedmaker_requires_guild_channel_context(self) -> None:
        """DM usage should be blocked because the command is meant for server channels."""

        cog = EmbedMakerCog(bot=MagicMock())
        interaction = SimpleNamespace(
            guild=None,
            channel=None,
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        await cog.embedmaker.callback(cog, interaction)

        interaction.response.send_message.assert_awaited_once_with(
            "This command can only be used in a server channel.",
            ephemeral=True,
        )


if __name__ == "__main__":
    unittest.main()
