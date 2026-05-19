"""Discord cog providing `/onlinetime` for Habbo total time online lookups."""

from __future__ import annotations

import json
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import discord
from discord import app_commands
from discord.ext import commands

from common_paths import json_file


class HabboOnlineTimeCog(commands.Cog):
    """Lookup and post Habbo total online time for verified RPA employees."""

    def __init__(self, bot: commands.Bot, verified_users_path: Path | None = None) -> None:
        self.bot = bot
        # Keep the path injectable so unit tests can provide a temporary JSON fixture.
        self.verified_users_path = verified_users_path or json_file("VerifiedUsers.json")

    @staticmethod
    def _has_employee_role(member: discord.abc.User | discord.Member) -> bool:
        """Return True when a guild member has the exact `RPA Employee` role."""

        # Prefer duck-typing over concrete class checks so this helper works for
        # discord.Member objects and lightweight test doubles that expose `.roles`.
        roles = getattr(member, "roles", None)
        if roles is None:
            return False
        return any(getattr(role, "name", None) == "RPA Employee" for role in roles)

    def _lookup_verified_habbo_username(self, discord_user_id: int) -> str | None:
        """Resolve a Discord user id to a Habbo username from the JSON verification file."""

        raw_text = self.verified_users_path.read_text(encoding="utf-8")
        payload = json.loads(raw_text)
        if not isinstance(payload, list):
            raise ValueError("VerifiedUsers.json must contain a JSON array.")

        discord_id_text = str(discord_user_id)
        for row in payload:
            if not isinstance(row, dict):
                continue
            if str(row.get("discord_id", "")) == discord_id_text:
                username = str(row.get("habbo_username", "")).strip()
                if username:
                    return username
        return None

    @staticmethod
    def _format_duration(total_seconds: int) -> str:
        """Convert raw seconds into an `X hours, Y minutes` style string."""

        if total_seconds < 0:
            total_seconds = 0

        total_minutes = total_seconds // 60
        hours, minutes = divmod(total_minutes, 60)
        return f"{hours} hour" + ("s" if hours != 1 else "") + f", {minutes} minute" + ("s" if minutes != 1 else "")

    async def _fetch_habbo_profile(self, habbo_name: str) -> dict[str, Any]:
        """Fetch Habbo profile JSON from the official Habbo API.

        We intentionally use Python's stdlib urllib instead of aiohttp to keep
        dependencies minimal. The blocking HTTP call runs in a thread via
        ``asyncio.to_thread`` so the Discord event loop remains responsive.
        """

        encoded_name = quote(habbo_name, safe="")
        profile_url = f"https://www.habbo.com/api/public/users?name={encoded_name}"
        request = Request(profile_url, headers={"User-Agent": "RPA-Admin/1.0"})

        def _read_json() -> dict[str, Any]:
            with urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    raise RuntimeError("Habbo API returned an invalid response body.")
                return payload

        try:
            return await asyncio.to_thread(_read_json)
        except HTTPError as exc:
            if exc.code == 404:
                raise LookupError("Habbo user not found.") from exc
            if exc.code >= 500:
                raise RuntimeError("Habbo API is currently unavailable.") from exc
            raise RuntimeError(f"Habbo API returned HTTP {exc.code}.") from exc
        except (URLError, TimeoutError) as exc:
            raise RuntimeError("Habbo API request failed.") from exc

    @staticmethod
    def _extract_online_time_seconds(profile: dict[str, Any]) -> int | None:
        """Resolve online-time seconds from API fields with a last-access fallback."""

        direct_seconds = profile.get("totalOnlineTime")
        if isinstance(direct_seconds, int):
            return max(0, direct_seconds)

        # Fallback requested by staff: when Habbo does not expose total online
        # seconds, estimate using elapsed time since `lastAccessTime`.
        last_access_dt = HabboOnlineTimeCog._parse_habbo_timestamp(profile.get("lastAccessTime"))
        if last_access_dt is not None:
            now_utc = datetime.now(timezone.utc)
            elapsed_seconds = int((now_utc - last_access_dt).total_seconds())
            return max(0, elapsed_seconds)
        return None

    @staticmethod
    def _parse_habbo_timestamp(value: str | None) -> datetime | None:
        """Parse Habbo timestamp text into a timezone-aware UTC datetime."""

        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z")
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return None

    @app_commands.command(name="onlinetime", description="Post Habbo total online time for an RPA employee.")
    @app_commands.describe(habbo_name="Optional Habbo username to look up")
    async def onlinetime(self, interaction: discord.Interaction, habbo_name: str | None = None) -> None:
        """Slash command handler that resolves a Habbo username and posts total online time."""

        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if not self._has_employee_role(interaction.user):
            await interaction.response.send_message(
                "You do not have permission to use `/onlinetime`.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=False, thinking=True)

        target_habbo_name = (habbo_name or "").strip()
        if not target_habbo_name:
            try:
                target_habbo_name = self._lookup_verified_habbo_username(interaction.user.id) or ""
            except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError):
                await interaction.followup.send(
                    "I could not read `VerifiedUsers.json` right now. Please try again later.",
                    ephemeral=True,
                )
                return

            if not target_habbo_name:
                await interaction.followup.send(
                    "You are not verified yet. Please provide a Habbo username manually.",
                    ephemeral=True,
                )
                return

        try:
            profile = await self._fetch_habbo_profile(target_habbo_name)
        except LookupError:
            await interaction.followup.send(
                "That Habbo username does not exist.",
                ephemeral=True,
            )
            return
        except RuntimeError:
            await interaction.followup.send(
                "The Habbo API is unavailable right now. Please try again shortly.",
                ephemeral=True,
            )
            return

        total_online_seconds = self._extract_online_time_seconds(profile)
        if total_online_seconds is None:
            await interaction.followup.send(
                "Total online time is unavailable for that Habbo user.",
                ephemeral=True,
            )
            return

        resolved_name = str(profile.get("name", target_habbo_name))
        readable_time = self._format_duration(total_online_seconds)
        figure_string = str(profile.get("figureString", ""))
        now_utc = datetime.now(timezone.utc)
        last_access_dt = self._parse_habbo_timestamp(profile.get("lastAccessTime"))
        last_access_display = (
            f"<t:{int(last_access_dt.timestamp())}:R>"
            if last_access_dt is not None
            else "Unavailable"
        )
        current_time_display = f"<t:{int(now_utc.timestamp())}:R>"
        thumbnail_url = (
            f"https://www.habbo.com/habbo-imaging/avatarimage?figure={quote(figure_string, safe='')}&size=l&direction=2&head_direction=3&gesture=sml"
            if figure_string
            else None
        )

        embed = discord.Embed(color=discord.Color.blurple())
        embed.add_field(name="Habbo Username", value=resolved_name, inline=True)
        embed.add_field(name="Total time online", value=readable_time, inline=True)
        # Show relative-time context directly in Discord using timestamp syntax.
        embed.add_field(name="Last access time", value=last_access_display, inline=False)
        embed.add_field(name="Current time", value=current_time_display, inline=False)
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        embed.set_footer(text=f"Requested by {interaction.user}")
        embed.timestamp = discord.utils.utcnow()

        await interaction.followup.send(embed=embed, ephemeral=False)


async def setup(bot: commands.Bot) -> None:
    """Discord extension setup for loading this cog."""

    await bot.add_cog(HabboOnlineTimeCog(bot))
