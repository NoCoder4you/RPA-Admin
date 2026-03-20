"""Unit tests for concise role-sync updater audit embeds."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

try:
    from COGS.ServerAutoRolesRPA import HabboRoleUpdaterCog
except ModuleNotFoundError as import_error:  # pragma: no cover - environment-dependent test skip
    HabboRoleUpdaterCog = None


@unittest.skipIf(HabboRoleUpdaterCog is None, "discord.py is not installed in the test environment")
class HabboRoleUpdaterCogEmbedTests(unittest.IsolatedAsyncioTestCase):
    """Ensure updater embeds include only user mention and true role deltas."""

    async def test_role_change_embed_is_skipped_when_no_role_changes(self) -> None:
        # Build a cog instance without running __init__ so background tasks are not started in tests.
        cog = HabboRoleUpdaterCog.__new__(HabboRoleUpdaterCog)
        cog.server_config_store = SimpleNamespace(get_audit_channel_id=lambda: 101)

        channel = SimpleNamespace(send=AsyncMock())
        guild = SimpleNamespace(get_channel=lambda channel_id: channel if channel_id == 101 else None)
        member = SimpleNamespace(mention="<@123>")

        await cog._send_role_change_embed_for_guild(
            guild=guild,
            member=member,
            added_role_names=[],
            removed_role_names=[],
        )

        # No role changes should produce no embed at all.
        channel.send.assert_not_awaited()

    async def test_role_change_embed_includes_only_non_empty_role_sections(self) -> None:
        # Use a test double for config/channel so we can inspect exactly what was sent.
        cog = HabboRoleUpdaterCog.__new__(HabboRoleUpdaterCog)
        cog.server_config_store = SimpleNamespace(get_audit_channel_id=lambda: 202)

        channel = SimpleNamespace(send=AsyncMock())
        guild = SimpleNamespace(get_channel=lambda channel_id: channel if channel_id == 202 else None)
        member = SimpleNamespace(mention="<@456>")

        await cog._send_role_change_embed_for_guild(
            guild=guild,
            member=member,
            added_role_names=["Role A", "Role B"],
            removed_role_names=[],
        )

        embed = channel.send.await_args.kwargs["embed"]
        fields = {field.name: field.value for field in embed.fields}

        self.assertEqual(fields["User"], "<@456>")
        self.assertEqual(fields["Added Roles"], "Role A, Role B")
        self.assertNotIn("Removed Roles", fields)

    async def test_member_join_reapplies_saved_roles_nickname_and_verification_log(self) -> None:
        """Previously verified members should be resynced immediately when they rejoin."""

        cog = HabboRoleUpdaterCog.__new__(HabboRoleUpdaterCog)
        cog.bot = SimpleNamespace(get_channel=lambda _channel_id: None)
        cog.server_config_store = SimpleNamespace(get_request_channel_id=lambda: 303)
        cog.verified_store = SimpleNamespace(get_habbo_username=lambda discord_id: "Siren" if discord_id == "456" else None)
        cog._assign_roles_to_member_from_profile = AsyncMock(
            return_value=("Added: Role A | Removed: none", ["Role A"], [])
        )
        cog._sync_member_nickname = AsyncMock(return_value="Nickname updated to verified Habbo username.")
        cog._send_role_change_embed_for_guild = AsyncMock()
        cog._send_verification_rejoin_log = AsyncMock()

        member = SimpleNamespace(
            id=456,
            mention="<@456>",
            guild=SimpleNamespace(),
        )

        with unittest.mock.patch("COGS.ServerAutoRolesRPA.fetch_habbo_profile", return_value={"name": "Siren"}):
            await cog.on_member_join(member)

        cog._assign_roles_to_member_from_profile.assert_awaited_once_with(member.guild, member, {"name": "Siren"})
        cog._sync_member_nickname.assert_awaited_once_with(member=member, habbo_username="Siren")
        cog._send_role_change_embed_for_guild.assert_awaited_once_with(
            guild=member.guild,
            member=member,
            added_role_names=["Role A"],
            removed_role_names=[],
        )
        cog._send_verification_rejoin_log.assert_awaited_once_with(
            guild=member.guild,
            member=member,
            habbo_username="Siren",
            role_status="Added: Role A | Removed: none",
            nickname_status="Nickname updated to verified Habbo username.",
            added_role_names=["Role A"],
            removed_role_names=[],
        )

    async def test_send_verification_rejoin_log_posts_expected_summary(self) -> None:
        """Join-time verification log embeds should summarize nickname and autorole sync results."""

        cog = HabboRoleUpdaterCog.__new__(HabboRoleUpdaterCog)
        cog.bot = SimpleNamespace(get_channel=lambda _channel_id: None)
        cog.server_config_store = SimpleNamespace(get_request_channel_id=lambda: 404)
        verification_channel = SimpleNamespace(send=AsyncMock())
        guild = SimpleNamespace(get_channel=lambda channel_id: verification_channel if channel_id == 404 else None)
        member = SimpleNamespace(mention="<@789>")

        await cog._send_verification_rejoin_log(
            guild=guild,
            member=member,
            habbo_username="Siren",
            role_status="No role changes were required.",
            nickname_status="Nickname updated to verified Habbo username.",
            added_role_names=[],
            removed_role_names=[],
        )

        verification_channel.send.assert_awaited_once()
        embed = verification_channel.send.await_args.kwargs["embed"]
        fields = {field.name: field.value for field in embed.fields}

        self.assertEqual(embed.title, "Verified Member Rejoined")
        self.assertEqual(fields["Member"], "<@789>")
        self.assertEqual(fields["Habbo Username"], "Siren")
        self.assertEqual(fields["Role Sync"], "No role changes were required.")
        self.assertEqual(fields["Nickname Sync"], "Nickname updated to verified Habbo username.")
        self.assertEqual(fields["Added Roles"], "none")
        self.assertEqual(fields["Removed Roles"], "none")

    async def test_send_verification_rejoin_log_skips_when_serverconfig_has_no_channel(self) -> None:
        """Do not try to send a join-time verification log when no channel is configured yet."""

        cog = HabboRoleUpdaterCog.__new__(HabboRoleUpdaterCog)
        cog.bot = SimpleNamespace(get_channel=AsyncMock())
        cog.server_config_store = SimpleNamespace(get_request_channel_id=lambda: None)
        guild = SimpleNamespace(get_channel=AsyncMock())
        member = SimpleNamespace(mention="<@999>")

        await cog._send_verification_rejoin_log(
            guild=guild,
            member=member,
            habbo_username="Siren",
            role_status="No role changes were required.",
            nickname_status="No nickname change was required.",
            added_role_names=[],
            removed_role_names=[],
        )

        guild.get_channel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
