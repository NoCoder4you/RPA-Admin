"""Discord cog implementing slash-command based Habbo motto verification."""

from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from habbo_verification_core import (
    HabboApiError,
    VerificationManager,
    fetch_habbo_profile,
    motto_contains_code,
)


class HabboVerificationCog(commands.Cog):
    """Cog for Discord users to verify ownership of a Habbo account via motto code."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Store one active challenge per Discord user with a fixed 5 minute TTL.
        self.manager = VerificationManager(ttl_minutes=5)

    @app_commands.command(
        name="verify_habbo",
        description="Verify your Habbo account by putting a temporary code in your motto.",
    )
    @app_commands.describe(
        habbo_name="Your Habbo username",
        hotel_domain='Habbo domain (default: "habbo.com")',
    )
    async def verify_habbo(
        self,
        interaction: discord.Interaction,
        habbo_name: str,
        hotel_domain: str = "habbo.com",
    ) -> None:
        """Create/check a verification challenge and validate against Habbo public API."""

        challenge = self.manager.get_or_create(interaction.user.id, habbo_name)

        # If this is a new/expired challenge, send clear instructions before checking.
        if challenge.code and self.manager.get_active(interaction.user.id) == challenge:
            # Attempt the API check immediately so users can run the same command repeatedly
            # after updating their motto without needing a second command.
            try:
                profile = fetch_habbo_profile(habbo_name, hotel_domain=hotel_domain)
            except HabboApiError as exc:
                await interaction.response.send_message(
                    embed=self._build_embed(
                        title="Habbo API Error",
                        description=(
                            "I could not fetch your Habbo profile right now. "
                            "Please try again in a moment."
                        ),
                        challenge_code=challenge.code,
                        expires_at=challenge.expires_at,
                        color=discord.Color.orange(),
                        extra_field=("Error", str(exc)),
                    ),
                    ephemeral=True,
                )
                return

            if motto_contains_code(profile, challenge.code):
                self.manager.clear(interaction.user.id)
                await interaction.response.send_message(
                    embed=self._build_embed(
                        title="Verification Successful",
                        description=(
                            "Your Habbo motto includes the verification code. "
                            "You are now verified."
                        ),
                        challenge_code=challenge.code,
                        expires_at=challenge.expires_at,
                        color=discord.Color.green(),
                        extra_field=("Habbo", profile.get("name", habbo_name)),
                    ),
                    ephemeral=True,
                )
                return

            await interaction.response.send_message(
                embed=self._build_embed(
                    title="Verification Failed",
                    description=(
                        "Your Habbo motto does not include the verification code yet. "
                        "Add the code below, save your motto, and run /verify_habbo again."
                    ),
                    challenge_code=challenge.code,
                    expires_at=challenge.expires_at,
                    color=discord.Color.red(),
                    extra_field=("Current Motto", str(profile.get("motto", "(empty)"))),
                ),
                ephemeral=True,
            )

    @staticmethod
    def _build_embed(
        *,
        title: str,
        description: str,
        challenge_code: str,
        expires_at: datetime,
        color: discord.Color,
        extra_field: tuple[str, str] | None = None,
    ) -> discord.Embed:
        """Return a consistent, concise embed for all verification states."""

        embed = discord.Embed(title=title, description=description, color=color)
        embed.add_field(name="Verification Code", value=f"`{challenge_code}`", inline=False)
        embed.add_field(
            name="Expires",
            value=f"<t:{int(expires_at.replace(tzinfo=timezone.utc).timestamp())}:R>",
            inline=True,
        )
        embed.add_field(name="How to Verify", value="1) Put code in motto\n2) Save\n3) Run /verify_habbo", inline=False)
        if extra_field:
            embed.add_field(name=extra_field[0], value=extra_field[1], inline=False)
        return embed


async def setup(bot: commands.Bot) -> None:
    """discord.py extension entry point."""

    await bot.add_cog(HabboVerificationCog(bot))
