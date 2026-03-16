"""Discord cog that provides a moderation `/purge` slash command."""

from __future__ import annotations

from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands


class PurgeCog(commands.Cog):
    """Moderation cog containing a staff-only purge command for bulk message cleanup."""

    def __init__(self, bot: commands.Bot) -> None:
        # Keep a bot reference for consistency with the project's other cogs.
        self.bot = bot

    @app_commands.command(
        name="purge",
        description="Bulk delete recent messages from this channel with a sender filter.",
    )
    @app_commands.describe(
        target="Which messages should be deleted: users, bots, or all",
        amount="How many recent messages to inspect and purge (1-100)",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.checks.bot_has_permissions(manage_messages=True, read_message_history=True)
    async def purge(
        self,
        interaction: discord.Interaction,
        target: Literal["users", "bots", "all"],
        amount: app_commands.Range[int, 1, 100],
    ) -> None:
        """Delete recent messages from the current text channel based on sender type."""

        # Purge must run inside a guild text channel where moderation and history exist.
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "This command can only be used in a server text channel.",
                ephemeral=True,
            )
            return

        # This operation can take more than 3 seconds, so acknowledge immediately.
        await interaction.response.defer(ephemeral=True, thinking=True)

        def should_delete(message: discord.Message) -> bool:
            """Return True when a message matches the selected purge filter."""

            # Delete everything when `all` is chosen.
            if target == "all":
                return True

            # Delete only bot-authored messages (including webhooks) for bot cleanup mode.
            if target == "bots":
                return bool(message.author.bot or message.webhook_id is not None)

            # Remaining mode is `users`: delete only non-bot, non-webhook messages.
            return not message.author.bot and message.webhook_id is None

        try:
            # Use bulk purge to delete up to `amount` matching messages from recent history.
            deleted_messages = await interaction.channel.purge(limit=amount, check=should_delete)
        except discord.Forbidden:
            await interaction.followup.send(
                "Purge failed: I do not have permission to manage messages here.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.followup.send(
                "Purge failed due to a Discord API error. Please try again.",
                ephemeral=True,
            )
            return

        deleted_count = len(deleted_messages)
        await interaction.followup.send(
            f"✅ Deleted **{deleted_count}** message(s) using filter **{target}** from the last **{amount}** message(s).",
            ephemeral=True,
        )

    @purge.error
    async def purge_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        """Return clear permission guidance for known slash-command check failures."""

        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the **Manage Messages** permission to use `/purge`.",
                ephemeral=True,
            )
            return

        if isinstance(error, app_commands.BotMissingPermissions):
            await interaction.response.send_message(
                "I need **Manage Messages** and **Read Message History** permissions to use `/purge`.",
                ephemeral=True,
            )
            return

        raise error


async def setup(bot: commands.Bot) -> None:
    """Discord extension entrypoint for loading this cog."""

    await bot.add_cog(PurgeCog(bot))
