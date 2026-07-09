from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

JSON_DIR = Path(__file__).resolve().parent.parent / "JSON"
PAYVOID_STORE_PATH = JSON_DIR / "payvoids.json"
PAYBAN_STORE_PATH = JSON_DIR / "paybans.json"
RPA_SERVER_ID = 1480440930828816489
PAY_RESET_CHANNEL_ID = 1483460272487141447
PAYBAN_MENTION_ROLE_ID = 1480466902500511875
THIRD_PAYBAN_ALERT_CHANNEL_ID = 1480462138823807117
THIRD_PAYBAN_ALERT_ROLE_ID = 1480442366333685913
PAY_RESET_ALLOWED_ROLE_ID = 1480442366333685913
PAYVOID_THRESHOLD = 3
PAYBAN_DURATIONS = (timedelta(hours=24), timedelta(hours=48), timedelta(hours=72))
EASTERN_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class PaybanDecision:
    """Result returned after recording one pay void for a Habbo username."""

    void_count: int
    payban_offence_count: int
    payban_until: datetime | None


class JsonStore:
    """Tiny JSON helper used by the separate pay void and payban files."""

    def __init__(self, path: Path, default_data: dict[str, Any]) -> None:
        self.path = path
        self.default_data = default_data
        self.data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        """Load JSON data while keeping the bot online if a file is absent or invalid."""

        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return json.loads(json.dumps(self.default_data))
        except json.JSONDecodeError:
            # A malformed data file should not break every command in the cog.
            return json.loads(json.dumps(self.default_data))

        if not isinstance(loaded, dict):
            return json.loads(json.dumps(self.default_data))
        return loaded

    def save(self) -> None:
        """Write the backing JSON in a stable format for simple manual audits."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True), encoding="utf-8")

    def reset(self) -> None:
        """Replace current data with this store's empty default structure."""

        self.data = json.loads(json.dumps(self.default_data))
        self.save()


class PayDisciplineStore:
    """Persist pay voids and paybans in separate JSON files."""

    def __init__(self, voids_path: Path = PAYVOID_STORE_PATH, bans_path: Path = PAYBAN_STORE_PATH) -> None:
        self.voids = JsonStore(voids_path, {"members": {}})
        self.bans = JsonStore(bans_path, {"members": {}, "meta": {}})

    @staticmethod
    def _iso(value: datetime) -> str:
        """Serialize datetimes consistently as UTC ISO strings."""

        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _parse_timestamp(value: object) -> datetime | None:
        """Parse an ISO timestamp and normalize it to aware UTC."""

        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def username_key(username: str) -> str:
        """Return a stable JSON key for a typed Habbo username.

        Habbo names are typed as plain text and do not need to be Discord
        server members, so the discipline stores are keyed by a normalized
        username instead of a Discord member ID.
        """

        return username.strip().casefold()

    def _void_record(self, username: str) -> dict[str, Any]:
        """Return the mutable void record for a Habbo username from payvoids.json."""

        members = self.voids.data.setdefault("members", {})
        key = self.username_key(username)
        record = members.setdefault(key, {})
        record["username"] = username.strip()
        if not isinstance(record.get("voids"), list):
            record["voids"] = []
        return record

    def _ban_record(self, username: str) -> dict[str, Any]:
        """Return the mutable ban record for a Habbo username from paybans.json."""

        members = self.bans.data.setdefault("members", {})
        key = self.username_key(username)
        record = members.setdefault(key, {})
        record["username"] = username.strip()
        if not isinstance(record.get("offences"), int):
            record["offences"] = 0
        return record

    def record_void(
        self, username: str, moderator_id: int, now: datetime, actiontaken: bool
    ) -> PaybanDecision:
        """Record one Habbo username void and create a payban after the third weekly void."""

        now = now.astimezone(timezone.utc)
        username = username.strip()
        void_record = self._void_record(username)
        voids = void_record["voids"]
        # Voids intentionally store only who/when and whether action was taken;
        # the command does not ask users for a reason.
        voids.append(
            {
                "created_at": self._iso(now),
                "moderator_id": moderator_id,
                "actiontaken": actiontaken,
            }
        )
        void_count = len(voids)

        payban_until = None
        ban_offences = self._ban_record(username)["offences"]
        if void_count % PAYVOID_THRESHOLD == 0:
            ban_record = self._ban_record(username)
            ban_record["offences"] += 1
            ban_offences = ban_record["offences"]
            duration = PAYBAN_DURATIONS[min(ban_offences, len(PAYBAN_DURATIONS)) - 1]
            payban_until = now + duration
            ban_record["active_until"] = self._iso(payban_until)
            ban_record["updated_at"] = self._iso(now)

        self.voids.save()
        self.bans.save()
        return PaybanDecision(void_count, ban_offences, payban_until)

    def reset_week(self, reset_monday: datetime) -> None:
        """Clear weekly voids while preserving indefinite payban history."""

        self.voids.reset()
        # Payban records intentionally survive weekly resets so the escalation
        # count remains an indefinite history instead of restarting each Monday.
        self.bans.data.setdefault("meta", {})["last_reset_monday"] = reset_monday.date().isoformat()
        self.bans.save()

    def has_reset_for(self, reset_monday: datetime) -> bool:
        """Return whether the current Monday reset has already been announced."""

        return self.bans.data.get("meta", {}).get("last_reset_monday") == reset_monday.date().isoformat()

    def reset_payban_counter(self, username: str) -> None:
        """Reset one Habbo username's lifetime payban offence counter to zero."""

        ban_record = self._ban_record(username.strip())
        ban_record["offences"] = 0
        # Clear active ban timing fields because staff are explicitly resetting
        # this user's payban counter.
        ban_record.pop("active_until", None)
        ban_record.pop("updated_at", None)
        self.bans.save()


class PayVoidCog(commands.Cog):
    """Track weekly pay voids and record paybans without changing member roles."""

    pay = app_commands.Group(name="pay", description="Pay void and payban commands.")

    def __init__(self, bot: commands.Bot, *, store: PayDisciplineStore | None = None) -> None:
        self.bot = bot
        self.store = store or PayDisciplineStore()
        self._weekly_reset_checker.start()

    def cog_unload(self) -> None:
        self._weekly_reset_checker.cancel()

    @staticmethod
    def _now() -> datetime:
        """Return the current UTC time; isolated for straightforward tests."""

        return datetime.now(timezone.utc)

    @staticmethod
    def _format_expiry(value: datetime) -> str:
        """Render an expiry as a Discord absolute timestamp with relative context."""

        unix = int(value.timestamp())
        return f"<t:{unix}:F> (<t:{unix}:R>)"

    @staticmethod
    def _current_week_reset_monday(now: datetime) -> datetime:
        """Return the Monday midnight EST reset that owns the current week.

        The reset loop can be delayed by downtime or scheduler drift, so this
        intentionally returns the current week's reset boundary for any time
        after Monday 00:00 EST instead of only during that exact minute.
        """

        now_est = now.astimezone(EASTERN_TZ)
        monday = now_est - timedelta(days=now_est.weekday())
        return monday.replace(hour=0, minute=0, second=0, microsecond=0)

    @staticmethod
    def _has_role(interaction: discord.Interaction, role_id: int) -> bool:
        """Return whether the invoking Discord user has the required role ID."""

        return any(getattr(role, "id", None) == role_id for role in getattr(interaction.user, "roles", []))

    @staticmethod
    def _format_void_counter(decision: PaybanDecision) -> str:
        """Render weekly void progress as 1/3, 2/3, or 3/3 before the counter rolls over."""

        progress = decision.void_count % PAYVOID_THRESHOLD
        if progress == 0:
            return f"{PAYVOID_THRESHOLD}/{PAYVOID_THRESHOLD}"
        return f"{progress}/{PAYVOID_THRESHOLD}"

    @staticmethod
    def _build_third_payban_alert_embed(username: str) -> discord.Embed:
        """Create the escalation alert used when a Habbo username reaches a third payban."""

        embed = discord.Embed(title="Third Payban Alert", color=discord.Color.red())
        embed.add_field(name="Username", value=username, inline=False)
        embed.add_field(name="Alert", value=f"This is the 3rd time {username} has been paybanned.", inline=False)
        return embed

    @staticmethod
    def _build_payvoid_embed(username: str, decision: PaybanDecision, actiontaken: bool) -> discord.Embed:
        """Create the public pay discipline embed requested for Habbo voids and bans."""

        is_banned = decision.payban_until is not None
        embed = discord.Embed(
            title="Payban Issued" if is_banned else "Pay Void Recorded",
            color=discord.Color.red() if is_banned else discord.Color.gold(),
        )
        embed.add_field(name="Username", value=username, inline=False)
        embed.add_field(name="Voids", value=PayVoidCog._format_void_counter(decision), inline=False)
        embed.add_field(name="Action Taken", value="Yes" if actiontaken else "No", inline=False)
        # Show lifetime bans separately from the weekly void progress; every
        # 3/3 void cycle increments this ban counter by one.
        embed.add_field(name="Payban Counter", value=str(decision.payban_offence_count), inline=False)
        if is_banned:
            embed.add_field(name="Payban Until", value=PayVoidCog._format_expiry(decision.payban_until), inline=False)
        return embed

    @pay.command(name="void", description="Record a weekly pay void for a Habbo username.")
    @app_commands.describe(
        username="The Habbo username receiving a pay void",
        actiontaken="Whether action has already been taken for this void",
    )
    async def void(
        self, interaction: discord.Interaction, username: str, actiontaken: Literal["Yes", "No"]
    ) -> None:
        """Record one pay void using a Habbo username; no Discord membership required."""

        # Keep the command globally syncable while still enforcing the requested server-only behavior.
        if interaction.guild is None or interaction.guild.id != RPA_SERVER_ID:
            await interaction.response.send_message("This command is only available in the RPA server.", ephemeral=True)
            return

        habbo_username = username.strip()
        if not habbo_username:
            await interaction.response.send_message("Please provide a Habbo username to void.", ephemeral=True)
            return

        # The input is a Habbo username, not a Discord member mention, so record
        # the typed text directly and never require the user to be in this server.
        action_was_taken = actiontaken == "Yes"
        decision = self.store.record_void(habbo_username, interaction.user.id, self._now(), action_was_taken)
        content = f"<@&{PAYBAN_MENTION_ROLE_ID}>" if decision.payban_until is not None else None
        await interaction.response.send_message(
            content=content,
            embed=self._build_payvoid_embed(habbo_username, decision, action_was_taken),
            allowed_mentions=discord.AllowedMentions(roles=True),
        )

        if decision.payban_until is not None and decision.payban_offence_count == 3:
            await self._send_third_payban_alert(habbo_username)

    @pay.command(name="reset", description="Reset a Habbo username's payban counter.")
    @app_commands.describe(username="The Habbo username whose payban counter should be reset")
    async def reset(self, interaction: discord.Interaction, username: str) -> None:
        """Reset a Habbo username's lifetime payban counter; limited to the configured role."""

        if interaction.guild is None or interaction.guild.id != RPA_SERVER_ID:
            await interaction.response.send_message("This command is only available in the RPA server.", ephemeral=True)
            return

        if not self._has_role(interaction, PAY_RESET_ALLOWED_ROLE_ID):
            await interaction.response.send_message("You do not have permission to reset payban counters.", ephemeral=True)
            return

        habbo_username = username.strip()
        if not habbo_username:
            await interaction.response.send_message("Please provide a Habbo username to reset.", ephemeral=True)
            return

        self.store.reset_payban_counter(habbo_username)
        await interaction.response.send_message(
            f"Payban counter for `{habbo_username}` has been reset.", ephemeral=True
        )

    async def _send_third_payban_alert(self, username: str) -> None:
        """Ping the escalation channel when a Habbo username reaches their third payban."""

        channel = self.bot.get_channel(THIRD_PAYBAN_ALERT_CHANNEL_ID)
        if channel is None:
            return

        await channel.send(
            content=f"<@&{THIRD_PAYBAN_ALERT_ROLE_ID}>",
            embed=self._build_third_payban_alert_embed(username),
            allowed_mentions=discord.AllowedMentions(roles=True),
        )

    @tasks.loop(minutes=1)
    async def _weekly_reset_checker(self) -> None:
        """Clear weekly pay void data at Monday midnight EST and announce it."""

        reset_monday = self._current_week_reset_monday(self._now())
        if self.store.has_reset_for(reset_monday):
            return

        self.store.reset_week(reset_monday)
        channel = self.bot.get_channel(PAY_RESET_CHANNEL_ID)
        if channel is None:
            return
        await channel.send("Pay voids have been reset for the week.")

    @_weekly_reset_checker.before_loop
    async def _before_weekly_reset_checker(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    """Discord extension entrypoint for loading this cog."""

    await bot.add_cog(PayVoidCog(bot))
