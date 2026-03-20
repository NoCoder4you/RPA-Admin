"""Unit tests for forwarding watched bot mentions to the configured owner."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

try:
    from COGS.MentionForwardCog import (
        BOT_OWNER_ID,
        TARGET_ROLE_ID,
        TARGET_USER_ID,
        MentionForwardCog,
    )
except Exception:
    MentionForwardCog = None


@unittest.skipIf(MentionForwardCog is None, "discord.py is not installed in the test environment")
class MentionForwardCogTests(unittest.IsolatedAsyncioTestCase):
    """Validate the owner-forwarding behavior for watched bot mentions."""

    def setUp(self) -> None:
        self.owner_user = SimpleNamespace(send=AsyncMock())
        self.bot_user = SimpleNamespace(id=999, mention="<@999>")
        self.bot = SimpleNamespace(
            user=self.bot_user,
            get_user=lambda user_id: self.owner_user if user_id == BOT_OWNER_ID else None,
            fetch_user=AsyncMock(return_value=self.owner_user),
        )
        self.cog = MentionForwardCog(self.bot)

    async def test_forwards_when_target_user_mentions_bot(self) -> None:
        message = SimpleNamespace(
            author=SimpleNamespace(id=TARGET_USER_ID, bot=False, roles=[], __str__=lambda self: "WatchedUser"),
            mentions=[self.bot_user],
            content="<@999> please review this",
            channel=SimpleNamespace(mention="#support"),
            guild=SimpleNamespace(name="RPA"),
        )

        await self.cog.on_message(message)

        self.owner_user.send.assert_awaited_once()
        forwarded_text = self.owner_user.send.await_args.args[0]
        self.assertIn("please review this", forwarded_text)
        self.assertIn("RPA", forwarded_text)

    async def test_forwards_when_target_role_member_mentions_bot(self) -> None:
        message = SimpleNamespace(
            author=SimpleNamespace(id=555, bot=False, roles=[SimpleNamespace(id=TARGET_ROLE_ID)], __str__=lambda self: "RoleMember"),
            mentions=[self.bot_user],
            content="<@999> escalated by role",
            channel=SimpleNamespace(mention="#alerts"),
            guild=SimpleNamespace(name="RPA"),
        )

        await self.cog.on_message(message)

        self.owner_user.send.assert_awaited_once()
        self.assertIn("escalated by role", self.owner_user.send.await_args.args[0])

    async def test_does_not_forward_without_bot_mention(self) -> None:
        message = SimpleNamespace(
            author=SimpleNamespace(id=TARGET_USER_ID, bot=False, roles=[]),
            mentions=[],
            content="hello there",
            channel=SimpleNamespace(mention="#general"),
            guild=SimpleNamespace(name="RPA"),
        )

        await self.cog.on_message(message)

        self.owner_user.send.assert_not_awaited()

    async def test_does_not_forward_for_unapproved_author(self) -> None:
        message = SimpleNamespace(
            author=SimpleNamespace(id=777, bot=False, roles=[]),
            mentions=[self.bot_user],
            content="<@999> random ping",
            channel=SimpleNamespace(mention="#general"),
            guild=SimpleNamespace(name="RPA"),
        )

        await self.cog.on_message(message)

        self.owner_user.send.assert_not_awaited()
