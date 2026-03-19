"""Unit tests for the Habbo username change cog workflow."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

try:
    import discord
    from discord.ext import commands
    from COGS.UsernameChangeCog import UsernameChangeCog, UsernameChangeRequestView
except ModuleNotFoundError as exc:  # pragma: no cover - environment-dependent test skip
    raise unittest.SkipTest(f"discord.py is not installed in this environment: {exc}")


class UsernameChangeCogTests(unittest.IsolatedAsyncioTestCase):
    """Validate saved username updates, nickname sync, reloads, and verification logging."""

    async def test_process_username_change_updates_store_nickname_reload_and_log(self) -> None:
        bot = SimpleNamespace(reload_extension=AsyncMock(), get_channel=lambda _cid: None)
        cog = UsernameChangeCog(bot=bot)
        cog.verified_store = SimpleNamespace(
            get_habbo_username=lambda _discord_id: "OldHabbo",
            save=MagicMock(),
        )
        cog.server_config_store = SimpleNamespace(
            get_request_channel_id=lambda: 1479465446632853524,
            get_admin_role_id=lambda: 1484029753185931336,
        )
        cog._sync_member_nickname = AsyncMock(return_value="Nickname updated to verified Habbo username.")
        cog._reload_autoroles_cog = AsyncMock(return_value="Reloaded AutoRoles cog successfully.")
        cog._send_verification_log_embed = AsyncMock()

        interaction = SimpleNamespace(user=SimpleNamespace(id=123, mention="<@123>"), guild=object())

        from COGS import UsernameChangeCog as username_change_module
        original_fetch = username_change_module.fetch_habbo_profile
        username_change_module.fetch_habbo_profile = lambda _username: {"name": "NewHabbo"}
        try:
            result = await cog._process_username_change(interaction, " NewHabbo ")
        finally:
            username_change_module.fetch_habbo_profile = original_fetch

        cog.verified_store.save.assert_called_once_with(discord_id="123", habbo_username="NewHabbo")
        cog._sync_member_nickname.assert_awaited_once_with(interaction, "NewHabbo")
        cog._reload_autoroles_cog.assert_awaited_once()
        cog._send_verification_log_embed.assert_awaited_once()
        self.assertIn("OldHabbo", result)
        self.assertIn("NewHabbo", result)

    async def test_process_username_change_requires_existing_verified_user(self) -> None:
        cog = UsernameChangeCog(bot=SimpleNamespace())
        cog.verified_store = SimpleNamespace(get_habbo_username=lambda _discord_id: None)
        cog.server_config_store = SimpleNamespace(
            get_request_channel_id=lambda: 1479465446632853524,
            get_admin_role_id=lambda: 1484029753185931336,
        )

        interaction = SimpleNamespace(user=SimpleNamespace(id=123), guild=object())

        result = await cog._process_username_change(interaction, "Anything")

        self.assertEqual(
            result,
            "You are not currently verified, so there is no saved Habbo username to update.",
        )

    async def test_reload_autoroles_cog_loads_extension_when_not_already_loaded(self) -> None:
        bot = SimpleNamespace(
            reload_extension=AsyncMock(side_effect=commands.ExtensionNotLoaded("COGS.ServerAutoRolesRPA")),
            load_extension=AsyncMock(),
        )
        cog = UsernameChangeCog(bot=bot)
        cog.server_config_store = SimpleNamespace(
            get_request_channel_id=lambda: 1479465446632853524,
            get_admin_role_id=lambda: 1484029753185931336,
        )

        status = await cog._reload_autoroles_cog()

        self.assertEqual(status, "Loaded AutoRoles cog because it was not already loaded.")
        bot.load_extension.assert_awaited_once_with("COGS.ServerAutoRolesRPA")

    async def test_send_verification_log_embed_posts_to_configured_requests_channel_with_admin_ping_and_buttons(self) -> None:
        sent_messages: list[dict[str, object]] = []

        async def capture_send(*, content=None, embed=None, view=None):
            sent_messages.append({"content": content, "embed": embed, "view": view})

        channel = SimpleNamespace(send=AsyncMock(side_effect=capture_send))
        guild = SimpleNamespace(get_channel=lambda channel_id: channel if channel_id == 1479465446632853524 else None)
        bot = SimpleNamespace(get_channel=lambda _channel_id: None)
        cog = UsernameChangeCog(bot=bot)
        cog.server_config_store = SimpleNamespace(
            get_request_channel_id=lambda: 1479465446632853524,
            get_admin_role_id=lambda: 1484029753185931336,
        )
        interaction = SimpleNamespace(user=SimpleNamespace(mention="<@123>"), guild=guild)

        await cog._send_verification_log_embed(
            interaction=interaction,
            previous_username="OldHabbo",
            updated_username="NewHabbo",
            nickname_status="Nickname updated to verified Habbo username.",
            reload_status="Reloaded AutoRoles cog successfully.",
        )

        self.assertEqual(len(sent_messages), 1)
        self.assertEqual(sent_messages[0]["content"], "<@&1484029753185931336>")
        embed = sent_messages[0]["embed"]
        self.assertEqual(embed.title, "Habbo Username Change Request")
        self.assertEqual(embed.fields[1].value, "OldHabbo")
        self.assertEqual(embed.fields[2].value, "NewHabbo")
        self.assertEqual(embed.fields[5].value, "Pending admin review")
        view = sent_messages[0]["view"]
        self.assertIsInstance(view, UsernameChangeRequestView)
        self.assertEqual([child.label for child in view.children], ["Accept", "Decline"])


class UsernameChangeRequestViewTests(unittest.IsolatedAsyncioTestCase):
    """Validate request-button authorization and embed status updates."""

    async def test_interaction_check_rejects_users_without_admin_role(self) -> None:
        view = UsernameChangeRequestView(admin_role_id=1484029753185931336)
        response = SimpleNamespace(send_message=AsyncMock())
        interaction = SimpleNamespace(user=SimpleNamespace(roles=[SimpleNamespace(id=1)]), response=response)

        allowed = await view.interaction_check(interaction)

        self.assertFalse(allowed)
        response.send_message.assert_awaited_once_with(
            "You need the configured Discord Admin role to use these buttons.",
            ephemeral=True,
        )

    async def test_accept_button_marks_embed_as_accepted_and_disables_buttons(self) -> None:
        view = UsernameChangeRequestView(admin_role_id=1484029753185931336)
        embed = discord.Embed(title="Habbo Username Change Request")
        embed.add_field(name="Request Status", value="Pending admin review", inline=False)
        response = SimpleNamespace(edit_message=AsyncMock())
        interaction = SimpleNamespace(
            user=SimpleNamespace(mention="<@555>"),
            message=SimpleNamespace(embeds=[embed]),
            response=response,
        )

        await view.accept.callback(interaction)

        response.edit_message.assert_awaited_once()
        edited_embed = response.edit_message.await_args.kwargs["embed"]
        edited_view = response.edit_message.await_args.kwargs["view"]
        self.assertEqual(edited_embed.fields[0].value, "Accepted by <@555>")
        self.assertTrue(all(child.disabled for child in edited_view.children))

    async def test_decline_button_marks_embed_as_declined_and_disables_buttons(self) -> None:
        view = UsernameChangeRequestView(admin_role_id=1484029753185931336)
        embed = discord.Embed(title="Habbo Username Change Request")
        response = SimpleNamespace(edit_message=AsyncMock())
        interaction = SimpleNamespace(
            user=SimpleNamespace(mention="<@777>"),
            message=SimpleNamespace(embeds=[embed]),
            response=response,
        )

        await view.decline.callback(interaction)

        edited_embed = response.edit_message.await_args.kwargs["embed"]
        self.assertEqual(edited_embed.fields[0].name, "Request Status")
        self.assertEqual(edited_embed.fields[0].value, "Declined by <@777>")


if __name__ == "__main__":
    unittest.main()
