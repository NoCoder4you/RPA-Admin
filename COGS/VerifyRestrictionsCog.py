"""Discord cog that manages restricted Habbo usernames and enforces verification restrictions."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from habbo_verification_core import VerifyRestrictionStore


class VerifyRestrictionsCog(commands.Cog):
    """Staff cog for maintaining the DNH and BoS username restriction lists."""

    verifyrestrictions_group = app_commands.Group(
        name="verifyrestrictions",
        description="Manage Habbo usernames restricted during verification.",
    )

    def __init__(self, bot: commands.Bot) -> None:
        # Keep shared state on the cog so command callbacks stay easy to test and mock.
        self.bot = bot
        self.restriction_store = VerifyRestrictionStore()

    @verifyrestrictions_group.command(name="add", description="Add a Habbo username to the DNH or BoS restriction list.")
    @app_commands.describe(
        group_name="Restriction group to add the Habbo username to",
        username="Habbo username that should be restricted during verification",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def add_restriction(
        self,
        interaction: discord.Interaction,
        group_name: str,
        username: str,
    ) -> None:
        """Save one Habbo username into the requested restriction group."""

        try:
            created = self.restriction_store.add_username(group_name, username)
            normalized_group = self.restriction_store._normalize_group_name(group_name)
            normalized_username = self.restriction_store._normalize_username(username)
        except ValueError:
            await interaction.response.send_message(
                "Invalid group. Use **DNH** or **BoS**.",
                ephemeral=True,
            )
            return

        if created:
            await interaction.response.send_message(
                f"✅ Added **{normalized_username}** to **{normalized_group}** verification restrictions.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"**{normalized_username}** is already listed in **{normalized_group}**.",
            ephemeral=True,
        )

    @verifyrestrictions_group.command(name="remove", description="Remove a Habbo username from the DNH or BoS restriction list.")
    @app_commands.describe(
        group_name="Restriction group to remove the Habbo username from",
        username="Habbo username that should no longer be restricted during verification",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove_restriction(
        self,
        interaction: discord.Interaction,
        group_name: str,
        username: str,
    ) -> None:
        """Delete one Habbo username from the requested restriction group."""

        try:
            removed = self.restriction_store.remove_username(group_name, username)
            normalized_group = self.restriction_store._normalize_group_name(group_name)
            normalized_username = self.restriction_store._normalize_username(username)
        except ValueError:
            await interaction.response.send_message(
                "Invalid group. Use **DNH** or **BoS**.",
                ephemeral=True,
            )
            return

        if removed:
            await interaction.response.send_message(
                f"✅ Removed **{normalized_username}** from **{normalized_group}** verification restrictions.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"**{normalized_username}** was not listed in **{normalized_group}**.",
            ephemeral=True,
        )

    @add_restriction.autocomplete("group_name")
    @remove_restriction.autocomplete("group_name")
    async def group_name_autocomplete(
        self,
        _interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Offer only the two supported restriction groups to slash-command users."""

        available_groups = [VerifyRestrictionStore.GROUP_DNH, VerifyRestrictionStore.GROUP_BOS]
        lowered_current = current.lower().strip()
        return [
            app_commands.Choice(name=group_name, value=group_name)
            for group_name in available_groups
            if not lowered_current or lowered_current in group_name.lower()
        ]

    @add_restriction.error
    @remove_restriction.error
    async def verifyrestrictions_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Return clear permission guidance for the grouped restriction commands."""

        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the **Manage Server** permission to use `/verifyrestrictions`.",
                ephemeral=True,
            )
            return

        raise error


async def setup(bot: commands.Bot) -> None:
    """Discord extension entrypoint for loading this cog."""

    await bot.add_cog(VerifyRestrictionsCog(bot))
