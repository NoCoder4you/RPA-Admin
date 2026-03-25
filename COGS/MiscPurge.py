"""Discord cog that provides grouped moderation `/purge` slash commands."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


class PurgeCog(commands.Cog):
    """Moderation cog containing staff-only purge subcommands for bulk message cleanup."""

    # Define `/purge` as a command group so each cleanup mode is a separate subcommand.
    purge_group = app_commands.Group(
        name="purge",
        description="Bulk delete recent messages from this channel.",
    )

    def __init__(self, bot: commands.Bot) -> None:
        # Keep a bot reference for consistency with the project's other cogs.
        self.bot = bot

    async def _run_purge(
        self,
        interaction: discord.Interaction,
        amount: int,
        *,
        mode_label: str,
        check,
    ) -> None:
        """Shared purge runner used by all `/purge` subcommands.

        This helper centralizes defer, permission/API error handling, and response
        formatting so every subcommand behaves consistently.
        """

        # Purge must run inside a guild text channel where moderation and history exist.
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "This command can only be used in a server text channel.",
                ephemeral=True,
            )
            return

        # This operation can take more than 3 seconds, so acknowledge immediately.
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            # Use bulk purge to delete up to `amount` matching messages from recent history.
            deleted_messages = await interaction.channel.purge(limit=amount, check=check)
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
            f"✅ Deleted **{deleted_count}** message(s) for **{mode_label}** from the last **{amount}** message(s).",
            ephemeral=True,
        )

    @purge_group.command(name="all", description="Delete all messages in the inspected window.")
    @app_commands.describe(amount="How many recent messages to inspect and purge (1-1000)")
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.checks.bot_has_permissions(manage_messages=True, read_message_history=True)
    async def purge_all(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[int, 1, 1000],
    ) -> None:
        """Delete all recent messages up to the selected inspection amount."""

        # `all` mode intentionally removes any message regardless of author type.
        await self._run_purge(
            interaction,
            amount,
            mode_label="all messages",
            check=lambda _message: True,
        )

    @purge_group.command(name="bots", description="Delete only bot and webhook messages.")
    @app_commands.describe(amount="How many recent messages to inspect and purge (1-1000)")
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.checks.bot_has_permissions(manage_messages=True, read_message_history=True)
    async def purge_bots(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[int, 1, 1000],
    ) -> None:
        """Delete recent bot/webhook-authored messages up to the selected amount."""

        def check(message: discord.Message) -> bool:
            # Match bot-authored content and webhook output for automation cleanup.
            return bool(message.author.bot or message.webhook_id is not None)

        await self._run_purge(
            interaction,
            amount,
            mode_label="bot/webhook messages",
            check=check,
        )

    @purge_group.command(name="users", description="Delete only human user messages.")
    @app_commands.describe(amount="How many recent messages to inspect and purge (1-1000)")
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.checks.bot_has_permissions(manage_messages=True, read_message_history=True)
    async def purge_users(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[int, 1, 1000],
    ) -> None:
        """Delete recent non-bot, non-webhook messages up to the selected amount."""

        def check(message: discord.Message) -> bool:
            # Keep this strictly human-only by excluding bots and webhooks.
            return not message.author.bot and message.webhook_id is None

        await self._run_purge(
            interaction,
            amount,
            mode_label="human user messages",
            check=check,
        )

    @purge_group.command(name="member", description="Delete messages from one specific member only.")
    @app_commands.describe(
        member="The member whose messages should be deleted",
        amount="How many recent messages to inspect and purge (1-1000)",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.checks.bot_has_permissions(manage_messages=True, read_message_history=True)
    async def purge_member(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount: app_commands.Range[int, 1, 1000],
    ) -> None:
        """Delete recent messages authored only by the selected member."""

        def check(message: discord.Message) -> bool:
            # Match only the selected member's messages to support targeted cleanup.
            return message.author.id == member.id

        await self._run_purge(
            interaction,
            amount,
            mode_label=f"messages from {member.mention}",
            check=check,
        )

    @purge_all.error
    @purge_bots.error
    @purge_users.error
    @purge_member.error
    async def purge_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        """Return clear permission guidance for known slash-command check failures."""

        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the **Manage Messages** permission to use `/purge` commands.",
                ephemeral=True,
            )
            return

        if isinstance(error, app_commands.BotMissingPermissions):
            await interaction.response.send_message(
                "I need **Manage Messages** and **Read Message History** permissions to use `/purge` commands.",
                ephemeral=True,
            )
            return

        raise error


async def setup(bot: commands.Bot) -> None:
    """Discord extension entrypoint for loading this cog."""

    await bot.add_cog(PurgeCog(bot))
