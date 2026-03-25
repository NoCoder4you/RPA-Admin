"""Restrict selected channels so only bot accounts can post messages."""

from __future__ import annotations

import json
from pathlib import Path

import discord
from discord.ext import commands

from common_paths import json_file


class SterileChannelStore:
    """Persist and retrieve per-guild sterile channel IDs.

    The stored JSON shape is intentionally simple so server admins can inspect or
    repair the file manually if needed:

    {
      "<guild_id>": ["<channel_id>", "<channel_id>"]
    }
    """

    def __init__(self, *, config_path: str | Path | None = None) -> None:
        self.config_path = Path(config_path) if config_path else json_file("SterileChannels.json")

    def _load(self) -> dict[str, list[str]]:
        """Load JSON data and normalize malformed payloads to an empty mapping."""

        if not self.config_path.exists():
            return {}

        try:
            with self.config_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (json.JSONDecodeError, OSError):
            return {}

        if not isinstance(payload, dict):
            return {}

        normalized: dict[str, list[str]] = {}
        for guild_id, channel_ids in payload.items():
            if not isinstance(guild_id, str):
                continue
            if not isinstance(channel_ids, list):
                continue

            # Keep only numeric channel IDs represented as strings.
            valid_channel_ids = [value for value in channel_ids if isinstance(value, str) and value.isdigit()]
            if valid_channel_ids:
                normalized[guild_id] = valid_channel_ids

        return normalized

    def _save(self, payload: dict[str, list[str]]) -> None:
        """Write configuration to disk, creating parent folders when needed."""

        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with self.config_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)

    def get_channels(self, guild_id: int) -> set[int]:
        """Return sterile channel IDs configured for the given guild."""

        channel_ids = self._load().get(str(guild_id), [])
        return {int(channel_id) for channel_id in channel_ids}

    def add_channel(self, guild_id: int, channel_id: int) -> bool:
        """Add one channel to the guild config and return True only on first insert."""

        payload = self._load()
        guild_key = str(guild_id)
        channel_key = str(channel_id)

        existing = set(payload.get(guild_key, []))
        if channel_key in existing:
            return False

        existing.add(channel_key)
        payload[guild_key] = sorted(existing, key=int)
        self._save(payload)
        return True

    def remove_channel(self, guild_id: int, channel_id: int) -> bool:
        """Remove one sterile channel entry and return True only when a change occurred."""

        payload = self._load()
        guild_key = str(guild_id)
        channel_key = str(channel_id)

        existing = set(payload.get(guild_key, []))
        if channel_key not in existing:
            return False

        existing.remove(channel_key)
        if existing:
            payload[guild_key] = sorted(existing, key=int)
        else:
            # Drop empty guild keys so the config file stays tidy over time.
            payload.pop(guild_key, None)

        self._save(payload)
        return True


class SterileChannelCog(commands.Cog):
    """Enforce bot-only posting rules in staff-selected channels."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = SterileChannelStore()

    @staticmethod
    def _is_sterile_candidate(message: discord.Message) -> bool:
        """Quickly discard messages that should never be moderated by this cog."""

        if getattr(message.author, "bot", False):
            return False

        guild = getattr(message, "guild", None)
        channel = getattr(message, "channel", None)
        if guild is None or channel is None:
            # Ignore DMs/group DMs because sterile mode is a guild-level moderation rule.
            return False

        return True

    def _is_channel_sterile(self, *, guild_id: int, channel_id: int) -> bool:
        """Check whether the channel is configured as bot-only for the guild."""

        return channel_id in self.store.get_channels(guild_id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Delete user messages posted in sterile channels and leave bot messages untouched."""

        if not self._is_sterile_candidate(message):
            return

        guild_id = getattr(message.guild, "id", None)
        channel_id = getattr(message.channel, "id", None)
        if guild_id is None or channel_id is None:
            return

        if not self._is_channel_sterile(guild_id=guild_id, channel_id=channel_id):
            return

        try:
            await message.delete()
        except (discord.Forbidden, discord.HTTPException, AttributeError):
            # Permission/API failures should not break event processing.
            return

    @commands.group(name="sterile", invoke_without_command=True)
    @commands.has_permissions(manage_channels=True)
    async def sterile(self, ctx: commands.Context) -> None:
        """Show usage guidance for sterile subcommands.

        Command format requested by staff:
        - PREFIX sterile add <CHANNEL_ID>
        - PREFIX sterile remove <CHANNEL_ID>
        - PREFIX sterile list
        """

        await ctx.send(
            "Usage:\n"
            "• `PREFIX sterile add <CHANNEL_ID>`\n"
            "• `PREFIX sterile remove <CHANNEL_ID>`\n"
            "• `PREFIX sterile list`",
            delete_after=15,
        )

    @sterile.command(name="add")
    async def sterile_add(self, ctx: commands.Context, channel_id: int) -> None:
        """Register a channel ID as sterile (bot-only posting)."""

        guild = ctx.guild
        if guild is None:
            await ctx.send("This command can only be used in a server.", delete_after=10)
            return

        changed = self.store.add_channel(guild.id, channel_id)
        if changed:
            await ctx.send(f"✅ <#{channel_id}> is now a sterile channel. Only bots may post there.", delete_after=10)
            return

        await ctx.send(f"<#{channel_id}> is already configured as a sterile channel.", delete_after=10)

    @sterile.command(name="remove")
    async def sterile_remove(self, ctx: commands.Context, channel_id: int) -> None:
        """Unregister a channel ID from sterile enforcement."""

        guild = ctx.guild
        if guild is None:
            await ctx.send("This command can only be used in a server.", delete_after=10)
            return

        changed = self.store.remove_channel(guild.id, channel_id)
        if changed:
            await ctx.send(f"✅ Removed sterile mode from <#{channel_id}>. Users can post there again.", delete_after=10)
            return

        await ctx.send(f"<#{channel_id}> is not currently configured as a sterile channel.", delete_after=10)

    @sterile.command(name="list")
    async def sterile_list(self, ctx: commands.Context) -> None:
        """Show all sterile channels configured for the current guild."""

        guild = ctx.guild
        if guild is None:
            await ctx.send("This command can only be used in a server.", delete_after=10)
            return

        channel_ids = sorted(self.store.get_channels(guild.id))
        if not channel_ids:
            await ctx.send("No sterile channels are configured for this server.", delete_after=10)
            return

        channel_mentions = "\n".join(f"• <#{channel_id}>" for channel_id in channel_ids)
        await ctx.send(f"Sterile channels (bot-only posting):\n{channel_mentions}", delete_after=15)

    @sterile.error
    @sterile_add.error
    @sterile_remove.error
    @sterile_list.error
    async def sterile_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        """Return friendly permission/argument errors for sterile text commands."""

        if isinstance(error, commands.MissingPermissions):
            await ctx.send("You need the **Manage Channels** permission to manage sterile channels.", delete_after=10)
            return

        if isinstance(error, commands.BadArgument):
            await ctx.send(
                "Please provide a valid numeric channel ID, e.g. `PREFIX sterile add 123456789012345678`.",
                delete_after=10,
            )
            return

        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                "Missing channel ID. Example: `PREFIX sterile add 123456789012345678`.",
                delete_after=10,
            )
            return

        raise error


async def setup(bot: commands.Bot) -> None:
    """discord.py extension entrypoint."""

    await bot.add_cog(SterileChannelCog(bot))
