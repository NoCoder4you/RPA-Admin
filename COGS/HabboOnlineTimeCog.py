"""Discord cog providing `/onlinetime` for Habbo total time online lookups."""

from __future__ import annotations

import json
import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp
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

        if not isinstance(member, discord.Member):
            return False
        return any(role.name == "RPA Employee" for role in member.roles)

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
        """Convert a raw total number of seconds into a clean, human-readable value."""

        if total_seconds < 0:
            total_seconds = 0

        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, _seconds = divmod(remainder, 60)

        parts: list[str] = []
        if days:
            parts.append(f"{days} day" + ("s" if days != 1 else ""))
        if hours:
            parts.append(f"{hours} hour" + ("s" if hours != 1 else ""))
        if minutes and not days:
            # Include minutes only when the duration is less than a day to keep output concise.
            parts.append(f"{minutes} minute" + ("s" if minutes != 1 else ""))

        if not parts:
            return "0 hours"
        return ", ".join(parts)

    async def _fetch_habbo_profile(self, habbo_name: str) -> dict[str, Any]:
        """Fetch Habbo profile JSON from the official Habbo API."""

        encoded_name = quote(habbo_name, safe="")
        profile_url = f"https://www.habbo.com/api/public/users?name={encoded_name}"

        async with aiohttp.ClientSession() as session:
            async with session.get(profile_url, timeout=15) as response:
                if response.status == 404:
                    raise LookupError("Habbo user not found.")
                if response.status >= 500:
                    raise RuntimeError("Habbo API is currently unavailable.")
                if response.status != 200:
                    raise RuntimeError(f"Habbo API returned HTTP {response.status}.")

                payload = await response.json()
                if not isinstance(payload, dict):
                    raise RuntimeError("Habbo API returned an invalid response body.")
                return payload

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
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError):
            await interaction.followup.send(
                "The Habbo API is unavailable right now. Please try again shortly.",
                ephemeral=True,
            )
            return

        total_online_seconds = profile.get("totalOnlineTime")
        if not isinstance(total_online_seconds, int):
            await interaction.followup.send(
                "Total online time is unavailable for that Habbo user.",
                ephemeral=True,
            )
            return

        resolved_name = str(profile.get("name", target_habbo_name))
        readable_time = self._format_duration(total_online_seconds)
        figure_string = str(profile.get("figureString", ""))
        thumbnail_url = (
            f"https://www.habbo.com/habbo-imaging/avatarimage?figure={quote(figure_string, safe='')}&size=l&direction=2&head_direction=3&gesture=sml"
            if figure_string
            else None
        )

        embed = discord.Embed(color=discord.Color.blurple())
        embed.add_field(name="Habbo Username", value=resolved_name, inline=True)
        embed.add_field(name="Total time online", value=readable_time, inline=True)
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        embed.set_footer(text=f"Requested by {interaction.user}")
        embed.timestamp = discord.utils.utcnow()

        await interaction.followup.send(embed=embed, ephemeral=False)


async def setup(bot: commands.Bot) -> None:
    """Discord extension setup for loading this cog."""

    await bot.add_cog(HabboOnlineTimeCog(bot))
