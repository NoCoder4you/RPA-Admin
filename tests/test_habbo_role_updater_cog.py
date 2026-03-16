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


if __name__ == "__main__":
    unittest.main()
