from __future__ import annotations

import discord
from discord.ext import commands


class RulesRegulationsCog(commands.Cog):
    """Community cog exposing a text `rules` command with section-by-section embeds."""

    def __init__(self, bot: commands.Bot) -> None:
        # Keep a bot reference so this cog matches the structure used in other project cogs.
        self.bot = bot

    def _build_rule_embeds(self, *, thumbnail_url: str | None = None) -> list[discord.Embed]:
        """Build all rule embeds in display order with one embed per section."""

        # Keep rule definitions data-driven so wording updates are easy and low-risk.
        rule_sections: list[tuple[str, str]] = [
            (
                "1) Zero Tolerance for Hate or Harassment",
                "Hate speech, racism, harassment, and targeted abuse are strictly forbidden. "
                "This includes discrimination based on race, religion, nationality, ethnicity, "
                "sexual orientation, gender identity, disability, or political beliefs.",
            ),
            (
                "2) No Spam or Disruptive Flooding",
                "Do not spam messages, emojis, mentions, links, or reactions in any channel. "
                "Repeated spam or disruptive behavior will lead to escalating moderation action.",
            ),
            (
                "3) No NSFW, Illegal, Gambling, or Pirated Content",
                "Any NSFW content, discussion of illegal activity, gambling promotion, or pirated "
                "content is prohibited across all RPA properties. Violations may result in immediate bans.",
            ),
            (
                "4) No Unauthorized Advertising",
                "Do not self-promote services, agencies, channels, or external communities without "
                "explicit Foundation approval or a designated promotion channel.",
            ),
            (
                "5) Follow Platform Terms of Service",
                "You must follow Discord's Terms of Service and Habbo's Terms of Service at all times.",
            ),
            (
                "6) Stay On Topic in Each Channel",
                "Use channels for their intended purpose. Keep conversations relevant to the channel topic "
                "and move off-topic discussion elsewhere.",
            ),
            (
                "7) Respect All Members",
                "Treat everyone with respect regardless of rank, role, title, or status. "
                "We are one team and expect mature, constructive communication.",
            ),
            (
                "8) Keep Profiles Appropriate",
                "Your username, avatar, status, bio, and display content must follow these rules. "
                "No hate, harassment, NSFW material, or advertising in profile elements.",
            ),
            (
                "9) English-Only Community",
                "To keep moderation and communication clear, use English in text and voice channels.",
            ),
        ]

        embeds: list[discord.Embed] = []
        for title, description in rule_sections:
            embed = discord.Embed(
                title=title,
                description=description,
                color=discord.Color.red(),
            )
            # Apply the same thumbnail to every rules embed for consistent branding.
            if thumbnail_url:
                embed.set_thumbnail(url=thumbnail_url)
            embeds.append(embed)

        # Add a final acknowledgement embed so members understand agreement implications.
        closing_embed = discord.Embed(
            title="Agreement and Enforcement",
            description=(
                "By participating in RPA spaces, you agree to these rules. Foundation staff may remove "
                "members from any RPA establishment (including Discord, rooms, and badges) when rules are broken."
            ),
            color=discord.Color.dark_red(),
        )
        # Mirror the same thumbnail on the final acknowledgement embed for consistency.
        if thumbnail_url:
            closing_embed.set_thumbnail(url=thumbnail_url)
        embeds.append(closing_embed)

        return embeds

    @commands.command(name="rules", help="Display the RPA rules and regulations in separate embeds.")
    async def rules(self, ctx: commands.Context) -> None:
        """Send the full ruleset in ordered embeds, one embed per section."""

        # Resolve the bot avatar once and propagate it to every embed thumbnail.
        bot_avatar = getattr(getattr(ctx, "me", None), "display_avatar", None)
        thumbnail_url = str(bot_avatar.url) if bot_avatar and getattr(bot_avatar, "url", None) else None

        embeds = self._build_rule_embeds(thumbnail_url=thumbnail_url)

        # Send one embed per message so each rule section stays visually separated in chat.
        for embed in embeds:
            await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    """Discord extension entrypoint for loading this cog."""

    await bot.add_cog(RulesRegulationsCog(bot))
