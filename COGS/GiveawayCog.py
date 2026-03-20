from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

LOGGER = logging.getLogger(__name__)
GIVEAWAY_CHANNEL_ID = 1479462940825489408
DEFAULT_STORAGE_PATH = Path(__file__).resolve().parent.parent / "data" / "giveaways.json"


@dataclass
class GiveawayRecord:
    """Persisted state for a single giveaway message."""

    message_id: int
    channel_id: int
    guild_id: int
    prize: str
    host_id: int
    end_time: str
    winner_count: int
    role_requirement_id: int | None = None
    minimum_account_age_days: int | None = None
    minimum_join_age_days: int | None = None
    entrants: list[int] = field(default_factory=list)
    ended: bool = False
    ended_at: str | None = None
    winner_ids: list[int] = field(default_factory=list)

    @property
    def end_datetime(self) -> datetime:
        return datetime.fromisoformat(self.end_time)

    @property
    def ended_at_datetime(self) -> datetime | None:
        return datetime.fromisoformat(self.ended_at) if self.ended_at else None


class GiveawayEnterButton(discord.ui.Button):
    """Persistent button that routes entry attempts back to the cog."""

    def __init__(self, cog: "GiveawayCog", message_id: int) -> None:
        super().__init__(
            label="Enter Giveaway",
            style=discord.ButtonStyle.success,
            custom_id=f"giveaway:enter:{message_id}",
        )
        self.cog = cog
        self.message_id = message_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_entry(interaction, self.message_id)


class GiveawayView(discord.ui.View):
    """Persistent view for a single giveaway message."""

    def __init__(self, cog: "GiveawayCog", message_id: int, *, disabled: bool = False) -> None:
        super().__init__(timeout=None)
        button = GiveawayEnterButton(cog, message_id)
        button.disabled = disabled
        self.add_item(button)


class GiveawayCog(commands.Cog):
    """Production-ready giveaway management using slash commands and JSON persistence."""

    giveaway = app_commands.Group(name="giveaway", description="Manage giveaway events.")

    def __init__(self, bot: commands.Bot, *, storage_path: Path | None = None) -> None:
        self.bot = bot
        self.storage_path = storage_path or DEFAULT_STORAGE_PATH
        self._giveaways: dict[int, GiveawayRecord] = {}
        self._end_tasks: dict[int, asyncio.Task[None]] = {}
        self._storage_lock = asyncio.Lock()
        self._restore_task: asyncio.Task[None] | None = None
        self._restored = asyncio.Event()

    async def cog_load(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._restore_task = asyncio.create_task(self._restore_giveaways())

    async def cog_unload(self) -> None:
        if self._restore_task is not None:
            self._restore_task.cancel()
        for task in self._end_tasks.values():
            task.cancel()
        self._end_tasks.clear()

    async def _restore_giveaways(self) -> None:
        try:
            records = await self._load_records_from_disk()
            self._giveaways = records
            for giveaway in self._giveaways.values():
                if giveaway.ended:
                    continue
                self.bot.add_view(GiveawayView(self, giveaway.message_id), message_id=giveaway.message_id)
                self._schedule_giveaway_end(giveaway.message_id)
        finally:
            self._restored.set()

    async def _load_records_from_disk(self) -> dict[int, GiveawayRecord]:
        if not self.storage_path.exists():
            return {}

        try:
            raw_data = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            LOGGER.exception("Giveaway storage JSON is corrupted: %s", self.storage_path)
            backup_path = self.storage_path.with_suffix(".corrupted.json")
            try:
                self.storage_path.replace(backup_path)
            except OSError:
                LOGGER.exception("Failed to back up corrupted giveaway JSON")
            return {}
        except OSError:
            LOGGER.exception("Failed to read giveaway storage file")
            return {}

        records: dict[int, GiveawayRecord] = {}
        if not isinstance(raw_data, list):
            LOGGER.error("Giveaway storage format is invalid; expected a list of records")
            return {}

        for entry in raw_data:
            if not isinstance(entry, dict):
                continue
            try:
                record = GiveawayRecord(**entry)
            except TypeError:
                LOGGER.exception("Skipping invalid giveaway record: %s", entry)
                continue
            records[record.message_id] = record
        return records

    async def _save_records(self) -> None:
        async with self._storage_lock:
            payload = [asdict(record) for record in self._giveaways.values()]
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            self.storage_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _schedule_giveaway_end(self, message_id: int) -> None:
        existing_task = self._end_tasks.get(message_id)
        if existing_task is not None:
            existing_task.cancel()
        self._end_tasks[message_id] = asyncio.create_task(self._end_when_due(message_id))

    async def _end_when_due(self, message_id: int) -> None:
        giveaway = self._giveaways.get(message_id)
        if giveaway is None or giveaway.ended:
            return

        delay = max((giveaway.end_datetime - self._utcnow()).total_seconds(), 0)
        try:
            await asyncio.sleep(delay)
            await self._finalize_giveaway(message_id, forced_by=None)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Unexpected error while ending giveaway %s", message_id)
        finally:
            self._end_tasks.pop(message_id, None)

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _format_timestamp(moment: datetime) -> str:
        unix = int(moment.timestamp())
        return f"<t:{unix}:F> (<t:{unix}:R>)"

    @staticmethod
    def _safe_get_member(guild: discord.Guild | None, user_id: int) -> discord.Member | None:
        if guild is None:
            return None
        return guild.get_member(user_id)

    @staticmethod
    def _normalize_positive_int(value: int, *, minimum: int = 1, field_name: str) -> int:
        if value < minimum:
            raise ValueError(f"{field_name} must be at least {minimum}.")
        return value

    def _build_requirements_text(self, giveaway: GiveawayRecord) -> str:
        parts: list[str] = []
        if giveaway.role_requirement_id:
            parts.append(f"Role: <@&{giveaway.role_requirement_id}>")
        if giveaway.minimum_account_age_days is not None:
            parts.append(f"Account age: {giveaway.minimum_account_age_days}+ day(s)")
        if giveaway.minimum_join_age_days is not None:
            parts.append(f"Server join age: {giveaway.minimum_join_age_days}+ day(s)")
        return "\n".join(parts) if parts else "No special requirements"

    def _build_giveaway_embed(self, giveaway: GiveawayRecord, *, ended: bool = False) -> discord.Embed:
        title_prefix = "🎉 Giveaway Ended" if ended or giveaway.ended else "🎉 Giveaway"
        embed = discord.Embed(
            title=title_prefix,
            color=discord.Color.blurple() if not giveaway.ended else discord.Color.dark_grey(),
            timestamp=self._utcnow(),
        )
        embed.add_field(name="Prize", value=giveaway.prize, inline=False)
        embed.add_field(name="Host", value=f"<@{giveaway.host_id}>", inline=True)
        embed.add_field(name="Winner Count", value=str(giveaway.winner_count), inline=True)
        embed.add_field(
            name="End Time" if not giveaway.ended else "Ended At",
            value=self._format_timestamp(giveaway.end_datetime if not giveaway.ended else giveaway.ended_at_datetime or giveaway.end_datetime),
            inline=False,
        )
        embed.add_field(name="Requirements", value=self._build_requirements_text(giveaway), inline=False)
        embed.add_field(name="Entry Count", value=str(len(giveaway.entrants)), inline=True)
        if giveaway.ended:
            winners = ", ".join(f"<@{winner_id}>" for winner_id in giveaway.winner_ids) or "No valid winners"
            embed.add_field(name="Winners", value=winners, inline=False)
        embed.set_footer(text=f"Message ID: {giveaway.message_id}")
        return embed

    def _build_response_embed(self, title: str, description: str, *, color: discord.Color | None = None) -> discord.Embed:
        return discord.Embed(title=title, description=description, color=color or discord.Color.blurple())

    async def _fetch_message(self, giveaway: GiveawayRecord) -> discord.Message | None:
        channel = self.bot.get_channel(giveaway.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(giveaway.channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                LOGGER.warning("Giveaway channel %s is unavailable", giveaway.channel_id)
                return None

        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return None

        try:
            return await channel.fetch_message(giveaway.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            LOGGER.warning("Giveaway message %s is unavailable", giveaway.message_id)
            return None

    def _eligible_for_entry(
        self,
        member: discord.Member,
        giveaway: GiveawayRecord,
    ) -> tuple[bool, str | None]:
        if giveaway.role_requirement_id and giveaway.role_requirement_id not in {role.id for role in member.roles}:
            return False, "You do not have the required role for this giveaway."

        if giveaway.minimum_account_age_days is not None:
            account_age = self._utcnow() - member.created_at.astimezone(timezone.utc)
            if account_age < timedelta(days=giveaway.minimum_account_age_days):
                return False, "Your Discord account is too new for this giveaway."

        if giveaway.minimum_join_age_days is not None:
            if member.joined_at is None:
                return False, "I could not verify when you joined this server."
            join_age = self._utcnow() - member.joined_at.astimezone(timezone.utc)
            if join_age < timedelta(days=giveaway.minimum_join_age_days):
                return False, "You have not been in this server long enough for this giveaway."

        return True, None

    async def handle_entry(self, interaction: discord.Interaction, message_id: int) -> None:
        await self._restored.wait()
        giveaway = self._giveaways.get(message_id)
        if giveaway is None:
            await interaction.response.send_message(
                embed=self._build_response_embed("Giveaway Not Found", "That giveaway is no longer active."),
                ephemeral=True,
            )
            return

        if giveaway.ended:
            await interaction.response.send_message(
                embed=self._build_response_embed("Giveaway Ended", "This giveaway has already ended."),
                ephemeral=True,
            )
            return

        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                embed=self._build_response_embed("Server Only", "You can only enter giveaways from the server."),
                ephemeral=True,
            )
            return

        if interaction.user.id in giveaway.entrants:
            await interaction.response.send_message(
                embed=self._build_response_embed("Already Entered", "You are already entered in this giveaway."),
                ephemeral=True,
            )
            return

        eligible, reason = self._eligible_for_entry(interaction.user, giveaway)
        if not eligible:
            await interaction.response.send_message(
                embed=self._build_response_embed("Entry Rejected", reason or "You do not meet the requirements."),
                ephemeral=True,
            )
            return

        giveaway.entrants.append(interaction.user.id)
        await self._save_records()

        message = await self._fetch_message(giveaway)
        if message is not None:
            try:
                await message.edit(embed=self._build_giveaway_embed(giveaway), view=GiveawayView(self, giveaway.message_id))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                LOGGER.warning("Failed to refresh giveaway message %s after entry", giveaway.message_id)

        await interaction.response.send_message(
            embed=self._build_response_embed("Entry Confirmed", f"You have entered the giveaway for **{giveaway.prize}**."),
            ephemeral=True,
        )

    def _pick_winners(self, giveaway: GiveawayRecord, *, guild: discord.Guild | None) -> list[int]:
        valid_entrant_ids: list[int] = []
        for entrant_id in giveaway.entrants:
            member = self._safe_get_member(guild, entrant_id)
            if member is None:
                continue
            eligible, _ = self._eligible_for_entry(member, giveaway)
            if eligible:
                valid_entrant_ids.append(entrant_id)

        if not valid_entrant_ids:
            return []

        winner_total = min(giveaway.winner_count, len(valid_entrant_ids))
        return random.sample(valid_entrant_ids, k=winner_total)

    async def _finalize_giveaway(self, message_id: int, *, forced_by: discord.abc.User | None) -> tuple[bool, str]:
        giveaway = self._giveaways.get(message_id)
        if giveaway is None:
            return False, "Giveaway not found."
        if giveaway.ended:
            return False, "That giveaway has already ended."

        guild = self.bot.get_guild(giveaway.guild_id)
        winners = self._pick_winners(giveaway, guild=guild)
        giveaway.ended = True
        giveaway.ended_at = self._utcnow().isoformat()
        giveaway.winner_ids = winners
        await self._save_records()

        message = await self._fetch_message(giveaway)
        if message is not None:
            try:
                await message.edit(embed=self._build_giveaway_embed(giveaway, ended=True), view=GiveawayView(self, giveaway.message_id, disabled=True))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                LOGGER.warning("Failed to edit ended giveaway message %s", giveaway.message_id)

            announcement = self._build_end_announcement(giveaway, forced_by=forced_by)
            try:
                await message.channel.send(embed=announcement)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                LOGGER.warning("Failed to announce giveaway winners for %s", giveaway.message_id)

        self._end_tasks.pop(message_id, None)
        if winners:
            return True, f"Giveaway ended. Winner(s): {', '.join(f'<@{winner_id}>' for winner_id in winners)}"
        return True, "Giveaway ended, but there were not enough valid entrants to select any winners."

    def _build_end_announcement(self, giveaway: GiveawayRecord, *, forced_by: discord.abc.User | None) -> discord.Embed:
        description = (
            f"The giveaway for **{giveaway.prize}** has ended.\n"
            f"Host: <@{giveaway.host_id}>"
        )
        if forced_by is not None:
            description += f"\nEnded early by: {forced_by.mention}"

        if giveaway.winner_ids:
            description += "\n\n**Winner(s):** " + ", ".join(f"<@{winner_id}>" for winner_id in giveaway.winner_ids)
        else:
            description += "\n\nNo valid winners could be selected."

        return discord.Embed(title="🎉 Giveaway Results", description=description, color=discord.Color.gold())

    def _has_manage_permissions(self, interaction: discord.Interaction) -> bool:
        permissions = getattr(interaction.user, "guild_permissions", None)
        if permissions is None:
            return False
        return bool(permissions.manage_guild or permissions.manage_messages)

    async def _ensure_management_access(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=self._build_response_embed("Server Only", "This command can only be used in a server."),
                ephemeral=True,
            )
            return False
        if not self._has_manage_permissions(interaction):
            await interaction.response.send_message(
                embed=self._build_response_embed(
                    "Missing Permissions",
                    "You need **Manage Server** or **Manage Messages** to manage giveaways.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return False
        return True

    @giveaway.command(name="start", description="Start a new giveaway in the configured giveaway channel.")
    @app_commands.describe(
        prize="Prize shown on the giveaway embed",
        duration_minutes="How long the giveaway should run in minutes",
        winner_count="How many winners should be selected",
        role_requirement="Optional role required to enter",
        minimum_account_age_days="Optional minimum Discord account age in days",
        minimum_server_join_age_days="Optional minimum time in the server in days",
    )
    async def giveaway_start(
        self,
        interaction: discord.Interaction,
        prize: str,
        duration_minutes: int,
        winner_count: int,
        role_requirement: discord.Role | None = None,
        minimum_account_age_days: app_commands.Range[int, 0, 3650] | None = None,
        minimum_server_join_age_days: app_commands.Range[int, 0, 3650] | None = None,
    ) -> None:
        await self._restored.wait()
        if not await self._ensure_management_access(interaction):
            return

        try:
            normalized_duration = self._normalize_positive_int(duration_minutes, field_name="Duration")
            normalized_winner_count = self._normalize_positive_int(winner_count, field_name="Winner count")
        except ValueError as exc:
            await interaction.response.send_message(
                embed=self._build_response_embed("Invalid Giveaway Settings", str(exc), color=discord.Color.red()),
                ephemeral=True,
            )
            return

        if interaction.guild is None:
            return

        channel = interaction.guild.get_channel(GIVEAWAY_CHANNEL_ID)
        if channel is None:
            try:
                fetched_channel = await self.bot.fetch_channel(GIVEAWAY_CHANNEL_ID)
                channel = fetched_channel if isinstance(fetched_channel, discord.TextChannel) else None
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                channel = None

        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=self._build_response_embed(
                    "Channel Missing",
                    f"I could not access the configured giveaway channel (`{GIVEAWAY_CHANNEL_ID}`).",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        provisional_end = self._utcnow() + timedelta(minutes=normalized_duration)
        placeholder = GiveawayRecord(
            message_id=0,
            channel_id=channel.id,
            guild_id=interaction.guild.id,
            prize=prize,
            host_id=interaction.user.id,
            end_time=provisional_end.isoformat(),
            winner_count=normalized_winner_count,
            role_requirement_id=role_requirement.id if role_requirement else None,
            minimum_account_age_days=minimum_account_age_days,
            minimum_join_age_days=minimum_server_join_age_days,
        )

        try:
            sent_message = await channel.send(embed=self._build_giveaway_embed(placeholder), view=GiveawayView(self, 0))
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                embed=self._build_response_embed(
                    "Send Failed",
                    "I could not post the giveaway message in the configured channel.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        giveaway = GiveawayRecord(
            message_id=sent_message.id,
            channel_id=channel.id,
            guild_id=interaction.guild.id,
            prize=prize,
            host_id=interaction.user.id,
            end_time=provisional_end.isoformat(),
            winner_count=normalized_winner_count,
            role_requirement_id=role_requirement.id if role_requirement else None,
            minimum_account_age_days=minimum_account_age_days,
            minimum_join_age_days=minimum_server_join_age_days,
        )
        self._giveaways[giveaway.message_id] = giveaway
        await self._save_records()
        self.bot.add_view(GiveawayView(self, giveaway.message_id), message_id=giveaway.message_id)

        try:
            await sent_message.edit(embed=self._build_giveaway_embed(giveaway), view=GiveawayView(self, giveaway.message_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            LOGGER.warning("Failed to finalize giveaway message view for %s", giveaway.message_id)

        self._schedule_giveaway_end(giveaway.message_id)

        await interaction.response.send_message(
            embed=self._build_response_embed(
                "Giveaway Started",
                f"Started **{prize}** in {channel.mention}. Message ID: `{giveaway.message_id}`.",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

    @giveaway.command(name="end", description="End an active giveaway immediately.")
    @app_commands.describe(message_id="The giveaway message ID to end")
    async def giveaway_end(self, interaction: discord.Interaction, message_id: str) -> None:
        await self._restored.wait()
        if not await self._ensure_management_access(interaction):
            return

        try:
            resolved_message_id = int(message_id)
        except ValueError:
            await interaction.response.send_message(
                embed=self._build_response_embed("Invalid Message ID", "Please provide a valid numeric giveaway message ID.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        success, details = await self._finalize_giveaway(resolved_message_id, forced_by=interaction.user)
        await interaction.response.send_message(
            embed=self._build_response_embed(
                "Giveaway Ended" if success else "Unable to End Giveaway",
                details,
                color=discord.Color.green() if success else discord.Color.red(),
            ),
            ephemeral=True,
        )

    @giveaway.command(name="reroll", description="Reroll a completed giveaway from the saved entrants.")
    @app_commands.describe(message_id="The giveaway message ID to reroll", winner_count="Optional new winner count")
    async def giveaway_reroll(
        self,
        interaction: discord.Interaction,
        message_id: str,
        winner_count: app_commands.Range[int, 1, 50] | None = None,
    ) -> None:
        await self._restored.wait()
        if not await self._ensure_management_access(interaction):
            return

        try:
            resolved_message_id = int(message_id)
        except ValueError:
            await interaction.response.send_message(
                embed=self._build_response_embed("Invalid Message ID", "Please provide a valid numeric giveaway message ID.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        giveaway = self._giveaways.get(resolved_message_id)
        if giveaway is None:
            await interaction.response.send_message(
                embed=self._build_response_embed("Giveaway Not Found", "I could not find a saved giveaway with that message ID.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        if not giveaway.ended:
            await interaction.response.send_message(
                embed=self._build_response_embed("Giveaway Still Active", "You can only reroll a giveaway after it has ended.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        if winner_count is not None:
            giveaway.winner_count = winner_count

        guild = self.bot.get_guild(giveaway.guild_id)
        winners = self._pick_winners(giveaway, guild=guild)
        giveaway.winner_ids = winners
        giveaway.ended_at = self._utcnow().isoformat()
        await self._save_records()

        message = await self._fetch_message(giveaway)
        if message is not None:
            try:
                await message.edit(embed=self._build_giveaway_embed(giveaway, ended=True), view=GiveawayView(self, giveaway.message_id, disabled=True))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                LOGGER.warning("Failed to update giveaway message during reroll %s", giveaway.message_id)
            announcement = discord.Embed(
                title="🔁 Giveaway Rerolled",
                description=(
                    f"New winner(s) for **{giveaway.prize}**: "
                    + (", ".join(f"<@{winner_id}>" for winner_id in winners) if winners else "No valid winners")
                ),
                color=discord.Color.orange(),
            )
            try:
                await message.channel.send(embed=announcement)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                LOGGER.warning("Failed to announce reroll results for %s", giveaway.message_id)

        await interaction.response.send_message(
            embed=self._build_response_embed(
                "Giveaway Rerolled",
                (
                    f"New winner(s): {', '.join(f'<@{winner_id}>' for winner_id in winners)}"
                    if winners
                    else "No valid winners were available for the reroll."
                ),
                color=discord.Color.green() if winners else discord.Color.orange(),
            ),
            ephemeral=True,
        )

    @giveaway.command(name="list", description="List all active giveaways.")
    async def giveaway_list(self, interaction: discord.Interaction) -> None:
        await self._restored.wait()
        if not await self._ensure_management_access(interaction):
            return

        active_giveaways = [record for record in self._giveaways.values() if not record.ended]
        if not active_giveaways:
            await interaction.response.send_message(
                embed=self._build_response_embed("Active Giveaways", "There are no active giveaways right now."),
                ephemeral=True,
            )
            return

        embed = discord.Embed(title="Active Giveaways", color=discord.Color.blurple())
        for record in sorted(active_giveaways, key=lambda current: current.end_datetime):
            embed.add_field(
                name=record.prize,
                value=(
                    f"Message ID: `{record.message_id}`\n"
                    f"Channel: <#{record.channel_id}>\n"
                    f"Ends: {self._format_timestamp(record.end_datetime)}"
                ),
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GiveawayCog(bot))
