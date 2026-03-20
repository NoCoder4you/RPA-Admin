"""Discord cog that manages restricted Habbo usernames and enforces verification restrictions."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from habbo_verification_core import VerifyRestrictionStore


class VerifyRestrictionsCog(commands.Cog):
    """Staff cog for maintaining the DNH and BoS username restriction lists."""

    def __init__(self, bot: commands.Bot) -> None:
        # Keep shared state on the cog so command callbacks stay easy to test and mock.
        self.bot = bot
        self.restriction_store = VerifyRestrictionStore()

    async def _handle_restriction_update(
        self,
        interaction: discord.Interaction,
        *,
        group_name: str,
        username: str,
        action: str,
    ) -> None:
        """Execute one add/remove mutation and return a consistent ephemeral result message."""

        normalized_group = self.restriction_store._normalize_group_name(group_name)
        normalized_username = self.restriction_store._normalize_username(username)

        if action == "add":
            changed = self.restriction_store.add_username(group_name, username)
            if changed:
                await interaction.response.send_message(
                    f"✅ Added **{normalized_username}** to **{normalized_group}** verification restrictions.",
                    ephemeral=True,
                )
                return

            await interaction.response.send_message(
                f"**{normalized_username}** is already listed in **{normalized_group}**.",
                ephemeral=True,
            )
            return

        if action == "remove":
            changed = self.restriction_store.remove_username(group_name, username)
            if changed:
                await interaction.response.send_message(
                    f"✅ Removed **{normalized_username}** from **{normalized_group}** verification restrictions.",
                    ephemeral=True,
                )
                return

            await interaction.response.send_message(
                f"**{normalized_username}** was not listed in **{normalized_group}**.",
                ephemeral=True,
            )
            return

        raise ValueError(f"Unsupported restriction action: {action}")

    @app_commands.command(name="dnh", description="Add or remove a Habbo username from the DNH restriction list.")
    @app_commands.describe(
        action="Whether to add or remove the Habbo username from the DNH list",
        username="Habbo username that should be updated in the DNH restriction list",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="add", value="add"),
            app_commands.Choice(name="remove", value="remove"),
        ]
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def dnh(
        self,
        interaction: discord.Interaction,
        action: str,
        username: str,
    ) -> None:
        """Add or remove one Habbo username from the DNH restriction list."""

        await self._handle_restriction_update(
            interaction,
            group_name=VerifyRestrictionStore.GROUP_DNH,
            username=username,
            action=action,
        )

    @app_commands.command(name="bos", description="Add or remove a Habbo username from the BoS restriction list.")
    @app_commands.describe(
        action="Whether to add or remove the Habbo username from the BoS list",
        username="Habbo username that should be updated in the BoS restriction list",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="add", value="add"),
            app_commands.Choice(name="remove", value="remove"),
        ]
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def bos(
        self,
        interaction: discord.Interaction,
        action: str,
        username: str,
    ) -> None:
        """Add or remove one Habbo username from the BoS restriction list."""

        await self._handle_restriction_update(
            interaction,
            group_name=VerifyRestrictionStore.GROUP_BOS,
            username=username,
            action=action,
        )

    @dnh.error
    @bos.error
    async def verifyrestrictions_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Return clear permission guidance for the combined DNH and BoS restriction commands."""

        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the **Manage Server** permission to use `/dnh` or `/bos`.",
                ephemeral=True,
            )
            return

        raise error


async def setup(bot: commands.Bot) -> None:
    """Discord extension entrypoint for loading this cog."""

    await bot.add_cog(VerifyRestrictionsCog(bot))
