from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import discord
from discord import app_commands
from discord.ext import commands
from habbo_verification_core import HabboApiError, VerifiedUserStore, fetch_habbo_profile

LOGGER = logging.getLogger(__name__)
DEFAULT_STORAGE_PATH = Path(__file__).resolve().parent.parent / "JSON" / "raffles.json"
MAX_ENTRIES_DISPLAY = 20
RAFFLE_LOG_CHANNEL_ID = 1485484040055427132


class RaffleCog(commands.Cog):
    """Slash-command-only raffle management with persistent JSON storage."""

    raffle = app_commands.Group(name="raffle", description="Manage server raffles.")

    def __init__(self, bot: commands.Bot, *, storage_path: Path | None = None) -> None:
        self.bot = bot
        self.storage_path = storage_path or DEFAULT_STORAGE_PATH
        self._storage_lock = asyncio.Lock()
        self._raffles: dict[str, dict[str, Any]] = {}
        self.verified_store = VerifiedUserStore()

    async def cog_load(self) -> None:
        """Load persisted raffle data when the cog is added to the bot."""
        await self._load_raffles()

    def _utcnow(self) -> datetime:
        return datetime.now(timezone.utc)

    def _generate_raffle_id(self) -> str:
        """Generate a compact raffle identifier suitable for staff slash commands."""
        while True:
            raffle_id = uuid4().hex[:8].upper()
            if raffle_id not in self._raffles:
                return raffle_id

    def _default_payload(self) -> dict[str, Any]:
        return {"raffles": {}}

    def _ensure_storage_file(self) -> None:
        """Create the JSON storage path automatically when it does not exist."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.storage_path.exists():
            self.storage_path.write_text(json.dumps(self._default_payload(), indent=2), encoding="utf-8")

    async def _load_raffles(self) -> None:
        """Safely load raffle data from disk, falling back to an empty structure if invalid."""
        async with self._storage_lock:
            self._ensure_storage_file()
            try:
                payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                LOGGER.exception("Raffle storage JSON is invalid: %s", self.storage_path)
                backup_path = self.storage_path.with_suffix(".corrupted.json")
                try:
                    self.storage_path.replace(backup_path)
                except OSError:
                    LOGGER.exception("Failed to back up corrupted raffle storage")
                self._ensure_storage_file()
                self._raffles = {}
                return
            except OSError:
                LOGGER.exception("Failed to read raffle storage file: %s", self.storage_path)
                self._raffles = {}
                return

        if not isinstance(payload, dict) or not isinstance(payload.get("raffles", {}), dict):
            LOGGER.error("Raffle storage format is invalid; resetting to an empty structure")
            self._raffles = {}
            await self._save_raffles()
            return

        cleaned: dict[str, dict[str, Any]] = {}
        for raffle_id, raffle_data in payload.get("raffles", {}).items():
            if not isinstance(raffle_id, str) or not isinstance(raffle_data, dict):
                continue
            normalized = self._normalize_raffle_payload(raffle_id, raffle_data)
            if normalized is not None:
                cleaned[raffle_id] = normalized
        self._raffles = cleaned
        await self._save_raffles()

    async def _save_raffles(self) -> None:
        """Persist raffle data to disk using atomic replacement to prevent corruption."""
        async with self._storage_lock:
            self._ensure_storage_file()
            payload = {"raffles": self._raffles}
            temp_path = self.storage_path.with_suffix(".tmp")
            temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            temp_path.replace(self.storage_path)

    def _normalize_raffle_payload(self, raffle_id: str, raffle_data: dict[str, Any]) -> dict[str, Any] | None:
        """Validate persisted raffle data before it is exposed to commands."""
        required_keys = {
            "raffle_id": str,
            "name": str,
            "description": (str, type(None)),
            "guild_id": int,
            "channel_id": int,
            "created_by": int,
            "created_at": str,
            "active": bool,
            "allow_multiple_entries": bool,
            "entrants": dict,
            "winners": list,
            "log_channel_id": int,
            "log_message_id": (int, type(None)),
        }
        for key, expected_type in required_keys.items():
            value = raffle_data.get(key)
            if not isinstance(value, expected_type):
                return None

        entrants: dict[str, dict[str, Any]] = {}
        for user_id, entrant_data in raffle_data["entrants"].items():
            if not isinstance(user_id, str) or not isinstance(entrant_data, dict):
                continue
            username = entrant_data.get("username")
            entry_count = entrant_data.get("entries")
            if not isinstance(username, str) or not isinstance(entry_count, int) or entry_count < 1:
                continue
            entrants[user_id] = {"username": username, "entries": entry_count}

        winners = [winner for winner in raffle_data["winners"] if isinstance(winner, int)]
        normalized = dict(raffle_data)
        normalized["raffle_id"] = raffle_id
        normalized["entrants"] = entrants
        normalized["winners"] = winners
        return normalized

    async def _fetch_log_channel(self, channel_id: int = RAFFLE_LOG_CHANNEL_ID) -> discord.TextChannel | None:
        """Resolve the configured raffle log channel used for public raffle announcements."""
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                LOGGER.exception("Failed to fetch raffle log channel %s", channel_id)
                return None
        if isinstance(channel, discord.TextChannel):
            return channel
        LOGGER.error("Configured raffle log channel %s is not a text channel", channel_id)
        return None

    def _build_creation_log_embed(
        self,
        raffle: dict[str, Any],
        *,
        created_by: discord.abc.User,
        source_channel_mention: str,
    ) -> discord.Embed:
        """Build the public embed mirrored into the configured raffle log channel."""
        embed = self._build_embed(
            "Raffle Created",
            f"Created raffle **{raffle['name']}** with ID `{raffle['raffle_id']}`.",
            color=discord.Color.green(),
        )
        embed.add_field(name="Description", value=raffle["description"] or "No description provided.", inline=False)
        embed.add_field(name="Multiple Entries", value="Enabled" if raffle["allow_multiple_entries"] else "Disabled", inline=True)
        embed.add_field(name="Created In", value=source_channel_mention, inline=True)
        embed.add_field(name="Created By", value=created_by.mention, inline=True)
        return embed

    async def _send_creation_log_embed(
        self,
        raffle: dict[str, Any],
        *,
        created_by: discord.abc.User,
        source_channel_mention: str,
    ) -> int | None:
        """Mirror raffle creation details into the configured raffle log channel and return the message ID."""
        log_channel = await self._fetch_log_channel(raffle.get("log_channel_id", RAFFLE_LOG_CHANNEL_ID))
        if log_channel is None:
            return None

        embed = self._build_creation_log_embed(
            raffle,
            created_by=created_by,
            source_channel_mention=source_channel_mention,
        )
        try:
            message = await log_channel.send(embed=embed)
        except discord.HTTPException:
            LOGGER.exception("Failed to send raffle creation embed for raffle %s", raffle["raffle_id"])
            return None
        return message.id

    @staticmethod
    def _is_same_channel_as_log(interaction: discord.Interaction, channel_id: int) -> bool:
        """Return True when the interaction is already happening inside the raffle log channel."""
        return getattr(getattr(interaction, "channel", None), "id", None) == channel_id

    async def _mirror_embed_to_log_channel(self, embed: discord.Embed, *, channel_id: int = RAFFLE_LOG_CHANNEL_ID) -> bool:
        """Send a copy of a raffle embed to the configured raffle channel without interrupting the command flow."""
        log_channel = await self._fetch_log_channel(channel_id)
        if log_channel is None:
            return False
        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException:
            LOGGER.exception("Failed to mirror raffle embed to channel %s", channel_id)
            return False
        return True

    async def _respond_and_log(
        self,
        interaction: discord.Interaction,
        *,
        embed: discord.Embed,
        ephemeral: bool = True,
        channel_id: int = RAFFLE_LOG_CHANNEL_ID,
        public_response: bool = False,
        mirror_to_log: bool = False,
    ) -> None:
        """Reply to staff and optionally mirror raffle-specific embeds into the raffle log channel."""
        # Keep staff-only validation or permission errors out of the dedicated raffle
        # announcement channel. Only explicit raffle updates should be mirrored there.
        await self._respond(interaction, embed=embed, ephemeral=False if public_response else ephemeral)
        if not mirror_to_log:
            return

        # Staff asked for raffle activity updates to appear as a normal public message
        # instead of an ephemeral response. When the current channel is already the
        # configured raffle log channel, skip the mirror entirely so the same embed
        # is not posted twice in the same place.
        if public_response and getattr(getattr(interaction, "channel", None), "id", None) == channel_id:
            return
        await self._mirror_embed_to_log_channel(embed, channel_id=channel_id)

    def _has_manage_permissions(self, interaction: discord.Interaction) -> bool:
        perms = getattr(interaction.user, "guild_permissions", None)
        return bool(perms and (perms.manage_guild or perms.administrator))

    async def _check_permissions(self, interaction: discord.Interaction) -> bool:
        if self._has_manage_permissions(interaction):
            return True

        embed = self._build_embed(
            title="Missing Permissions",
            description="You need **Manage Server** or **Administrator** permissions to manage raffles.",
            color=discord.Color.red(),
        )
        await self._respond_and_log(interaction, embed=embed, ephemeral=True)
        return False

    async def _respond(self, interaction: discord.Interaction, *, embed: discord.Embed, ephemeral: bool = True) -> None:
        """Send a response whether the interaction has already been acknowledged or not."""
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    def _build_embed(self, title: str, description: str, *, color: discord.Color | None = None) -> discord.Embed:
        return discord.Embed(title=title, description=description, color=color or discord.Color.blurple(), timestamp=self._utcnow())

    @staticmethod
    def _build_avatar_thumbnail_url(profile: dict[str, Any]) -> str | None:
        """Build a Habbo avatar thumbnail URL when a figure string is available."""
        figure_string = str(profile.get("figureString", "")).strip()
        if not figure_string:
            return None

        from urllib.parse import quote

        encoded_figure = quote(figure_string, safe="")
        # Request the standard full-body render so the raffle thumbnails show the
        # member's Habbo character instead of falling back to a tighter head crop.
        return (
            "https://www.habbo.com/habbo-imaging/avatarimage"
            f"?figure={encoded_figure}&size=l&direction=2&head_direction=3&gesture=sml&action=std"
        )

    def _get_habbo_thumbnail_url(self, discord_user_id: int) -> str | None:
        """Resolve a verified member's Habbo avatar thumbnail for embed branding when possible."""
        habbo_username = self.verified_store.get_habbo_username(str(discord_user_id))
        if not habbo_username:
            return None

        try:
            profile = fetch_habbo_profile(habbo_username)
        except HabboApiError:
            LOGGER.exception("Failed to fetch Habbo profile for raffle embed thumbnail: %s", habbo_username)
            return None
        return self._build_avatar_thumbnail_url(profile)

    def _get_guild_raffle(self, interaction: discord.Interaction, raffle_id: str) -> dict[str, Any] | None:
        raffle = self._raffles.get(raffle_id.upper())
        if raffle is None or interaction.guild is None:
            return None
        if raffle["guild_id"] != interaction.guild.id:
            return None
        return raffle

    def _total_entries(self, raffle: dict[str, Any]) -> int:
        return sum(entrant["entries"] for entrant in raffle["entrants"].values())

    def _build_raffle_list_value(self, raffle: dict[str, Any]) -> str:
        """Format a raffle summary so the ID and raffle name each appear in their own subheading-style block."""
        # Discord embeds do not support true heading levels inside field values, so
        # we use explicit labeled sections to create the visual hierarchy staff asked
        # for while keeping the remaining raffle stats easy to scan underneath.
        return (
            "**ID**\n"
            f"`{raffle['raffle_id']}`\n"
            "**Raffle Name**\n"
            f"{raffle['name']}\n"
            "**Details**\n"
            f"Entries: **{self._total_entries(raffle)}**\n"
            f"Unique Users: **{len(raffle['entrants'])}**\n"
            f"Multiple Entries: **{'Enabled' if raffle['allow_multiple_entries'] else 'Disabled'}**"
        )

    def _build_weighted_pool(self, raffle: dict[str, Any]) -> list[int]:
        weighted_entries: list[int] = []
        for user_id, entrant in raffle["entrants"].items():
            weighted_entries.extend([int(user_id)] * entrant["entries"])
        return weighted_entries

    def _pick_unique_weighted_winners(self, raffle: dict[str, Any], winner_count: int) -> list[int]:
        """Draw unique winners while preserving weighted odds from entry counts."""
        remaining_entries = {
            int(user_id): entrant["entries"] for user_id, entrant in raffle["entrants"].items() if entrant["entries"] > 0
        }
        winners: list[int] = []

        for _ in range(winner_count):
            pool: list[int] = []
            for user_id, entries in remaining_entries.items():
                pool.extend([user_id] * entries)
            if not pool:
                break
            winner = random.choice(pool)
            winners.append(winner)
            remaining_entries.pop(winner, None)
        return winners

    async def _send_entry_dm(
        self,
        member: discord.Member,
        *,
        raffle_name: str,
        guild_name: str,
        added_by: discord.abc.User,
        entry_count: int,
    ) -> bool:
        """Notify a player that staff entered them into a raffle, even if DMs are disabled."""
        embed = discord.Embed(
            title="Raffle Entry Confirmed",
            description=f"You have been entered into the raffle in **{guild_name}**.",
            color=discord.Color.green(),
            timestamp=self._utcnow(),
        )
        embed.add_field(name="Raffle Name", value=raffle_name, inline=False)
        embed.add_field(name="Total Entries", value=str(entry_count), inline=False)
        embed.add_field(name="Added By", value=added_by.mention, inline=False)
        thumbnail_url = self._get_habbo_thumbnail_url(member.id)
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        try:
            await member.send(embed=embed)
            return True
        except discord.Forbidden:
            return False
        except discord.HTTPException:
            LOGGER.exception("Failed to DM raffle entry confirmation to %s", member.id)
            return False

    def _build_winner_embed(
        self,
        member: discord.abc.User,
        *,
        raffle: dict[str, Any],
        guild_name: str,
        placement: int,
        total_winners: int,
    ) -> discord.Embed:
        """Build the winner announcement embed so the same card can be reused in DMs and the raffle channel."""
        embed = discord.Embed(
            title="Raffle Winner",
            description=f"You won **{raffle['name']}** in **{guild_name}**.",
            color=discord.Color.gold(),
            timestamp=self._utcnow(),
        )
        embed.add_field(name="Raffle ID", value=raffle["raffle_id"], inline=True)
        embed.add_field(name="Placement", value=f"{placement} of {total_winners}", inline=True)
        embed.add_field(name="Total Entries", value=str(self._total_entries(raffle)), inline=True)
        thumbnail_url = self._get_habbo_thumbnail_url(member.id)
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        return embed

    async def _send_winner_dm(
        self,
        member: discord.abc.User,
        *,
        raffle: dict[str, Any],
        guild_name: str,
        placement: int,
        total_winners: int,
    ) -> bool:
        """DM each winner their own result embed so multiple winners are clearly separated."""
        embed = self._build_winner_embed(
            member,
            raffle=raffle,
            guild_name=guild_name,
            placement=placement,
            total_winners=total_winners,
        )
        try:
            await member.send(embed=embed)
            return True
        except discord.Forbidden:
            return False
        except discord.HTTPException:
            LOGGER.exception("Failed to DM raffle winner confirmation to %s", member.id)
            return False

    @raffle.command(name="create", description="Create a new raffle in this server.")
    @app_commands.describe(
        name="Name of the raffle.",
        description="Optional description for the raffle.",
        allow_multiple_entries="Whether the raffle allows more than one entry per user.",
    )
    async def raffle_create(
        self,
        interaction: discord.Interaction,
        name: str,
        allow_multiple_entries: bool,
        description: str | None = None,
    ) -> None:
        if not await self._check_permissions(interaction):
            return
        if interaction.guild is None or interaction.channel is None:
            await self._respond(
                interaction,
                embed=self._build_embed("Server Only", "Raffles can only be created inside a server.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        clean_name = name.strip()
        clean_description = description.strip() if description else None
        if not clean_name:
            await self._respond(
                interaction,
                embed=self._build_embed("Invalid Name", "Raffle name cannot be empty.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        raffle_id = self._generate_raffle_id()
        raffle = {
            "raffle_id": raffle_id,
            "name": clean_name,
            "description": clean_description,
            "guild_id": interaction.guild.id,
            "channel_id": interaction.channel.id,
            "created_by": interaction.user.id,
            "created_at": self._utcnow().isoformat(),
            "active": True,
            "allow_multiple_entries": allow_multiple_entries,
            "entrants": {},
            "winners": [],
            "log_channel_id": RAFFLE_LOG_CHANNEL_ID,
            "log_message_id": None,
        }
        self._raffles[raffle_id] = raffle
        # When staff create the raffle directly inside the configured raffle log
        # channel, the public interaction reply already serves as the visible log
        # message. Skip the pre-send log copy so the channel only receives one embed.
        if self._is_same_channel_as_log(interaction, raffle["log_channel_id"]):
            raffle["log_message_id"] = None
        else:
            raffle["log_message_id"] = await self._send_creation_log_embed(
                raffle,
                created_by=interaction.user,
                source_channel_mention=interaction.channel.mention,
            )
        await self._save_raffles()

        embed = self._build_creation_log_embed(
            raffle,
            created_by=interaction.user,
            source_channel_mention=interaction.channel.mention,
        )
        # Only show the configured log channel when the mirror embed was delivered.
        # This keeps the confirmation embed focused on raffle details without exposing
        # an extra status block when the log mirror is unavailable.
        if raffle["log_message_id"] is not None:
            embed.add_field(name="Log Channel", value=f"<#{raffle['log_channel_id']}>", inline=False)
        await self._respond_and_log(interaction, embed=embed, ephemeral=True, channel_id=raffle["log_channel_id"], public_response=True, mirror_to_log=True)

    @raffle.command(name="add", description="Manually add a member to a raffle.")
    @app_commands.describe(
        raffle_id="The raffle ID returned when the raffle was created.",
        user="Member to enter into the raffle.",
        entries="How many entries to add for this member.",
    )
    async def raffle_add(
        self,
        interaction: discord.Interaction,
        raffle_id: str,
        user: discord.Member,
        entries: app_commands.Range[int, 1] = 1,
    ) -> None:
        if not await self._check_permissions(interaction):
            return
        raffle = self._get_guild_raffle(interaction, raffle_id)
        if raffle is None:
            await self._respond_and_log(interaction, embed=self._build_embed("Raffle Not Found", "That raffle ID does not exist in this server.", color=discord.Color.red()), ephemeral=True)
            return
        if not raffle["active"]:
            await self._respond_and_log(interaction, embed=self._build_embed("Raffle Closed", "That raffle is no longer active.", color=discord.Color.red()), ephemeral=True, channel_id=raffle["log_channel_id"])
            return

        user_key = str(user.id)
        existing_entry = raffle["entrants"].get(user_key)
        current_count = existing_entry["entries"] if existing_entry else 0

        if raffle["allow_multiple_entries"]:
            new_count = current_count + entries
        else:
            if current_count >= 1:
                await self._respond_and_log(
                    interaction,
                    embed=self._build_embed("Entry Exists", f"{user.mention} already has their single allowed entry in this raffle.", color=discord.Color.orange()),
                    ephemeral=True,
                    channel_id=raffle["log_channel_id"],
                )
                return
            new_count = 1

        raffle["entrants"][user_key] = {
            "username": str(user),
            "entries": new_count,
        }
        await self._save_raffles()
        dm_sent = await self._send_entry_dm(
            user,
            raffle_name=raffle["name"],
            guild_name=interaction.guild.name if interaction.guild else "Unknown Server",
            added_by=interaction.user,
            entry_count=new_count,
        )

        embed = self._build_embed(
            "Entry Added",
            f"Added {entries if raffle['allow_multiple_entries'] else 1} entrie(s) for {user.mention} in **{raffle['name']}**.",
            color=discord.Color.green(),
        )
        # Reuse the verified Habbo avatar on the staff-facing confirmation embed so
        # moderators can immediately identify which player the entry update belongs to.
        thumbnail_url = self._get_habbo_thumbnail_url(user.id)
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        embed.add_field(name="Raffle ID", value=raffle["raffle_id"], inline=True)
        embed.add_field(name="User Total Entries", value=str(new_count), inline=True)
        embed.add_field(name="DM Status", value="Sent successfully" if dm_sent else "Entry added, but the DM could not be delivered.", inline=False)
        await self._respond_and_log(interaction, embed=embed, ephemeral=True, channel_id=raffle["log_channel_id"], public_response=True, mirror_to_log=True)

    @raffle.command(name="remove", description="Remove one or more entries from a raffle member.")
    @app_commands.describe(
        raffle_id="The raffle ID to edit.",
        user="Member whose entries should be removed.",
        entries="How many entries to remove when multiple entries are allowed.",
    )
    async def raffle_remove(
        self,
        interaction: discord.Interaction,
        raffle_id: str,
        user: discord.Member,
        entries: app_commands.Range[int, 1] = 1,
    ) -> None:
        if not await self._check_permissions(interaction):
            return
        raffle = self._get_guild_raffle(interaction, raffle_id)
        if raffle is None:
            await self._respond_and_log(interaction, embed=self._build_embed("Raffle Not Found", "That raffle ID does not exist in this server.", color=discord.Color.red()), ephemeral=True)
            return

        user_key = str(user.id)
        entrant = raffle["entrants"].get(user_key)
        if entrant is None:
            await self._respond_and_log(interaction, embed=self._build_embed("User Not Entered", f"{user.mention} does not have any entries in this raffle.", color=discord.Color.orange()), ephemeral=True, channel_id=raffle["log_channel_id"])
            return

        removed_entries = entrant["entries"] if not raffle["allow_multiple_entries"] else min(entries, entrant["entries"])
        remaining_entries = entrant["entries"] - removed_entries
        if remaining_entries <= 0 or not raffle["allow_multiple_entries"]:
            raffle["entrants"].pop(user_key, None)
            remaining_entries = 0
        else:
            entrant["entries"] = remaining_entries
            entrant["username"] = str(user)

        await self._save_raffles()
        embed = self._build_embed(
            "Entries Removed",
            f"Removed {removed_entries} entrie(s) from {user.mention} in **{raffle['name']}**.",
            color=discord.Color.green(),
        )
        embed.add_field(name="Remaining Entries", value=str(remaining_entries), inline=True)
        embed.add_field(name="Total Raffle Entries", value=str(self._total_entries(raffle)), inline=True)
        await self._respond_and_log(interaction, embed=embed, ephemeral=True, channel_id=raffle["log_channel_id"], public_response=True, mirror_to_log=True)

    @raffle.command(name="entries", description="View entries for a raffle.")
    @app_commands.describe(raffle_id="The raffle ID to inspect.")
    async def raffle_entries(self, interaction: discord.Interaction, raffle_id: str) -> None:
        if not await self._check_permissions(interaction):
            return
        raffle = self._get_guild_raffle(interaction, raffle_id)
        if raffle is None:
            await self._respond_and_log(interaction, embed=self._build_embed("Raffle Not Found", "That raffle ID does not exist in this server.", color=discord.Color.red()), ephemeral=True)
            return

        embed = self._build_embed(
            f"Entries for {raffle['name']}",
            raffle.get("description") or "No description provided.",
        )
        embed.add_field(name="Raffle ID", value=raffle["raffle_id"], inline=True)
        embed.add_field(name="Status", value="Active" if raffle["active"] else "Inactive", inline=True)
        embed.add_field(name="Unique Users", value=str(len(raffle["entrants"])), inline=True)
        embed.add_field(name="Total Entries", value=str(self._total_entries(raffle)), inline=True)
        embed.add_field(name="Multiple Entries", value="Enabled" if raffle["allow_multiple_entries"] else "Disabled", inline=True)

        if not raffle["entrants"]:
            embed.add_field(name="Entrants", value="No entries have been added yet.", inline=False)
        else:
            entrant_lines = [
                f"<@{user_id}> — **{entrant['entries']}** entrie(s)"
                for user_id, entrant in sorted(raffle["entrants"].items(), key=lambda item: (-item[1]["entries"], item[1]["username"].lower()))
            ]
            if len(entrant_lines) <= MAX_ENTRIES_DISPLAY:
                embed.add_field(name="Entrants", value="\n".join(entrant_lines), inline=False)
            else:
                preview = "\n".join(entrant_lines[:MAX_ENTRIES_DISPLAY])
                embed.add_field(name="Entrants", value=f"{preview}\n...and {len(entrant_lines) - MAX_ENTRIES_DISPLAY} more user(s).", inline=False)

        await self._respond_and_log(interaction, embed=embed, ephemeral=True, channel_id=raffle["log_channel_id"], public_response=True, mirror_to_log=True)

    @raffle.command(name="draw", description="Draw one or more unique winners from a raffle.")
    @app_commands.describe(
        raffle_id="The raffle ID to draw from.",
        winners="How many winners to draw.",
    )
    async def raffle_draw(
        self,
        interaction: discord.Interaction,
        raffle_id: str,
        winners: app_commands.Range[int, 1] = 1,
    ) -> None:
        if not await self._check_permissions(interaction):
            return
        raffle = self._get_guild_raffle(interaction, raffle_id)
        if raffle is None:
            await self._respond_and_log(interaction, embed=self._build_embed("Raffle Not Found", "That raffle ID does not exist in this server.", color=discord.Color.red()), ephemeral=True)
            return
        if not raffle["entrants"]:
            await self._respond_and_log(interaction, embed=self._build_embed("No Entrants", "This raffle has no entries to draw from.", color=discord.Color.red()), ephemeral=True, channel_id=raffle["log_channel_id"])
            return

        unique_entrants = len(raffle["entrants"])
        if winners > unique_entrants:
            await self._respond_and_log(
                interaction,
                embed=self._build_embed(
                    "Too Many Winners",
                    f"You requested {winners} winner(s), but only {unique_entrants} unique entrant(s) are available.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
                channel_id=raffle["log_channel_id"],
            )
            return

        winner_ids = self._pick_unique_weighted_winners(raffle, winners)
        raffle["winners"] = winner_ids
        # A completed draw should immediately lock the raffle so staff do not keep
        # adding entries after winners were already announced.
        raffle["active"] = False
        await self._save_raffles()

        winner_mentions = []
        dm_successes = 0
        total_entries = self._total_entries(raffle)
        for placement, winner_id in enumerate(winner_ids, start=1):
            winner_member = interaction.guild.get_member(winner_id) if interaction.guild else None
            winner_mentions.append(winner_member.mention if winner_member else f"<@{winner_id}>")
            if winner_member is None:
                continue

            # Build the winner card once so the same embed can be reused for the
            # direct message and the public raffle-channel announcement.
            winner_embed = self._build_winner_embed(
                winner_member,
                raffle=raffle,
                guild_name=interaction.guild.name if interaction.guild else "Unknown Server",
                placement=placement,
                total_winners=len(winner_ids),
            )

            # Send one DM embed per winner so each person receives a dedicated result
            # card with their own Habbo thumbnail, even when multiple winners are drawn.
            try:
                await winner_member.send(embed=winner_embed)
                dm_successes += 1
            except discord.Forbidden:
                pass
            except discord.HTTPException:
                LOGGER.exception("Failed to DM raffle winner confirmation to %s", winner_member.id)

            # Staff asked for the DM winner card to also appear in the raffle log
            # channel so the public record matches what winners receive privately.
            await self._mirror_embed_to_log_channel(winner_embed, channel_id=raffle["log_channel_id"])

        mentions = ", ".join(winner_mentions)
        embed = self._build_embed(
            "Winner Drawn",
            f"Winner(s) for **{raffle['name']}**: {mentions}",
            color=discord.Color.gold(),
        )
        embed.add_field(name="Raffle ID", value=raffle["raffle_id"], inline=True)
        embed.add_field(name="Winner Count", value=str(len(winner_ids)), inline=True)
        embed.add_field(name="Pool Size", value=str(total_entries), inline=True)
        embed.add_field(name="Raffle Status", value="Closed automatically after draw", inline=False)
        embed.add_field(name="Winner DM Status", value=f"Sent {dm_successes}/{len(winner_ids)} winner DM(s).", inline=False)
        await self._respond_and_log(interaction, embed=embed, ephemeral=True, channel_id=raffle["log_channel_id"], public_response=True, mirror_to_log=True)

    @raffle.command(name="end", description="Close a raffle so no more entries can be added.")
    @app_commands.describe(raffle_id="The raffle ID to close.")
    async def raffle_end(self, interaction: discord.Interaction, raffle_id: str) -> None:
        if not await self._check_permissions(interaction):
            return
        raffle = self._get_guild_raffle(interaction, raffle_id)
        if raffle is None:
            await self._respond_and_log(interaction, embed=self._build_embed("Raffle Not Found", "That raffle ID does not exist in this server.", color=discord.Color.red()), ephemeral=True)
            return
        if not raffle["active"]:
            await self._respond_and_log(interaction, embed=self._build_embed("Already Closed", "That raffle has already been closed.", color=discord.Color.orange()), ephemeral=True, channel_id=raffle["log_channel_id"])
            return

        raffle["active"] = False
        await self._save_raffles()
        embed = self._build_embed(
            "Raffle Closed",
            f"Raffle **{raffle['name']}** (`{raffle['raffle_id']}`) is now inactive.",
            color=discord.Color.green(),
        )
        embed.add_field(name="Total Entries", value=str(self._total_entries(raffle)), inline=True)
        embed.add_field(name="Unique Users", value=str(len(raffle["entrants"])), inline=True)
        await self._respond_and_log(interaction, embed=embed, ephemeral=True, channel_id=raffle["log_channel_id"], public_response=True, mirror_to_log=True)

    @raffle.command(name="list", description="List active raffles in this server.")
    async def raffle_list(self, interaction: discord.Interaction) -> None:
        if not await self._check_permissions(interaction):
            return
        if interaction.guild is None:
            await self._respond_and_log(interaction, embed=self._build_embed("Server Only", "Raffles can only be listed inside a server.", color=discord.Color.red()), ephemeral=True)
            return

        active_raffles = [raffle for raffle in self._raffles.values() if raffle["guild_id"] == interaction.guild.id and raffle["active"]]
        embed = self._build_embed("Active Raffles", "Currently active raffles for this server.")
        if not active_raffles:
            embed.description = "There are no active raffles in this server right now."
            await self._respond_and_log(interaction, embed=embed, ephemeral=True, public_response=True, mirror_to_log=True)
            return

        for raffle in sorted(active_raffles, key=lambda item: item["created_at"]):
            embed.add_field(
                name="Raffle",
                value=self._build_raffle_list_value(raffle),
                inline=False,
            )
        await self._respond_and_log(interaction, embed=embed, ephemeral=True, public_response=True, mirror_to_log=True)


async def setup(bot: commands.Bot) -> None:
    """Standard discord.py extension entry point."""
    await bot.add_cog(RaffleCog(bot))
