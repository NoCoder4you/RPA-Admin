"""Discord cog containing an owner-only leave command."""

from __future__ import annotations

from discord.ext import commands


class OwnerLeaveCog(commands.Cog):
    """Administrative cog with a command for making the bot leave a server."""

    def __init__(self, bot: commands.Bot) -> None:
        # Keep a bot reference so commands can query guild information.
        self.bot = bot

    @commands.command(name="leave", help="Owner-only: make the bot leave the current or specified server.")
    @commands.is_owner()
    async def leave(self, ctx: commands.Context, guild_id: int | None = None) -> None:
        """Leave the current guild, or a specific guild by ID when provided."""

        # Allow the owner to target a specific guild ID when managing remotely.
        target_guild = self.bot.get_guild(guild_id) if guild_id is not None else ctx.guild

        # Give clear feedback when the command has no valid guild to operate on.
        if target_guild is None:
            await ctx.send("I could not find that server.")
            return

        guild_name = target_guild.name
        await target_guild.leave()
        await ctx.send(f"Left server: **{guild_name}**")


async def setup(bot: commands.Bot) -> None:
    """Discord extension entrypoint for loading this cog."""

    await bot.add_cog(OwnerLeaveCog(bot))
