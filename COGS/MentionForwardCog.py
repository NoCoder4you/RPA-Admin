"""Forward qualifying bot mentions to the configured owner via direct message."""

from __future__ import annotations

import discord
from discord.ext import commands


BOT_OWNER_ID = 298121351871594497
TARGET_USER_ID = 1481426914928361685
TARGET_ROLE_ID = 1484058808081715274


class MentionForwardCog(commands.Cog):
    """Send the bot owner a DM when approved users mention the bot."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @staticmethod
    def _member_has_target_role(message: discord.Message) -> bool:
        """Return True when the message author currently has the watched Discord role.

        The helper stays defensive because tests and some Discord events may provide
        lightweight message-author objects without a full `.roles` collection.
        """

        author_roles = getattr(message.author, "roles", [])
        return any(getattr(role, "id", None) == TARGET_ROLE_ID for role in author_roles)

    def _should_forward_message(self, message: discord.Message) -> bool:
        """Decide whether a message qualifies for owner forwarding.

        A message is forwarded only when:
        * it is not authored by a bot,
        * the bot itself was directly mentioned in the message, and
        * the author matches the configured user ID or holds the configured role.
        """

        bot_user = self.bot.user
        if bot_user is None or getattr(message.author, "bot", False):
            return False

        if bot_user not in getattr(message, "mentions", []):
            return False

        author_id = getattr(message.author, "id", None)

        # Never forward the owner's own bot mentions back to the owner.
        if author_id == BOT_OWNER_ID:
            return False

        return author_id == TARGET_USER_ID or self._member_has_target_role(message)

    @staticmethod
    def _build_forwarded_message(message: discord.Message) -> str:
        """Build a readable DM payload for the bot owner.

        Including author and channel metadata gives the owner enough context to act
        on the forwarded mention without opening logs first.
        """

        author = getattr(message, "author", None)
        channel = getattr(message, "channel", None)
        guild = getattr(message, "guild", None)
        author_label = f"{author} ({getattr(author, 'id', 'unknown')})"
        channel_label = getattr(channel, "mention", getattr(channel, "name", "Direct Message"))
        guild_label = getattr(guild, "name", "Direct Message")
        content = message.content or "[No text content]"

        return (
            "A watched bot mention was detected.\n"
            f"Author: {author_label}\n"
            f"Guild: {guild_label}\n"
            f"Channel: {channel_label}\n"
            f"Message: {content}"
        )

    async def _forward_message_to_owner(self, message: discord.Message) -> None:
        """Resolve the configured owner user and DM them the forwarded mention."""

        owner_user = self.bot.get_user(BOT_OWNER_ID)
        if owner_user is None:
            try:
                owner_user = await self.bot.fetch_user(BOT_OWNER_ID)
            except (discord.NotFound, discord.HTTPException, discord.Forbidden):
                return

        try:
            await owner_user.send(self._build_forwarded_message(message))
        except (discord.HTTPException, discord.Forbidden, AttributeError):
            # Silently ignore DM delivery issues so mention handling never breaks chat flow.
            return

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Forward approved bot mentions to the configured bot owner via DM."""

        if not self._should_forward_message(message):
            return

        await self._forward_message_to_owner(message)


async def setup(bot: commands.Bot) -> None:
    """discord.py extension entrypoint used by automatic cog loading."""

    await bot.add_cog(MentionForwardCog(bot))
