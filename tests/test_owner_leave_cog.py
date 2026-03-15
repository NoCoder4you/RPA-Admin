"""Unit tests for the owner-only leave cog."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

try:
    from cogs.owner_leave import OwnerLeaveCog
except ModuleNotFoundError as import_error:
    OwnerLeaveCog = None


@unittest.skipIf(OwnerLeaveCog is None, "discord.py is not installed in the test environment")
class OwnerLeaveCogTests(unittest.IsolatedAsyncioTestCase):
    """Validate leave command behavior for valid and invalid guild targets."""

    async def test_leave_current_guild_when_no_guild_id_is_given(self) -> None:
        bot = MagicMock()
        cog = OwnerLeaveCog(bot)

        guild = SimpleNamespace(name="Example Guild", leave=AsyncMock())
        ctx = SimpleNamespace(guild=guild, send=AsyncMock())

        await cog.leave.callback(cog, ctx)

        guild.leave.assert_awaited_once()
        ctx.send.assert_awaited_once_with("Left server: **Example Guild**")

    async def test_leave_targeted_guild_when_guild_id_is_given(self) -> None:
        bot = MagicMock()
        cog = OwnerLeaveCog(bot)

        guild = SimpleNamespace(name="Target Guild", leave=AsyncMock())
        bot.get_guild.return_value = guild
        ctx = SimpleNamespace(guild=None, send=AsyncMock())

        await cog.leave.callback(cog, ctx, 123456789)

        bot.get_guild.assert_called_once_with(123456789)
        guild.leave.assert_awaited_once()
        ctx.send.assert_awaited_once_with("Left server: **Target Guild**")

    async def test_leave_reports_when_guild_cannot_be_found(self) -> None:
        bot = MagicMock()
        cog = OwnerLeaveCog(bot)

        bot.get_guild.return_value = None
        ctx = SimpleNamespace(guild=None, send=AsyncMock())

        await cog.leave.callback(cog, ctx, 111)

        ctx.send.assert_awaited_once_with("I could not find that server.")


if __name__ == "__main__":
    unittest.main()
