"""Discord cog that manages moderator-approved Habbo username change requests."""

from __future__ import annotations

from datetime import datetime, timezone
import re

import discord
from discord import app_commands
from discord.ext import commands

from habbo_verification_core import HabboApiError, ServerConfigStore, VerifiedUserStore, fetch_habbo_profile


class UsernameChangeRequestView(discord.ui.View):
    """Interactive moderator controls for approving or declining a username-change request embed."""

    USER_ID_PATTERN = re.compile(r"<(?:@!?)?(\d+)>")

    def __init__(self, cog: "UsernameChangeCog", *, admin_role_id: int | None) -> None:
        # Keep the view persistent long enough for staff to action routine requests.
        super().__init__(timeout=None)
        self.cog = cog
        self.admin_role_id = admin_role_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Allow only configured Discord Admin role holders to use the moderation buttons."""

        if self.admin_role_id is None:
            await interaction.response.send_message(
                "This request cannot be actioned because the admin role is not configured.",
                ephemeral=True,
            )
            return False

        member_roles = getattr(interaction.user, "roles", [])
        if any(getattr(role, "id", None) == self.admin_role_id for role in member_roles):
            return True

        await interaction.response.send_message(
            "You need the configured Discord Admin role to use these buttons.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="username_change:accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Apply the requested change only after a moderator explicitly approves it."""

        await self._finalize_request(interaction, button, status="Accepted", color=discord.Color.green())

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, custom_id="username_change:decline")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Mark the request as declined and lock the moderation controls without changing stored data."""

        await self._finalize_request(interaction, button, status="Declined", color=discord.Color.red())

    async def _finalize_request(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
        *,
        status: str,
        color: discord.Color,
    ) -> None:
        """Update the embed status, apply approved changes, and disable further button input."""

        del button  # Button metadata is unused once Discord routes the callback.
        message = interaction.message
        if message is None or not message.embeds:
            await interaction.response.send_message(
                "I could not find the original username-change embed to update.",
                ephemeral=True,
            )
            return

        embed = message.embeds[0].copy()
        action_summary = "No username or nickname changes were applied."
        if status == "Accepted":
            action_summary = await self.cog.apply_username_change_from_embed(interaction, embed)

        self._upsert_status_field(embed, status=status, moderator=interaction.user.mention)
        self._upsert_action_summary_field(embed, summary=action_summary)
        embed.color = color

        # Disable every button once a final moderator decision has been made.
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

        await interaction.response.edit_message(embed=embed, view=self)

    @classmethod
    def _extract_member_id(cls, mention_text: str) -> int | None:
        """Parse a Discord mention field back into the user ID needed for approval-time updates."""

        match = cls.USER_ID_PATTERN.search(mention_text or "")
        if match is None:
            return None
        return int(match.group(1))

    @staticmethod
    def _upsert_status_field(embed: discord.Embed, *, status: str, moderator: str) -> None:
        """Insert or replace the request-status field so the moderation outcome is always visible."""

        status_value = f"{status} by {moderator}"
        for index, field in enumerate(embed.fields):
            if field.name == "Request Status":
                embed.set_field_at(index, name="Request Status", value=status_value, inline=False)
                return

        embed.add_field(name="Request Status", value=status_value, inline=False)

    @staticmethod
    def _upsert_action_summary_field(embed: discord.Embed, *, summary: str) -> None:
        """Record the outcome of approval-time processing directly on the moderator-facing embed."""

        for index, field in enumerate(embed.fields):
            if field.name == "Approval Result":
                embed.set_field_at(index, name="Approval Result", value=summary, inline=False)
                return

        embed.add_field(name="Approval Result", value=summary, inline=False)


class UsernameChangeCog(commands.Cog):
    """Self-service cog for requesting moderator-approved Habbo username changes."""

    AUTOROLES_EXTENSION = "COGS.ServerAutoRolesRPA"

    def __init__(self, bot: commands.Bot) -> None:
        # Keep shared dependencies on the cog so tests can replace them with stubs.
        self.bot = bot
        self.verified_store = VerifiedUserStore()
        self.server_config_store = ServerConfigStore()

    @app_commands.command(
        name="usernamechange",
        description="Request an update to your saved Habbo username after you rename your Habbo account.",
    )
    @app_commands.describe(username="Your new Habbo username")
    async def usernamechange(self, interaction: discord.Interaction, username: str) -> None:
        """Validate and submit a username-change request for moderator approval."""

        # Defer because the command performs API fetches before responding.
        await interaction.response.defer(ephemeral=True, thinking=True)
        result_message = await self._process_username_change(interaction, username)
        await interaction.followup.send(result_message, ephemeral=True)

    async def _process_username_change(self, interaction: discord.Interaction, username: str) -> str:
        """Validate the request and post it for review without mutating saved verification data."""

        discord_id = str(interaction.user.id)
        if not self.verified_store.is_verified(discord_id):
            return "You must already exist in VerifiedUsers.json before you can request a username change."

        stored_username = self.verified_store.get_habbo_username(discord_id)
        if not stored_username:
            return "You must already exist in VerifiedUsers.json before you can request a username change."

        normalized_username = username.strip()
        if not normalized_username:
            return "Please provide a valid Habbo username."

        if normalized_username.casefold() == stored_username.casefold():
            return "You cannot request the same Habbo username that is already saved for you."

        try:
            profile = fetch_habbo_profile(normalized_username)
        except HabboApiError as exc:
            return f"I could not fetch that Habbo profile right now: {exc}"

        requested_habbo_username = str(profile.get("name", normalized_username)).strip() or normalized_username
        if requested_habbo_username.casefold() == stored_username.casefold():
            return "You cannot request the same Habbo username that is already saved for you."

        posted = await self._send_verification_log_embed(
            interaction=interaction,
            previous_username=stored_username,
            requested_username=requested_habbo_username,
        )
        if not posted:
            return "Your request could not be submitted because the request channel is unavailable."

        return (
            f"Your username change request from **{stored_username}** to **{requested_habbo_username}** has been sent "
            "for admin approval. Your saved username and nickname will only update after the approve button is pressed."
        )

    async def apply_username_change_from_embed(self, interaction: discord.Interaction, embed: discord.Embed) -> str:
        """Apply the approved change described by the request embed and report what happened."""

        field_values = {field.name: field.value for field in embed.fields}
        member_id = UsernameChangeRequestView._extract_member_id(field_values.get("Member", ""))
        previous_username = field_values.get("Previous Username", "").strip()
        requested_username = field_values.get("Requested Username", "").strip()
        if member_id is None or not previous_username or not requested_username:
            return "Approval failed: the request embed is missing the member or username details needed to apply the change."

        # Re-fetch the current saved value so staff approval always acts on the latest persisted data.
        current_saved_username = self.verified_store.get_habbo_username(str(member_id))
        if not current_saved_username:
            return "Approval failed: the member is no longer present in VerifiedUsers.json."
        if current_saved_username.casefold() != previous_username.casefold():
            return (
                "Approval failed: the saved username changed after this request was submitted, so no stale update was applied."
            )

        member = interaction.guild.get_member(member_id) if interaction.guild else None
        target_interaction = type("ApprovalInteraction", (), {"guild": interaction.guild, "user": member})()

        self.verified_store.save(discord_id=str(member_id), habbo_username=requested_username)

        nickname_status = "Skipped (member is not currently in this server)."
        if member is not None:
            nickname_status = await self._sync_member_nickname(target_interaction, requested_username)

        reload_status = await self._reload_autoroles_cog()
        return (
            f"Saved username updated from **{current_saved_username}** to **{requested_username}**.\n"
            f"Nickname: {nickname_status}\n"
        )

    async def _sync_member_nickname(self, interaction: discord.Interaction, habbo_username: str) -> str:
        if interaction.guild is None:
            return "Skipped (nickname can only be changed inside a server)."

        member = interaction.user
        if member is None:
            return "Skipped (member is not currently in this server)."

        if getattr(member, "nick", None) == habbo_username:
            return "No nickname change was required."

        try:
            await member.edit(
                nick=habbo_username,
                reason="Approved Habbo username change",
            )
        except discord.Forbidden:
            return "Failed (bot lacks permission to manage this nickname)."
        except discord.HTTPException:
            return "Failed (Discord rejected the nickname update request)."

        return "Nickname updated to approved Habbo username."

    async def _reload_autoroles_cog(self) -> str:
        """Reload the automatic role updater so it immediately uses the refreshed username mapping."""

        try:
            await self.bot.reload_extension(self.AUTOROLES_EXTENSION)
        except commands.ExtensionNotLoaded:
            try:
                await self.bot.load_extension(self.AUTOROLES_EXTENSION)
            except commands.ExtensionError as exc:
                return f"Failed ({exc})"
            return "Loaded AutoRoles cog because it was not already loaded."
        except commands.ExtensionError as exc:
            return f"Failed ({exc})"

        return "Reloaded AutoRoles cog successfully."

    async def _send_verification_log_embed(
        self,
        *,
        interaction: discord.Interaction,
        previous_username: str,
        requested_username: str,
    ) -> bool:
        """Post a moderator review embed without applying any saved username or nickname changes yet."""

        if interaction.guild is None:
            return False

        request_channel_id = self.server_config_store.get_request_channel_id()
        if request_channel_id is None:
            return False

        channel = interaction.guild.get_channel(request_channel_id)
        if channel is None:
            channel = self.bot.get_channel(request_channel_id)
        if channel is None:
            return False

        admin_role_id = self.server_config_store.get_admin_role_id()

        embed = discord.Embed(
            title="Habbo Username Change Request",
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc),
            description=(
                "This request has not been applied yet."
                "The saved Habbo username and Discord nickname must only change after a Discord Admin presses Approve."
            ),
        )
        embed.add_field(name="Member", value=interaction.user.mention, inline=False)
        embed.add_field(name="Previous Username", value=previous_username, inline=True)
        embed.add_field(name="Requested Username", value=requested_username, inline=True)
        embed.add_field(name="Request Status", value="Pending admin review", inline=False)

        try:
            content = f"<@&{admin_role_id}>" if admin_role_id else None
            await channel.send(
                content=content,
                embed=embed,
                view=UsernameChangeRequestView(self, admin_role_id=admin_role_id),
            )
        except (discord.Forbidden, discord.HTTPException):
            return False

        return True


async def setup(bot: commands.Bot) -> None:
    """Discord extension entrypoint for loading this cog."""

    await bot.add_cog(UsernameChangeCog(bot))
