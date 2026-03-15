"""Discord cog implementing slash-command based Habbo motto verification."""

from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from habbo_verification_core import (
    BadgeRoleMapper,
    HabboApiError,
    ServerConfigStore,
    VerificationManager,
    VerifiedUserStore,
    fetch_habbo_group_ids,
    fetch_habbo_profile,
    motto_contains_code,
)


class HabboVerificationCog(commands.Cog):
    """Cog for Discord users to verify ownership of a Habbo account via motto code."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Store one active challenge per Discord user with a fixed 5 minute TTL.
        self.manager = VerificationManager(ttl_minutes=5)
        # Persist successful verification links in JSON/VerifiedUsers.json.
        self.verified_store = VerifiedUserStore()
        # Resolve Discord roles from Habbo groups using JSON/BadgesToRoles.json.
        self.badge_role_mapper = BadgeRoleMapper()
        # Resolve audit-log destination from serverconfig.json.
        self.server_config_store = ServerConfigStore()

    @app_commands.command(
        name="verify",
        description="Verify your Habbo account by putting a temporary code in your motto.",
    )
    @app_commands.describe(
        habbo_name="Your Habbo username",
    )
    async def verify(
        self,
        interaction: discord.Interaction,
        habbo_name: str,
    ) -> None:
        """Create/check a verification challenge and validate against Habbo public API."""

        challenge = self.manager.get_or_create(interaction.user.id, habbo_name)

        # Check the active challenge and validate the Habbo motto in one command call.
        if challenge.code and self.manager.get_active(interaction.user.id) == challenge:
            try:
                profile = fetch_habbo_profile(habbo_name)
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
                verified_habbo_name = str(profile.get("name", habbo_name))

                # Change #1: persist verified Discord<->Habbo mapping.
                self.verified_store.save(
                    discord_id=str(interaction.user.id),
                    habbo_username=verified_habbo_name,
                )

                # Change #2: synchronize guild roles based on Habbo groups.
                role_status, assigned_role_names = await self._assign_roles_from_habbo_groups(interaction, profile)

                # Audit the changes made by the bot (mapping save + role sync result).
                await self._send_audit_log(
                    interaction=interaction,
                    action="habbo_verification_success",
                    details={
                        "discord_user_id": str(interaction.user.id),
                        "discord_user": str(interaction.user),
                        "habbo_username": verified_habbo_name,
                        "saved_mapping": "yes",
                        "role_sync_status": role_status,
                        "assigned_roles": ", ".join(assigned_role_names) if assigned_role_names else "none",
                    },
                )

                self.manager.clear(interaction.user.id)
                await interaction.response.send_message(
                    embed=self._build_embed(
                        title="Verification Successful",
                        description=(
                            "Your Habbo motto includes the verification code. "
                            "You are now verified and your link has been saved."
                        ),
                        challenge_code=challenge.code,
                        expires_at=challenge.expires_at,
                        color=discord.Color.green(),
                        extra_field=("Role Sync", role_status),
                    ),
                    ephemeral=True,
                )
                return

            await interaction.response.send_message(
                embed=self._build_embed(
                    title="Verification Failed",
                    description=(
                        "Your Habbo motto does not include the verification code yet. "
                        "Add the code below, save your motto, and run /verify again."
                    ),
                    challenge_code=challenge.code,
                    expires_at=challenge.expires_at,
                    color=discord.Color.red(),
                    extra_field=("Current Motto", str(profile.get("motto", "(empty)"))),
                ),
                ephemeral=True,
            )

    async def _assign_roles_from_habbo_groups(self, interaction: discord.Interaction, profile: dict) -> tuple[str, list[str]]:
        """Assign Discord roles using Habbo group memberships and mapping JSON.

        Returns:
            A tuple of:
            - human-readable status text
            - list of assigned role names (for audit logging)
        """

        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return "Skipped (roles can only be assigned inside a server).", []

        unique_id = str(profile.get("uniqueId", "")).strip()
        if not unique_id:
            return "Skipped (Habbo profile has no uniqueId for group lookup).", []

        try:
            habbo_group_ids = fetch_habbo_group_ids(unique_id)
            role_ids = self.badge_role_mapper.resolve_role_ids(habbo_group_ids)
        except HabboApiError:
            return "Skipped (could not fetch Habbo groups right now).", []

        if not role_ids:
            return "No matching roles found from your Habbo groups.", []

        roles_to_add = []
        for role_id in role_ids:
            role = interaction.guild.get_role(role_id)
            if role is not None:
                roles_to_add.append(role)

        if not roles_to_add:
            return "No mapped roles exist in this server.", []

        try:
            await interaction.user.add_roles(*roles_to_add, reason="Habbo verification role sync", atomic=False)
        except discord.Forbidden:
            return "Failed (bot lacks permission to assign one or more roles).", []

        role_names = [role.name for role in roles_to_add]
        return "Assigned: " + ", ".join(role_names), role_names

    async def _send_audit_log(self, interaction: discord.Interaction, action: str, details: dict[str, str]) -> None:
        """Send an audit-style embed to the configured channel from serverconfig.json."""

        if not interaction.guild:
            return

        channel_id = self.server_config_store.get_audit_channel_id()
        if channel_id is None:
            return

        channel = interaction.guild.get_channel(channel_id)
        if channel is None:
            return

        embed = discord.Embed(
            title="Habbo Verification Audit",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Action", value=action, inline=False)
        for key, value in details.items():
            embed.add_field(name=key.replace("_", " ").title(), value=value, inline=False)

        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            # Audit logging should never block verification UX.
            return

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
        embed.add_field(name="How to Verify", value="1) Put code in motto\n2) Save\n3) Run /verify", inline=False)
        if extra_field:
            embed.add_field(name=extra_field[0], value=extra_field[1], inline=False)
        return embed


async def setup(bot: commands.Bot) -> None:
    """discord.py extension entry point."""

    await bot.add_cog(HabboVerificationCog(bot))
