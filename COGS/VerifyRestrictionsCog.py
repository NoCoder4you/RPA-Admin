"""Discord cog that manages restricted Habbo usernames and enforces verification restrictions."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from habbo_verification_core import VerifyRestrictionStore


class VerifyRestrictionsCog(commands.Cog):
    """Staff cog for maintaining the DNH and BoS username restriction lists."""

    # Keep one parent command so all restriction-management actions stay discoverable together.
    verifyrestrictions_group = app_commands.Group(
        name="verifyrestrictions",
        description="Manage Habbo usernames restricted during verification.",
    )
    # The user requested group-specific add/remove commands, so expose dedicated DNH and BoS subgroups.
    dnh_group = app_commands.Group(
        name="dnh",
        description="Manage the Do Not Hire verification restriction list.",
        parent=verifyrestrictions_group,
    )
    bos_group = app_commands.Group(
        name="bos",
        description="Manage the Ban on Sight verification restriction list.",
        parent=verifyrestrictions_group,
    )

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

    @dnh_group.command(name="add", description="Add a Habbo username to the DNH restriction list.")
    @app_commands.describe(username="Habbo username that should be marked Do Not Hire during verification")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def dnh_add(
        self,
        interaction: discord.Interaction,
        username: str,
    ) -> None:
        """Add one Habbo username to the DNH restriction group."""

        await self._handle_restriction_update(
            interaction,
            group_name=VerifyRestrictionStore.GROUP_DNH,
            username=username,
            action="add",
        )

    @dnh_group.command(name="remove", description="Remove a Habbo username from the DNH restriction list.")
    @app_commands.describe(username="Habbo username that should no longer be marked Do Not Hire during verification")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def dnh_remove(
        self,
        interaction: discord.Interaction,
        username: str,
    ) -> None:
        """Remove one Habbo username from the DNH restriction group."""

        await self._handle_restriction_update(
            interaction,
            group_name=VerifyRestrictionStore.GROUP_DNH,
            username=username,
            action="remove",
        )

    @bos_group.command(name="add", description="Add a Habbo username to the BoS restriction list.")
    @app_commands.describe(username="Habbo username that should be marked Ban on Sight during verification")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def bos_add(
        self,
        interaction: discord.Interaction,
        username: str,
    ) -> None:
        """Add one Habbo username to the BoS restriction group."""

        await self._handle_restriction_update(
            interaction,
            group_name=VerifyRestrictionStore.GROUP_BOS,
            username=username,
            action="add",
        )

    @bos_group.command(name="remove", description="Remove a Habbo username from the BoS restriction list.")
    @app_commands.describe(username="Habbo username that should no longer be marked Ban on Sight during verification")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def bos_remove(
        self,
        interaction: discord.Interaction,
        username: str,
    ) -> None:
        """Remove one Habbo username from the BoS restriction group."""

        await self._handle_restriction_update(
            interaction,
            group_name=VerifyRestrictionStore.GROUP_BOS,
            username=username,
            action="remove",
        )

    @dnh_add.error
    @dnh_remove.error
    @bos_add.error
    @bos_remove.error
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
