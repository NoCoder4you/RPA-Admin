"""Unit tests for Habbo verification cog nickname synchronization behavior."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

try:
    import discord
    from COGS.ServerVerifyRPA import HabboVerificationCog, WHITE_CHECK_MARK_EMOJI
except ModuleNotFoundError as exc:  # pragma: no cover - environment-dependent test skip
    raise unittest.SkipTest(f"discord.py is not installed in this environment: {exc}")


class HabboVerificationCogNicknameTests(unittest.IsolatedAsyncioTestCase):
    """Validate nickname synchronization outcomes used by /verify flows."""

    async def test_sync_member_nickname_updates_to_verified_habbo_name(self) -> None:
        cog = HabboVerificationCog(bot=MagicMock())
        member = SimpleNamespace(nick="OldName", edit=AsyncMock())
        interaction = SimpleNamespace(guild=object(), user=member)

        status = await cog._sync_member_nickname(interaction, "Siren")

        self.assertEqual(status, "Nickname updated to verified Habbo username.")
        member.edit.assert_awaited_once_with(
            nick="Siren",
            reason="Habbo verification nickname sync",
        )

    async def test_sync_member_nickname_skips_when_no_guild_context(self) -> None:
        cog = HabboVerificationCog(bot=MagicMock())
        member = SimpleNamespace(nick="OldName", edit=AsyncMock())
        interaction = SimpleNamespace(guild=None, user=member)

        status = await cog._sync_member_nickname(interaction, "Siren")

        self.assertEqual(status, "Skipped (nickname can only be changed inside a server).")
        member.edit.assert_not_called()

    async def test_sync_member_nickname_handles_missing_permissions(self) -> None:
        cog = HabboVerificationCog(bot=MagicMock())
        member = SimpleNamespace(nick="OldName", edit=AsyncMock(side_effect=discord.Forbidden(MagicMock(), "missing perms")))
        interaction = SimpleNamespace(guild=object(), user=member)

        status = await cog._sync_member_nickname(interaction, "Siren")

        self.assertEqual(status, "Failed (bot lacks permission to manage this nickname).")


@unittest.skipIf(HabboVerificationCog is None, "discord.py is not installed in the test environment")
class HabboVerificationCogReactionRoleTests(unittest.IsolatedAsyncioTestCase):
    """Validate reaction-based Awaiting Verification role assignment flow."""

    def _build_reaction_test_context(self) -> tuple[HabboVerificationCog, SimpleNamespace, SimpleNamespace, SimpleNamespace]:
        """Create a reusable, lightweight reaction-test context for cog listener checks."""

        bot = SimpleNamespace()
        cog = HabboVerificationCog.__new__(HabboVerificationCog)
        cog.bot = bot
        cog.server_config_store = SimpleNamespace(get_verification_reaction_message_id=lambda: 1481010999157981256)

        role = SimpleNamespace(name="Awaiting Verification")
        member = SimpleNamespace(roles=[], add_roles=AsyncMock())

        message = SimpleNamespace(remove_reaction=AsyncMock())
        channel = SimpleNamespace(fetch_message=AsyncMock(return_value=message))
        guild = SimpleNamespace(roles=[role], get_member=lambda _uid: member, fetch_member=AsyncMock())

        bot.user = SimpleNamespace(id=999)
        bot.get_guild = lambda _gid: guild
        bot.get_channel = lambda _cid: channel

        return cog, member, channel, message

    async def test_reaction_add_assigns_awaiting_verification_role_and_removes_user_reaction(self) -> None:
        # Build a lightweight cog test double without running full bot startup logic.
        cog, member, _channel, message = self._build_reaction_test_context()

        payload = SimpleNamespace(
            guild_id=123,
            channel_id=987,
            user_id=555,
            message_id=1481010999157981256,
            emoji="✅",
        )

        await cog.on_raw_reaction_add(payload)

        # Valid green-check reactions on the configured message should grant the staging role.
        member.add_roles.assert_awaited_once()
        # User reaction should be removed so only bot-owned reaction persists.
        message.remove_reaction.assert_awaited_once_with("✅", member)

    async def test_reaction_add_skips_role_when_message_id_does_not_match(self) -> None:
        # Ensure role assignment and reaction cleanup are gated to the configured verification message ID.
        cog, member, channel, message = self._build_reaction_test_context()

        payload = SimpleNamespace(
            guild_id=123,
            channel_id=987,
            user_id=555,
            message_id=111,
            emoji="✅",
        )

        await cog.on_raw_reaction_add(payload)

        member.add_roles.assert_not_awaited()
        channel.fetch_message.assert_not_awaited()
        message.remove_reaction.assert_not_awaited()

    async def test_reaction_add_removes_non_green_check_without_assigning_role(self) -> None:
        # Any non-green-check reaction on the configured message should be removed but not grant roles.
        cog, member, _channel, message = self._build_reaction_test_context()

        payload = SimpleNamespace(
            guild_id=123,
            channel_id=987,
            user_id=555,
            message_id=1481010999157981256,
            emoji="❌",
        )

        await cog.on_raw_reaction_add(payload)

        member.add_roles.assert_not_awaited()
        message.remove_reaction.assert_awaited_once_with("❌", member)

    async def test_ensure_verification_message_reaction_adds_green_check_to_configured_message(self) -> None:
        # Confirm startup behavior: the bot should place ✅ on the configured verification message.
        bot = SimpleNamespace(guilds=[])
        cog = HabboVerificationCog.__new__(HabboVerificationCog)
        cog.bot = bot
        cog.server_config_store = SimpleNamespace(get_verification_reaction_message_id=lambda: 1481010999157981256)

        message = SimpleNamespace(add_reaction=AsyncMock(), reactions=[])
        matching_channel = SimpleNamespace(fetch_message=AsyncMock(return_value=message))
        missing_channel = SimpleNamespace(fetch_message=AsyncMock(side_effect=discord.NotFound(MagicMock(), "missing")))
        bot.guilds = [SimpleNamespace(text_channels=[missing_channel, matching_channel])]

        await cog._ensure_verification_message_reaction()

        message.add_reaction.assert_awaited_once_with(WHITE_CHECK_MARK_EMOJI)


    async def test_ensure_verification_message_reaction_skips_duplicate_bot_white_check_mark(self) -> None:
        # If the bot already owns the white check mark reaction, startup should not add a duplicate request.
        reaction = SimpleNamespace(emoji=WHITE_CHECK_MARK_EMOJI, me=True)
        message = SimpleNamespace(add_reaction=AsyncMock(), reactions=[reaction])

        bot = SimpleNamespace(user=SimpleNamespace(id=999), guilds=[])
        cog = HabboVerificationCog.__new__(HabboVerificationCog)
        cog.bot = bot
        cog.server_config_store = SimpleNamespace(get_verification_reaction_message_id=lambda: 1481010999157981256)

        matching_channel = SimpleNamespace(fetch_message=AsyncMock(return_value=message))
        bot.guilds = [SimpleNamespace(text_channels=[matching_channel])]

        await cog._ensure_verification_message_reaction()

        message.add_reaction.assert_not_awaited()

    async def test_ensure_verification_message_reaction_noops_without_configured_message_id(self) -> None:
        # Guardrail: without a configured target message ID, startup should not issue fetch requests.
        fetch_message = AsyncMock()
        bot = SimpleNamespace(guilds=[SimpleNamespace(text_channels=[SimpleNamespace(fetch_message=fetch_message)])])
        cog = HabboVerificationCog.__new__(HabboVerificationCog)
        cog.bot = bot
        cog.server_config_store = SimpleNamespace(get_verification_reaction_message_id=lambda: None)

        await cog._ensure_verification_message_reaction()

        fetch_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
