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
    VerifyRestrictionStore,
    fetch_habbo_group_ids,
    fetch_habbo_profile,
    motto_contains_code,
)


WHITE_CHECK_MARK_EMOJI = "✅"
AWAITING_VERIFICATION_CHANNEL_ID = 1479391662076723224
VERIFICATION_LOG_CHANNEL_ID = 1481456997726425168


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
        # Read JSON-backed DNH/BoS verification restrictions managed by staff slash commands.
        self.verify_restriction_store = VerifyRestrictionStore()

    async def _send_awaiting_verification_embed(
        self,
        *,
        guild: discord.Guild,
        member: discord.Member,
    ) -> None:
        """Post a standardized onboarding embed in the fixed Awaiting Verification channel."""

        # Keep all staging notifications in the exact moderation channel requested by staff so
        # every newly queued member gets the same visible ping and instructions.
        channel = guild.get_channel(AWAITING_VERIFICATION_CHANNEL_ID)
        if channel is None:
            return

        embed = discord.Embed(
            title="Awaiting Verification",
            description=(
                f"{member.mention}, you're now queued for verification. Follow the steps below to verify your account."
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="Step 1",
            value="Run `/verify` with your Habbo username to get your verification code from the bot.",
            inline=False,
        )
        embed.add_field(
            name="Step 2",
            value="Copy that code into your Habbo motto and save the change in Habbo.",
            inline=False,
        )
        embed.add_field(
            name="Step 3",
            value="Come back here and run `/verify` again so the bot can confirm the code in your motto.",
            inline=False,
        )
        embed.add_field(
            name="Need Help?",
            value="If the code does not work, double-check the spelling in your motto and ask staff for help in this channel.",
            inline=False,
        )

        try:
            # Mention the member in the message body too so Discord triggers the expected notification.
            await channel.send(content=member.mention, embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return

    @app_commands.command(
        name="verify",
        description="Verify your Habbo account by putting a temporary code in your motto.",
    )
    @app_commands.describe(
        username="Your Habbo username",
    )
    async def verify(
        self,
        interaction: discord.Interaction,
        username: str,
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
            verified_role_status, verified_role_names = await self._ensure_verified_role(interaction)
            if verified_role_status != "No Verified role change was required.":
                # Surface the baseline Verified-role sync result alongside mapped Habbo roles so
                # staff can immediately see whether core verification access was restored.
                role_status = f"{role_status} | Verified Role: {verified_role_status}"
                added_role_names = [*added_role_names, *verified_role_names]
            # Do not post a fresh verification audit every time an already-verified member reruns
            # /verify just to resync roles. Staff only want the audit entry on the initial successful
            # verification, while dedicated username-change logging still covers approved renames.
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
            profile = fetch_habbo_profile(username)
        except HabboApiError as exc:
            challenge = self.manager.get_or_create(interaction.user.id, username)
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

        challenge = self.manager.get_or_create(interaction.user.id, username)

        if motto_contains_code(profile, challenge.code):
            verified_habbo_name = str(profile.get("name", username))
            self.verified_store.save(
                discord_id=discord_id,
                habbo_username=verified_habbo_name,
            )

            role_status, added_role_names, removed_role_names = await self._assign_roles_from_habbo_groups(interaction, profile)
            verified_role_status, verified_role_names = await self._ensure_verified_role(interaction)
            if verified_role_status != "No Verified role change was required.":
                # Always grant the stable Verified role after a successful motto check, even when
                # the member has no mapped Habbo-group roles to add during the same verification run.
                role_status = f"{role_status} | Verified Role: {verified_role_status}"
                added_role_names = [*added_role_names, *verified_role_names]
            restriction_status = await self._enforce_restrictions_after_verification(
                interaction=interaction,
                habbo_username=verified_habbo_name,
            )
            await self._send_audit_log(
                interaction=interaction,
                action="habbo_verification_success",
                details={
                    "discord_user_id": discord_id,
                    "discord_user": str(interaction.user),
                    "habbo_username": verified_habbo_name,
                    "saved_mapping": "yes",
                    "role_sync_status": role_status,
                    "restriction_status": restriction_status,
                    "roles_added": ", ".join(added_role_names) if added_role_names else "none",
                    "roles_removed": ", ".join(removed_role_names) if removed_role_names else "none",
                    "figure_string": str(profile.get("figureString", "")),
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
                    extra_field=("Restriction Check", restriction_status),
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

    async def _enforce_restrictions_after_verification(
        self,
        *,
        interaction: discord.Interaction,
        habbo_username: str,
    ) -> str:
        """Apply DNH or BoS policy immediately after a successful verification."""

        restriction_group = self.verify_restriction_store.get_group_for_username(habbo_username)
        if restriction_group is None:
            return "No restriction matched."

        if restriction_group == VerifyRestrictionStore.GROUP_DNH:
            removal_status, removed_role_names = await self._remove_employee_roles_for_restricted_member(interaction)
            if removed_role_names:
                return f"DNH matched; removed employee roles: {', '.join(removed_role_names)}."
            return f"DNH matched; {removal_status}"

        if restriction_group == VerifyRestrictionStore.GROUP_BOS:
            dm_status = await self._notify_bos_member(interaction)
            ban_status = await self._ban_bos_member(interaction)
            return f"BoS matched; DM: {dm_status}; Ban: {ban_status}"

        return f"Restriction group {restriction_group} is not implemented."

    async def _remove_employee_roles_for_restricted_member(
        self,
        interaction: discord.Interaction,
    ) -> tuple[str, list[str]]:
        """Strip all mapped employee roles so DNH users cannot retain staff access after verify."""

        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return "restriction check ran outside a guild, so no roles were changed.", []

        managed_role_ids = self.badge_role_mapper.get_all_mapped_role_ids()
        roles_to_remove = [role for role in interaction.user.roles if role.id in managed_role_ids]
        if not roles_to_remove:
            return "no employee-mapped roles were present to remove.", []

        try:
            await interaction.user.remove_roles(
                *roles_to_remove,
                reason="Verification restriction policy: DNH user cannot retain employee roles",
                atomic=False,
            )
        except discord.Forbidden:
            return "failed to remove employee roles because the bot lacks permissions.", []
        except discord.HTTPException:
            return "failed to remove employee roles because Discord rejected the request.", []

        return "employee roles removed.", [role.name for role in roles_to_remove]

    async def _notify_bos_member(self, interaction: discord.Interaction) -> str:
        """Inform BoS users that they must contact Foundation before joining."""

        guild_name = interaction.guild.name if interaction.guild is not None else "this server"
        try:
            await interaction.user.send(
                "You may not join **"
                f"{guild_name}"
                "** until you speak to a member of Foundation."
            )
        except (discord.Forbidden, discord.HTTPException, AttributeError):
            return "failed to send direct message."

        return "direct message sent."

    async def _ban_bos_member(self, interaction: discord.Interaction) -> str:
        """Ban BoS users immediately after verification so the restriction is enforced."""

        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return "ban skipped outside a guild."

        try:
            await interaction.guild.ban(
                interaction.user,
                reason="Verification restriction policy: BoS user must contact Foundation before joining",
            )
        except discord.Forbidden:
            return "ban failed because the bot lacks permissions."
        except discord.HTTPException:
            return "ban failed because Discord rejected the request."

        return "member banned from the server."


    async def _sync_member_nickname(self, interaction: discord.Interaction, habbo_username: str) -> str:
        """Rename the member in Discord so their nickname matches the verified Habbo username."""

        if interaction.guild is None:
            return "Skipped (nickname can only be changed inside a server)."

        member = interaction.user
        if getattr(member, "nick", None) == habbo_username:
            return "No nickname change was required."

        try:
            await member.edit(
                nick=habbo_username,
                reason="Habbo verification nickname sync",
            )
        except discord.Forbidden:
            return "Failed (bot lacks permission to manage this nickname)."
        except discord.HTTPException:
            return "Failed (Discord rejected the nickname update request)."

        return "Nickname updated to verified Habbo username."

    async def _ensure_verified_role(self, interaction: discord.Interaction) -> tuple[str, list[str]]:
        """Ensure successful verification also grants the baseline Discord Verified role."""

        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return "Skipped (Verified role can only be assigned inside a server).", []

        verified_role = discord.utils.get(interaction.guild.roles, name="Verified")
        if verified_role is None:
            return "Skipped (Verified role does not exist in this server).", []

        if verified_role in interaction.user.roles:
            return "No Verified role change was required.", []

        try:
            # Keep the baseline access grant separate from Habbo-group role sync so verification
            # still succeeds even when the server only relies on the standalone Verified role.
            await interaction.user.add_roles(verified_role, reason="Habbo verification verified-role sync")
        except discord.Forbidden:
            return "Failed (bot lacks permission to manage the Verified role).", []
        except discord.HTTPException:
            return "Failed (Discord rejected the Verified role update request).", []

        return "Verified role added.", [verified_role.name]

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
                f"Removed: {' '.join(removed_role_names) if removed_role_names else 'none'} "
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

        # Requirement: only post a role-sync update embed when an actual role delta exists.
        # If both lists are empty, there is nothing meaningful for moderators to review.
        if not added_role_names and not removed_role_names:
            return

        channel_id = self.server_config_store.get_audit_channel_id()
        if channel_id is None:
            return

        channel = guild.get_channel(channel_id)
        if channel is None:
            return

        embed = discord.Embed(
            title="Habbo Role Sync Update",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )

        # Always mention the user so moderators can jump straight to who was updated.
        embed.add_field(name="User", value=member.mention, inline=False)

        # Only include role sections when there is an actual role delta to report.
        if added_role_names:
            embed.add_field(name="Added Roles", value=", ".join(added_role_names), inline=False)
        if removed_role_names:
            embed.add_field(name="Removed Roles", value=", ".join(removed_role_names), inline=False)

        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return

    async def _send_audit_log(self, interaction: discord.Interaction, action: str, details: dict[str, str]) -> None:
        """Send a streamlined verification audit embed to the fixed staff verification-log channel."""

        if not interaction.guild:
            return

        # Verification audit entries must always land in the dedicated staff review channel.
        channel = interaction.guild.get_channel(VERIFICATION_LOG_CHANNEL_ID)
        if channel is None:
            channel = self.bot.get_channel(VERIFICATION_LOG_CHANNEL_ID)
        if channel is None:
            return

        embed = discord.Embed(
            title="Habbo Verification Audit",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        # Mention the user inside the embed itself so moderators can identify the verified account at a glance.
        embed.add_field(name="User", value=interaction.user.mention, inline=False)

        # Keep the audit embed focused on identity details staff still need after verification.
        hidden_keys = {"role_sync_status", "roles_added", "roles_removed", "figure_string"}
        for key, value in details.items():
            if key in hidden_keys:
                continue
            embed.add_field(name=key.replace("_", " ").title(), value=value, inline=False)

        thumbnail_url = self._build_avatar_thumbnail_url({"figureString": details.get("figure_string", "")})
        if not thumbnail_url:
            try:
                profile = fetch_habbo_profile(details.get("habbo_username", ""))
            except HabboApiError:
                profile = None
            thumbnail_url = self._build_avatar_thumbnail_url(profile or {})

        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)

        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return


    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """Handle verification-message reactions and keep only bot-owned reactions on that message."""

        # Ignore DM reactions because role assignment only makes sense inside a guild.
        if payload.guild_id is None:
            return

        # Prevent bot self-actions and other automation accounts from receiving roles.
        if self.bot.user is not None and payload.user_id == self.bot.user.id:
            return

        configured_message_id = self.server_config_store.get_verification_reaction_message_id()
        if configured_message_id is None:
            return

        # Gate this handler to one explicit message ID stored in serverconfig.json.
        if payload.message_id != configured_message_id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        member = guild.get_member(payload.user_id)
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return

        # Enforce policy: user reactions should not persist on the verification message.
        # This keeps the message reaction list effectively bot-only after processing.
        await self._remove_member_reaction_from_message(payload, member)

        emoji = str(payload.emoji)
        allowed_green_checks = {WHITE_CHECK_MARK_EMOJI, "☑️", "✔️"}
        if emoji not in allowed_green_checks:
            return

        role = discord.utils.get(guild.roles, name="Awaiting Verification")
        if role is None or role in member.roles:
            return

        try:
            # Assign the staging role required before moderators complete verification review.
            await member.add_roles(role, reason="Reacted with green check on verification message")
        except (discord.Forbidden, discord.HTTPException):
            return

        # Mirror the rules-flow onboarding notice so any path that grants the staging role also posts
        # the required embed and ping in the dedicated verification queue channel.
        await self._send_awaiting_verification_embed(guild=guild, member=member)

    async def _remove_member_reaction_from_message(
        self,
        payload: discord.RawReactionActionEvent,
        member: discord.Member,
    ) -> None:
        """Remove the reacting member's reaction to keep the verification message bot-only."""

        channel = self.bot.get_channel(payload.channel_id)
        if channel is None:
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

        try:
            await message.remove_reaction(payload.emoji, member)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
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
    await bot.add_cog(HabboVerificationCog(bot))
