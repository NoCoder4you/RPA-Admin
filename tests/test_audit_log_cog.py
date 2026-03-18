"""Unit tests for the event-driven audit logging cog."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

try:
    from COGS.AuditLogCog import AuditLogCog
except ModuleNotFoundError:
    AuditLogCog = None


@unittest.skipIf(AuditLogCog is None, "discord.py is not installed in the test environment")
class AuditLogCogTests(unittest.IsolatedAsyncioTestCase):
    """Verify that guild events are transformed into audit-log messages."""

    async def test_member_join_posts_to_audit_channel(self) -> None:
        cog = AuditLogCog(MagicMock())
        audit_channel = SimpleNamespace(send=AsyncMock())
        guild = SimpleNamespace(get_channel=MagicMock(return_value=audit_channel))
        member = SimpleNamespace(guild=guild, mention="<@1>", id=1)

        cog.server_config_store = SimpleNamespace(get_audit_channel_id=MagicMock(return_value=123))

        await cog.on_member_join(member)

        audit_channel.send.assert_awaited_once()
        embed = audit_channel.send.await_args.kwargs["embed"]
        self.assertEqual(embed.title, "Member Joined")
        # The embed should include a Discord relative timestamp marker.
        when_field = next(field for field in embed.fields if field.name == "When")
        self.assertRegex(when_field.value, r"^<t:\d+:R>$")

    async def test_channel_permission_update_logs_only_when_overwrites_change(self) -> None:
        cog = AuditLogCog(MagicMock())
        cog._send_audit_embed = AsyncMock()

        guild = SimpleNamespace()
        before = SimpleNamespace(overwrites={"a": 1}, guild=guild, id=10, mention="#general", name="general")
        after = SimpleNamespace(overwrites={"a": 2}, guild=guild, id=10, mention="#general", name="general")

        await cog.on_guild_channel_update(before, after)
        cog._send_audit_embed.assert_awaited_once()

        cog._send_audit_embed.reset_mock()
        unchanged_after = SimpleNamespace(overwrites={"a": 1}, guild=guild, id=10, mention="#general", name="general")
        await cog.on_guild_channel_update(before, unchanged_after)
        cog._send_audit_embed.assert_not_awaited()

    async def test_member_ban_and_unban_are_logged(self) -> None:
        cog = AuditLogCog(MagicMock())
        cog._send_audit_embed = AsyncMock()
        cog._find_recent_audit_entry = AsyncMock(return_value=SimpleNamespace(user=SimpleNamespace(id=7, mention="<@7>")))

        guild = SimpleNamespace()
        user = SimpleNamespace(id=99, mention="<@99>")

        await cog.on_member_ban(guild, user)
        await cog.on_member_unban(guild, user)

        self.assertEqual(cog._send_audit_embed.await_count, 2)
        first_fields = cog._send_audit_embed.await_args_list[0].kwargs["fields"]
        self.assertEqual(first_fields[0][0], "By")

    async def test_role_permission_update_logs_before_and_after_values(self) -> None:
        cog = AuditLogCog(MagicMock())
        cog._send_audit_embed = AsyncMock()
        cog._find_recent_audit_entry = AsyncMock(return_value=SimpleNamespace(user=SimpleNamespace(id=7, mention="<@7>")))

        guild = SimpleNamespace()
        before_permissions = SimpleNamespace(value=1)
        after_permissions = SimpleNamespace(value=2)

        before = SimpleNamespace(guild=guild, permissions=before_permissions, name="Admin", id=55)
        after = SimpleNamespace(guild=guild, permissions=after_permissions, name="Admin", id=55)

        await cog.on_guild_role_update(before, after)

        cog._send_audit_embed.assert_awaited_once()
        fields = cog._send_audit_embed.await_args.kwargs["fields"]
        self.assertEqual(fields[1][1], "1")
        self.assertEqual(fields[2][1], "2")
        self.assertEqual(fields[0][0], "By")
        self.assertEqual(fields[3][0], "Changed Flags")

    async def test_voice_state_update_logs_server_mute_and_deafen_changes(self) -> None:
        cog = AuditLogCog(MagicMock())
        cog._send_audit_embed = AsyncMock()

        member = SimpleNamespace(guild=SimpleNamespace(), mention="<@22>", id=22)
        before = SimpleNamespace(mute=False, deaf=False)
        after = SimpleNamespace(mute=True, deaf=True)

        await cog.on_voice_state_update(member, before, after)

        cog._send_audit_embed.assert_awaited_once()
        fields = cog._send_audit_embed.await_args.kwargs["fields"]
        self.assertTrue(any(field[0] == "By" for field in fields))
        self.assertTrue(any(field[0] == "Server Mute" for field in fields))
        self.assertTrue(any(field[0] == "Server Deaf" for field in fields))

        cog._send_audit_embed.reset_mock()
        unchanged = SimpleNamespace(mute=True, deaf=True)
        await cog.on_voice_state_update(member, unchanged, unchanged)
        cog._send_audit_embed.assert_not_awaited()

    async def test_member_update_logs_nickname_and_roles(self) -> None:
        cog = AuditLogCog(MagicMock())
        cog._send_audit_embed = AsyncMock()
        cog._find_recent_audit_entry = AsyncMock(return_value=SimpleNamespace(user=SimpleNamespace(id=8, mention="<@8>")))

        role_a = SimpleNamespace(id=1)
        role_b = SimpleNamespace(id=2)
        guild = SimpleNamespace()
        before = SimpleNamespace(guild=guild, mention="<@22>", id=22, nick="Old", roles=[role_a])
        after = SimpleNamespace(guild=guild, mention="<@22>", id=22, nick="New", roles=[role_a, role_b])

        await cog.on_member_update(before, after)

        cog._send_audit_embed.assert_awaited_once()
        fields = cog._send_audit_embed.await_args.kwargs["fields"]
        self.assertTrue(any(field[0] == "By" for field in fields))
        self.assertTrue(any(field[0] == "Nickname" for field in fields))
        self.assertTrue(any(field[0] == "Roles Added" for field in fields))

    async def test_guild_update_logs_core_setting_changes(self) -> None:
        cog = AuditLogCog(MagicMock())
        cog._send_audit_embed = AsyncMock()
        cog._find_recent_audit_entry = AsyncMock(return_value=SimpleNamespace(user=SimpleNamespace(id=9, mention="<@9>")))

        afk_before = SimpleNamespace(name="AFK-Old")
        afk_after = SimpleNamespace(name="AFK-New")
        before = SimpleNamespace(
            id=7,
            name="Old Name",
            description="Old Desc",
            afk_timeout=60,
            afk_channel=afk_before,
        )
        after = SimpleNamespace(
            id=7,
            name="New Name",
            description="New Desc",
            afk_timeout=300,
            afk_channel=afk_after,
        )

        await cog.on_guild_update(before, after)

        cog._send_audit_embed.assert_awaited_once()
        self.assertEqual(cog._send_audit_embed.await_args.kwargs["title"], "Server Settings Updated")
        fields = cog._send_audit_embed.await_args.kwargs["fields"]
        self.assertTrue(any(field[0] == "By" for field in fields))


if __name__ == "__main__":
    unittest.main()
