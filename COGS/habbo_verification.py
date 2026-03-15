"""Discord cog implementing slash-command based Habbo motto verification."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

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

        # Defer immediately so slow Habbo API calls do not expire the interaction.
        await interaction.response.defer(ephemeral=True, thinking=True)

        discord_id = str(interaction.user.id)

        # Security: if already verified, always use the stored Habbo account for role sync.
        stored_habbo_name = self.verified_store.get_habbo_username(discord_id)
        if stored_habbo_name:
            try:
                stored_profile = fetch_habbo_profile(stored_habbo_name)
            except HabboApiError as exc:
                await interaction.followup.send(
                    embed=self._build_embed(
                        title="Already Verified",
                        description=(
                            "You are already verified, but I could not refresh your stored Habbo profile "
                            "for role sync right now. Please try again in a moment."
                        ),
                        challenge_code="N/A",
                        expires_at=datetime.now(timezone.utc),
                        color=discord.Color.orange(),
                        extra_field=("Error", str(exc)),
                    ),
                    ephemeral=True,
                )
                return

            role_status, added_role_names, removed_role_names = await self._assign_roles_from_habbo_groups(interaction, stored_profile)
            await self._send_audit_log(
                interaction=interaction,
                action="habbo_verification_already_verified",
                details={
                    "discord_user_id": discord_id,
                    "discord_user": str(interaction.user),
                    "habbo_username": stored_habbo_name,
                    "role_sync_status": role_status,
                    "roles_added": ", ".join(added_role_names) if added_role_names else "none",
                    "roles_removed": ", ".join(removed_role_names) if removed_role_names else "none",
                },
            )
            await interaction.followup.send(
                embed=self._build_embed(
                    title="Already Verified",
                    description=(
                        "You are already verified, so you do not need to add a new code to your motto. "
                        "I have synced your roles from your stored verified Habbo account."
                    ),
                    challenge_code="N/A",
                    expires_at=datetime.now(timezone.utc),
                    color=discord.Color.blue(),
                    extra_field=("Role Sync", role_status),
                    thumbnail_url=self._build_avatar_thumbnail_url(stored_profile),
                ),
                ephemeral=True,
            )
            return

        # First-time verification path: use the currently provided Habbo name.
        try:
            profile = fetch_habbo_profile(habbo_name)
        except HabboApiError as exc:
            challenge = self.manager.get_or_create(interaction.user.id, habbo_name)
            await interaction.followup.send(
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

        challenge = self.manager.get_or_create(interaction.user.id, habbo_name)

        if motto_contains_code(profile, challenge.code):
            verified_habbo_name = str(profile.get("name", habbo_name))
            self.verified_store.save(
                discord_id=discord_id,
                habbo_username=verified_habbo_name,
            )

            role_status, added_role_names, removed_role_names = await self._assign_roles_from_habbo_groups(interaction, profile)
            await self._send_audit_log(
                interaction=interaction,
                action="habbo_verification_success",
                details={
                    "discord_user_id": discord_id,
                    "discord_user": str(interaction.user),
                    "habbo_username": verified_habbo_name,
                    "saved_mapping": "yes",
                    "role_sync_status": role_status,
                    "roles_added": ", ".join(added_role_names) if added_role_names else "none",
                    "roles_removed": ", ".join(removed_role_names) if removed_role_names else "none",
                },
            )

            self.manager.clear(interaction.user.id)
            await interaction.followup.send(
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
                    thumbnail_url=self._build_avatar_thumbnail_url(profile),
                ),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
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
                thumbnail_url=self._build_avatar_thumbnail_url(profile),
            ),
            ephemeral=True,
        )

    async def _assign_roles_from_habbo_groups(
        self,
        interaction: discord.Interaction,
        profile: dict,
    ) -> tuple[str, list[str], list[str]]:
        """Synchronize mapped Discord roles from Habbo groups in interaction context."""

        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return "Skipped (roles can only be assigned inside a server).", [], []

        unique_id = str(profile.get("uniqueId", "")).strip()
        if not unique_id:
            return "Skipped (Habbo profile has no uniqueId for group lookup).", [], []

        try:
            habbo_group_ids = fetch_habbo_group_ids(unique_id)
            role_ids = self.badge_role_mapper.resolve_role_ids(habbo_group_ids)
        except HabboApiError:
            return "Skipped (could not fetch Habbo groups right now).", [], []

        # Build target role set from current Habbo groups and current guild mappings.
        target_roles: list[discord.Role] = []
        for role_id in role_ids:
            role = interaction.guild.get_role(role_id)
            if role is not None:
                target_roles.append(role)

        managed_role_ids = self.badge_role_mapper.get_all_mapped_role_ids()
        managed_roles: list[discord.Role] = []
        for role_id in managed_role_ids:
            role = interaction.guild.get_role(role_id)
            if role is not None:
                managed_roles.append(role)

        target_role_ids = {role.id for role in target_roles}
        current_role_ids = {role.id for role in interaction.user.roles}
        managed_role_id_set = {role.id for role in managed_roles}

        # Add roles that should now exist; remove mapped roles that are now stale.
        roles_to_add = [role for role in target_roles if role.id not in current_role_ids]
        roles_to_remove = [
            role for role in interaction.user.roles if role.id in managed_role_id_set and role.id not in target_role_ids
        ]

        try:
            if roles_to_add:
                await interaction.user.add_roles(*roles_to_add, reason="Habbo verification role sync", atomic=False)
            if roles_to_remove:
                await interaction.user.remove_roles(*roles_to_remove, reason="Habbo verification role sync", atomic=False)
        except discord.Forbidden:
            return "Failed (bot lacks permission to manage one or more roles).", [], []

        added_role_names = [role.name for role in roles_to_add]
        removed_role_names = [role.name for role in roles_to_remove]

        if not target_roles and not managed_roles:
            status = "No mapped roles exist in this server."
        elif not target_roles and not roles_to_remove:
            status = "No matching roles found from your Habbo groups."
        elif not added_role_names and not removed_role_names:
            status = "No role changes were required."
        else:
            status = (
                f"Added: {', '.join(added_role_names) if added_role_names else 'none'} | "
                f"Removed: {', '.join(removed_role_names) if removed_role_names else 'none'}"
            )

        await self._send_role_change_embed(
            guild=interaction.guild,
            member=interaction.user,
            source="verify",
            habbo_username=str(profile.get("name", "unknown")),
            added_role_names=added_role_names,
            removed_role_names=removed_role_names,
        )

        return status, added_role_names, removed_role_names

    async def _send_role_change_embed(
        self,
        *,
        guild: discord.Guild,
        member: discord.Member,
        source: str,
        habbo_username: str,
        added_role_names: list[str],
        removed_role_names: list[str],
    ) -> None:
        """Send a dedicated role-change embed for verify and auto-sync actions."""

        channel_id = self.server_config_store.get_audit_channel_id()
        if channel_id is None:
            return

        channel = guild.get_channel(channel_id)
        if channel is None:
            return

        embed = discord.Embed(
            title="Habbo Role Sync Update",
            description="Roles getting added and removed from Habbo role sync.",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Source", value=source, inline=False)
        embed.add_field(name="Discord User", value=f"{member} (`{member.id}`)", inline=False)
        embed.add_field(name="Habbo Username", value=habbo_username, inline=False)
        embed.add_field(name="Roles Added", value=", ".join(added_role_names) if added_role_names else "None", inline=False)
        embed.add_field(
            name="Roles Removed",
            value=", ".join(removed_role_names) if removed_role_names else "None",
            inline=False,
        )

        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return

    async def _send_audit_log(self, interaction: discord.Interaction, action: str, details: dict[str, str]) -> None:
        """Send an audit-style embed using the interaction's guild context."""

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
            return

    @staticmethod
    def _build_avatar_thumbnail_url(profile: dict) -> str | None:
        """Build Habbo avatar thumbnail URL from profile figure string."""

        figure_string = str(profile.get("figureString", "")).strip()
        if not figure_string:
            return None

        encoded_figure = quote(figure_string, safe="")
        return (
            "https://www.habbo.com/habbo-imaging/avatarimage"
            f"?figure={encoded_figure}&size=l&direction=2&head_direction=3&gesture=sml"
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
        thumbnail_url: str | None = None,
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
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        return embed


async def setup(bot: commands.Bot) -> None:
    """discord.py extension entry point."""

    await bot.add_cog(HabboVerificationCog(bot))
