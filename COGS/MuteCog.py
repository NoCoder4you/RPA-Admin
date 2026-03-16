from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re

import discord
from discord import app_commands
from discord.ext import commands


_DURATION_PATTERN = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.IGNORECASE)
_MAX_TIMEOUT = timedelta(days=28)
_MUTE_LOG_PATH = Path(__file__).resolve().parent.parent / "mute_timeouts.json"


class MuteCog(commands.Cog):
    """Moderation cog containing a staff-only mute/timeout command."""

    def __init__(self, bot: commands.Bot) -> None:
        # Keep a bot reference for consistency with the project's other cogs.
        self.bot = bot
        # Persist timeout metadata so moderation actions are auditable outside Discord.
        self.mute_log_path = _MUTE_LOG_PATH

    def _append_mute_record(
        self,
        *,
        guild_id: int,
        member_id: int,
        moderator_id: int,
        reason: str,
        requested_length: str,
        duration_seconds: int,
        started_at: datetime,
        ends_at: datetime,
    ) -> None:
        """Append a timeout record to JSON storage, creating the file if required."""

        # Keep timestamps in ISO 8601 UTC format for machine parsing and readability.
        record = {
            "guild_id": str(guild_id),
            "member_id": str(member_id),
            "moderator_id": str(moderator_id),
            "reason": reason,
            "requested_length": requested_length,
            "duration_seconds": duration_seconds,
            "start_time": started_at.isoformat(),
            "end_time": ends_at.isoformat(),
        }

        self.mute_log_path.parent.mkdir(parents=True, exist_ok=True)

        records: list[dict[str, str | int]] = []
        if self.mute_log_path.exists():
            try:
                with self.mute_log_path.open("r", encoding="utf-8") as existing_file:
                    existing_data = json.load(existing_file)
                if isinstance(existing_data, list):
                    records = existing_data
            except (json.JSONDecodeError, OSError):
                # If the file is malformed or inaccessible, reset to a fresh list.
                records = []

        records.append(record)

        with self.mute_log_path.open("w", encoding="utf-8") as output_file:
            json.dump(records, output_file, indent=2)

    @staticmethod
    def _parse_timeout_length(lengthoftime: str) -> timedelta | None:
        """Parse timeout text like `10m`, `2h`, `7d`, or `1w` into a timedelta."""

        match = _DURATION_PATTERN.match(lengthoftime)
        if not match:
            return None

        amount = int(match.group(1))
        unit = match.group(2).lower()

        # Translate compact user input into a specific moderation duration.
        multiplier_seconds = {
            "s": 1,
            "m": 60,
            "h": 60 * 60,
            "d": 60 * 60 * 24,
            "w": 60 * 60 * 24 * 7,
        }
        return timedelta(seconds=amount * multiplier_seconds[unit])

    @app_commands.command(name="mute", description="Temporarily mute (timeout) a member with a reason.")
    @app_commands.describe(
        mention="The member to mute",
        lengthoftime="How long to mute them (examples: 10m, 2h, 3d, 1w)",
        reason="Why this member is being muted",
    )
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    async def mute(
        self,
        interaction: discord.Interaction,
        mention: discord.Member,
        lengthoftime: str,
        reason: str,
    ) -> None:
        """Timeout a guild member for a parsed duration when permission checks pass."""

        # This command only makes sense where guild member moderation is possible.
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        duration = self._parse_timeout_length(lengthoftime)
        if duration is None:
            await interaction.response.send_message(
                "Invalid `lengthoftime`. Use formats like `10m`, `2h`, `3d`, or `1w`.",
                ephemeral=True,
            )
            return

        if duration <= timedelta(seconds=0):
            await interaction.response.send_message("Mute duration must be greater than zero.", ephemeral=True)
            return

        # Discord only allows timeouts up to 28 days; fail early with guidance.
        if duration > _MAX_TIMEOUT:
            await interaction.response.send_message(
                "Mute duration cannot exceed 28 days.",
                ephemeral=True,
            )
            return

        if mention.id == interaction.user.id:
            await interaction.response.send_message("You cannot mute yourself.", ephemeral=True)
            return

        if mention.id == interaction.guild.owner_id:
            await interaction.response.send_message("I cannot mute the server owner.", ephemeral=True)
            return

        # Enforce role hierarchy to avoid attempting invalid moderation actions.
        if hasattr(interaction.user, "top_role") and mention.top_role >= interaction.user.top_role:
            await interaction.response.send_message(
                "You can only mute members with a lower top role than yours.",
                ephemeral=True,
            )
            return

        bot_member = interaction.guild.me
        if bot_member is not None and mention.top_role >= bot_member.top_role:
            await interaction.response.send_message(
                "I cannot mute that member because their top role is higher than or equal to mine.",
                ephemeral=True,
            )
            return

        timeout_started_at = datetime.now(timezone.utc)
        timeout_until = timeout_started_at + duration

        try:
            await mention.timeout(timeout_until, reason=f"{interaction.user} - {reason}")
        except discord.Forbidden:
            await interaction.response.send_message(
                "Mute failed: I do not have permission to mute that member.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.response.send_message(
                "Mute failed due to a Discord API error. Please try again.",
                ephemeral=True,
            )
            return

        self._append_mute_record(
            guild_id=interaction.guild.id,
            member_id=mention.id,
            moderator_id=interaction.user.id,
            reason=reason,
            requested_length=lengthoftime,
            duration_seconds=int(duration.total_seconds()),
            started_at=timeout_started_at,
            ends_at=timeout_until,
        )

        await interaction.response.send_message(
            f"🔇 Muted {mention.mention} for `{lengthoftime}`. Reason: {reason}",
            ephemeral=True,
        )

    @mute.error
    async def mute_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        """Return clear permission guidance for known slash-command check failures."""

        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the **Moderate Members** permission to use `/mute`.",
                ephemeral=True,
            )
            return

        if isinstance(error, app_commands.BotMissingPermissions):
            await interaction.response.send_message(
                "I need the **Moderate Members** permission to use `/mute`.",
                ephemeral=True,
            )
            return

        raise error


async def setup(bot: commands.Bot) -> None:
    """Discord extension entrypoint for loading this cog."""

    await bot.add_cog(MuteCog(bot))
