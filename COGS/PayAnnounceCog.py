from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks

from common_paths import json_file

# Eastern time is required by the pay schedule. Using IANA timezone names keeps
# DST transitions correct without any custom offset math.
EASTERN_TZ = ZoneInfo("America/New_York")

# The paid shift windows requested by the user. We keep both the public-facing
# label and the role mention ID in one place to avoid duplicated lookup tables.
PAY_WINDOWS: tuple[tuple[str, str], ...] = (
    ("12:00 AM", "1378512350981914634"),
    ("1:00 AM", "1378511887368585266"),
    ("6:00 AM", "1378511110025511002"),
    ("7:00 AM", "1378511732921733211"),
    ("12:00 PM", "1378512513729040507"),
    ("1:00 PM", "1378512026158235689"),
    ("6:00 PM", "1378512129564479558"),
    ("7:00 PM", "1378512208366931998"),
)


class PayAnnounceCog(commands.Cog):
    """Announce each EST pay window exactly 15 minutes before it begins."""

    def __init__(self, bot: commands.Bot, *, config_path: Path | None = None) -> None:
        self.bot = bot
        # Prefer explicit config path, then well-known defaults. If neither exists,
        # we can still discover the channel id by scanning JSON/*.json files.
        self.config_path = config_path
        self.announcement_channel_id = self._load_announcement_channel_id()
        self.external_emoji = "<:RPA:1484696606111699166>"
        self.unicode_emoji = "💰"
        self._last_announcement_key: str | None = None
        self._pay_schedule_checker.start()

    def cog_unload(self) -> None:
        self._pay_schedule_checker.cancel()

    def _load_announcement_channel_id(self) -> int | None:
        """Read the configured pay announcement channel from JSON configuration files."""

        # Ordered candidates make behavior predictable while still supporting
        # existing repositories that use serverconfig.json instead of server.json.
        if self.config_path is not None:
            candidate_paths = [self.config_path]
        else:
            candidate_paths = [json_file("server.json"), json_file("serverconfig.json")]

        for candidate in candidate_paths:
            channel_id = self._read_channel_id_from_config(candidate)
            if channel_id is not None:
                return channel_id

        # Fallback: scan every JSON file in JSON/ so custom config naming still works.
        json_folder = json_file("").resolve()
        for candidate in sorted(json_folder.glob("*.json")):
            if candidate in candidate_paths:
                continue
            channel_id = self._read_channel_id_from_config(candidate)
            if channel_id is not None:
                return channel_id

        print("[PayAnnounce] Could not find channels.payannounce in any JSON config file.")
        return None

    def _read_channel_id_from_config(self, config_path: Path) -> int | None:
        """Attempt to extract channels.payannounce from one specific JSON file."""

        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            print(f"[PayAnnounce] Config file not found: {config_path}")
            return None
        except json.JSONDecodeError as exc:
            print(f"[PayAnnounce] Invalid JSON in {config_path}: {exc}")
            return None

        channel_id = config.get("channels", {}).get("payannounce")
        if channel_id is None:
            return None

        try:
            return int(channel_id)
        except (TypeError, ValueError):
            print(f"[PayAnnounce] Invalid channel id value for payannounce in {config_path}: {channel_id!r}")
            return None

    @staticmethod
    def _parse_label_to_time(label: str) -> tuple[int, int]:
        """Convert labels like '12:00 PM' to 24-hour clock values."""

        time_str, meridiem = label.split(" ")
        hour, minute = map(int, time_str.split(":"))

        if meridiem == "AM":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12

        return hour, minute

    @classmethod
    def _window_start_for(cls, now_est: datetime, label: str) -> datetime:
        """Return the next EST datetime for the requested pay window label."""

        hour, minute = cls._parse_label_to_time(label)
        candidate = now_est.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now_est:
            candidate += timedelta(days=1)
        return candidate

    @classmethod
    def _due_window(cls, now_est: datetime) -> tuple[str, str] | None:
        """Find which pay window is due to be announced at this exact EST minute.

        A window is due when the current minute equals (window_start - 15 min).
        """

        current_minute = now_est.replace(second=0, microsecond=0)
        for label, role_id in PAY_WINDOWS:
            start = cls._window_start_for(current_minute, label)
            if start - timedelta(minutes=15) == current_minute:
                return label, role_id
        return None

    def _announcement_key(self, now_est: datetime, label: str) -> str:
        """Build an idempotency key so reconnects do not double-post in a minute."""

        return f"{now_est.strftime('%Y-%m-%d %H:%M')}|{label}"

    @tasks.loop(seconds=30)
    async def _pay_schedule_checker(self) -> None:
        """Wake regularly and announce only on EST schedule boundaries."""

        now_est = datetime.now(tz=EASTERN_TZ)
        due = self._due_window(now_est)
        if due is None:
            return

        label, role_id = due
        key = self._announcement_key(now_est.replace(second=0, microsecond=0), label)
        if key == self._last_announcement_key:
            return

        self._last_announcement_key = key
        await self._send_announcement(label, role_id)

    @_pay_schedule_checker.before_loop
    async def _before_pay_schedule_checker(self) -> None:
        await self.bot.wait_until_ready()

    async def _send_announcement(self, event_label: str, role_id: str) -> None:
        """Post the pay-start reminder embed text in the configured channel."""

        if not self.announcement_channel_id:
            print("[PayAnnounce] Announcement channel ID not configured.")
            return

        channel = self.bot.get_channel(self.announcement_channel_id)
        if channel is None:
            print(f"[PayAnnounce] Channel {self.announcement_channel_id} not found in cache.")
            return

        guild_member = getattr(channel.guild, "me", None)
        can_use_external = bool(
            guild_member
            and guild_member.guild_permissions
            and guild_member.guild_permissions.use_external_emojis
        )
        emoji = self.external_emoji if can_use_external else self.unicode_emoji

        now_est = datetime.now(tz=EASTERN_TZ)
        start_est = self._window_start_for(now_est, event_label)
        unix_timestamp = int(start_est.timestamp())

        await channel.send(
            f"# {emoji} Pay Time: {event_label} {emoji}\n"
            f"## Pay begins at <t:{unix_timestamp}:T> (<t:{unix_timestamp}:R>).\n"
            f"## <@&{role_id}>"
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PayAnnounceCog(bot))
