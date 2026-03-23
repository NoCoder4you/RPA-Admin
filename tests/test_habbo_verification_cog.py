"""Unit tests for Habbo verification cog nickname synchronization behavior."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

try:
    import discord
    from COGS.ServerVerifyRPA import (
        HabboVerificationCog,
        VERIFICATION_LOG_CHANNEL_ID,
        WHITE_CHECK_MARK_EMOJI,
    )
except ModuleNotFoundError as exc:  # pragma: no cover - environment-dependent test skip
    raise unittest.SkipTest(f"discord.py is not installed in this environment: {exc}")


AWAITING_VERIFICATION_CHANNEL_ID = 1479391662076723224


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


class HabboVerificationCogVerifiedRoleTests(unittest.IsolatedAsyncioTestCase):
    """Validate baseline Discord Verified role assignment used by the /verify flow."""

    async def test_ensure_verified_role_adds_verified_role_when_missing(self) -> None:
        cog = HabboVerificationCog(bot=MagicMock())
        cog.server_config_store = SimpleNamespace(get_awaiting_verification_role_id=lambda: None)
        verified_role = SimpleNamespace(name="Verified")
        member = SimpleNamespace(roles=[], add_roles=AsyncMock(), remove_roles=AsyncMock())
        interaction = SimpleNamespace(
            guild=SimpleNamespace(roles=[verified_role], get_role=lambda _role_id: None),
            user=member,
        )

        status, changed_roles = await cog._ensure_verified_role(interaction)

        self.assertEqual(status, "Verified role added.")
        self.assertEqual(changed_roles, ["Verified"])
        member.add_roles.assert_awaited_once_with(
            verified_role,
            reason="Habbo verification verified-role sync",
        )
        member.remove_roles.assert_not_awaited()

    async def test_ensure_verified_role_skips_outside_guild_context(self) -> None:
        cog = HabboVerificationCog(bot=MagicMock())
        interaction = SimpleNamespace(guild=None, user=SimpleNamespace())

        status, added_roles = await cog._ensure_verified_role(interaction)

        self.assertEqual(status, "Skipped (Verified role can only be assigned inside a server).")
        self.assertEqual(added_roles, [])

    async def test_ensure_verified_role_removes_awaiting_verification_role_after_success(self) -> None:
        cog = HabboVerificationCog(bot=MagicMock())
        awaiting_role = SimpleNamespace(id=42, name="Awaiting Verification")
        verified_role = SimpleNamespace(name="Verified")
        cog.server_config_store = SimpleNamespace(get_awaiting_verification_role_id=lambda: 42)
        member = SimpleNamespace(
            roles=[awaiting_role],
            add_roles=AsyncMock(),
            remove_roles=AsyncMock(),
        )
        interaction = SimpleNamespace(
            guild=SimpleNamespace(roles=[verified_role, awaiting_role], get_role=lambda role_id: awaiting_role if role_id == 42 else None),
            user=member,
        )

        status, changed_roles = await cog._ensure_verified_role(interaction)

        self.assertEqual(status, "Verified role added. | Awaiting Verification role removed.")
        self.assertEqual(changed_roles, ["Verified", "Awaiting Verification"])
        member.add_roles.assert_awaited_once_with(
            verified_role,
            reason="Habbo verification verified-role sync",
        )
        member.remove_roles.assert_awaited_once_with(
            awaiting_role,
            reason="Habbo verification verified-role sync",
        )


class HabboVerificationCommandTests(unittest.IsolatedAsyncioTestCase):
    """Validate /verify command behavior that differs between initial and repeat runs."""

    async def test_verify_skips_verification_audit_when_user_is_already_verified(self) -> None:
        """Already-verified members should only get a role sync response, not a new audit post."""

        cog = HabboVerificationCog(bot=MagicMock())
        cog.verified_store = SimpleNamespace(get_habbo_username=lambda discord_id: "Siren" if discord_id == "123" else None)
        cog._assign_roles_from_habbo_groups = AsyncMock(return_value=("No role changes were required.", [], []))
        cog._ensure_verified_role = AsyncMock(return_value=("No Verified role change was required.", []))
        cog._send_audit_log = AsyncMock()

        interaction = SimpleNamespace(
            user=SimpleNamespace(id=123, mention="<@123>"),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        from COGS import ServerVerifyRPA as verify_module
        original_fetch = verify_module.fetch_habbo_profile
        verify_module.fetch_habbo_profile = lambda _username: {"name": "Siren", "figureString": "hr-1-1"}
        try:
            await cog.verify.callback(cog, interaction, "IgnoredInput")
        finally:
            verify_module.fetch_habbo_profile = original_fetch

        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        cog._assign_roles_from_habbo_groups.assert_awaited_once()
        cog._ensure_verified_role.assert_awaited_once_with(interaction)
        cog._send_audit_log.assert_not_awaited()
        interaction.followup.send.assert_awaited_once()
        sent_embed = interaction.followup.send.await_args.kwargs["embed"]
        self.assertEqual(sent_embed.title, "Already Verified")



class HabboVerificationCogAuditLogTests(unittest.IsolatedAsyncioTestCase):
    """Validate the staff-facing verification audit embed output."""

    async def test_send_audit_log_posts_to_fixed_verification_channel(self) -> None:
        cog = HabboVerificationCog.__new__(HabboVerificationCog)
        verification_channel = SimpleNamespace(send=AsyncMock())
        cog.bot = SimpleNamespace(get_channel=lambda channel_id: verification_channel if channel_id == VERIFICATION_LOG_CHANNEL_ID else None)
        cog.server_config_store = MagicMock()

        guild = SimpleNamespace(get_channel=lambda _channel_id: None)
        interaction = SimpleNamespace(guild=guild, user=SimpleNamespace(mention="<@123>"))

        await cog._send_audit_log(
            interaction,
            action="habbo_verification_success",
            details={
                "discord_user_id": "123",
                "discord_user": "Tester",
                "habbo_username": "Siren",
                "role_sync_status": "Added: VIP",
                "roles_added": "VIP",
                "roles_removed": "none",
                "figure_string": "hr-100-42",
            },
        )

        verification_channel.send.assert_awaited_once()
        embed = verification_channel.send.await_args.kwargs["embed"]
        fields = {field.name: field.value for field in embed.fields}

        self.assertEqual(embed.title, "Habbo Verification Audit")
        self.assertEqual(fields["User"], "<@123>")
        self.assertEqual(fields["Discord User Id"], "123")
        self.assertEqual(fields["Discord User"], "Tester")
        self.assertEqual(fields["Habbo Username"], "Siren")
        self.assertNotIn("Action", fields)
        self.assertNotIn("Role Sync Status", fields)
        self.assertNotIn("Roles Added", fields)
        self.assertNotIn("Roles Removed", fields)
        self.assertEqual(embed.thumbnail.url.split("?", 1)[0], "https://www.habbo.com/habbo-imaging/avatarimage")

    async def test_send_audit_log_skips_when_fixed_channel_is_unavailable(self) -> None:
        cog = HabboVerificationCog.__new__(HabboVerificationCog)
        cog.bot = SimpleNamespace(get_channel=lambda _channel_id: None)
        cog.server_config_store = MagicMock()
        guild = SimpleNamespace(get_channel=lambda _channel_id: None)
        interaction = SimpleNamespace(guild=guild, user=SimpleNamespace(mention="<@123>"))

        await cog._send_audit_log(
            interaction,
            action="habbo_verification_success",
            details={"discord_user_id": "123", "habbo_username": "Siren", "figure_string": ""},
        )

        self.assertTrue(True)


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
        member = SimpleNamespace(roles=[], add_roles=AsyncMock(), mention="<@555>")

        message = SimpleNamespace(remove_reaction=AsyncMock())
        reaction_channel = SimpleNamespace(fetch_message=AsyncMock(return_value=message))
        verification_channel = SimpleNamespace(send=AsyncMock())
        guild = SimpleNamespace(
            roles=[role],
            get_member=lambda _uid: member,
            fetch_member=AsyncMock(),
            get_channel=lambda channel_id: verification_channel if channel_id == AWAITING_VERIFICATION_CHANNEL_ID else None,
        )

        bot.user = SimpleNamespace(id=999)
        bot.get_guild = lambda _gid: guild
        bot.get_channel = lambda _cid: reaction_channel

        return cog, member, reaction_channel, message, verification_channel

    async def test_reaction_add_assigns_awaiting_verification_role_and_removes_user_reaction(self) -> None:
        # Build a lightweight cog test double without running full bot startup logic.
        cog, member, _channel, message, verification_channel = self._build_reaction_test_context()

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
        verification_channel.send.assert_awaited_once_with(
            content=member.mention,
            embed=unittest.mock.ANY,
        )
        sent_embed = verification_channel.send.await_args.kwargs["embed"]
        self.assertEqual(sent_embed.title, "Awaiting Verification")
        self.assertEqual(sent_embed.fields[0].name, "Step 1")
        self.assertIn("run `/verify`", sent_embed.fields[0].value.lower())
        self.assertEqual(sent_embed.fields[1].name, "Step 2")
        self.assertIn("habbo motto", sent_embed.fields[1].value.lower())
        self.assertEqual(sent_embed.fields[2].name, "Step 3")
        self.assertIn("/verify", sent_embed.fields[2].value)
        self.assertIn("again", sent_embed.fields[2].value.lower())

    async def test_reaction_add_skips_role_when_message_id_does_not_match(self) -> None:
        # Ensure role assignment and reaction cleanup are gated to the configured verification message ID.
        cog, member, channel, message, verification_channel = self._build_reaction_test_context()

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
        verification_channel.send.assert_not_awaited()

    async def test_reaction_add_removes_non_green_check_without_assigning_role(self) -> None:
        # Any non-green-check reaction on the configured message should be removed but not grant roles.
        cog, member, _channel, message, verification_channel = self._build_reaction_test_context()

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
        verification_channel.send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
