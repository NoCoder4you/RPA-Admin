from __future__ import annotations

import asyncio
import discord
from discord.ext import commands

from habbo_verification_core import ServerConfigStore, VerifiedUserStore

WHITE_CHECK_MARK_EMOJI = "✅"
AWAITING_VERIFICATION_CHANNEL_ID = 1479391662076723224


class RulesRegulationsCog(commands.Cog):
    """Community cog exposing a text `rules` command with section-by-section embeds."""

    def __init__(self, bot: commands.Bot) -> None:
        # Keep a bot reference so this cog matches the structure used in other project cogs.
        self.bot = bot
        # Reuse the shared JSON-backed config store so the rules acknowledgement message ID
        # survives bot restarts without introducing a one-off file format.
        self.server_config_store = ServerConfigStore()
        # Reuse the existing verified-user persistence so the rules acknowledgement listener can
        # avoid re-queueing members who have already completed the verification flow.
        self.verified_store = VerifiedUserStore()

    def _build_rule_embeds(
        self,
        *,
        thumbnail_url: str | None = None,
        footer_text: str | None = None,
    ) -> list[discord.Embed]:
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

            # Show the bot's active guild nickname in the footer for identity consistency.
            if footer_text:
                embed.set_footer(text=footer_text)
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

        # Keep footer identity consistent on the closing embed as well.
        if footer_text:
            closing_embed.set_footer(text=footer_text)
        embeds.append(closing_embed)

        return embeds

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Re-apply the white check mark to the configured rules acknowledgement message on startup."""

        await self._ensure_rules_message_reaction()

    async def _ensure_rules_message_reaction(self) -> None:
        """Ensure the saved rules acknowledgement message still has the canonical white check mark."""

        configured_message_id = self.server_config_store.get_rules_acknowledgement_message_id()
        if configured_message_id is None:
            return

        # Search accessible guild text channels so the config only needs the message ID.
        for guild in self.bot.guilds:
            for channel in guild.text_channels:
                try:
                    message = await channel.fetch_message(configured_message_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    continue

                if self._message_has_bot_reaction(message, WHITE_CHECK_MARK_EMOJI):
                    return

                try:
                    await message.add_reaction(WHITE_CHECK_MARK_EMOJI)
                except (discord.Forbidden, discord.HTTPException):
                    return
                return

    def _message_has_bot_reaction(self, message: discord.Message | object, emoji: str) -> bool:
        """Return True when the bot already owns the requested reaction on the message."""

        for reaction in getattr(message, "reactions", []):
            if str(getattr(reaction, "emoji", "")) != emoji:
                continue
            if getattr(reaction, "me", False):
                return True
        return False

    async def _send_awaiting_verification_embed(
        self,
        *,
        guild: discord.Guild,
        member: discord.Member,
    ) -> None:
        """Post a per-member onboarding embed in the verification channel after staging."""

        # Staff requested that every Awaiting Verification onboarding notice land in the fixed
        # verification queue channel so the embed + ping always appears in one predictable place.
        channel = guild.get_channel(AWAITING_VERIFICATION_CHANNEL_ID)
        if channel is None:
            return

        embed = discord.Embed(
            title="Awaiting Verification",
            description=(
                f"{member.mention}, you're now queued for verification. Follow the steps below to verify your account."
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="Step 1",
            value="Open Habbo and copy the verification code provided by the bot.",
            inline=False,
        )
        embed.add_field(
            name="Step 2",
            value="Paste that code into your Habbo motto and save the change.",
            inline=False,
        )
        embed.add_field(
            name="Step 3",
            value="Come back here and run `/verify` again so the bot can confirm your motto.",
            inline=False,
        )
        embed.add_field(
            name="Need Help?",
            value="If the code does not work, double-check the spelling in your motto and ask staff for help in this channel.",
            inline=False,
        )

        try:
            # Mention the member in the message body as well so Discord reliably notifies them.
            await channel.send(content=member.mention, embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """Handle only the saved rules message and route ✅ acknowledgements into verification staging."""

        if payload.guild_id is None:
            return

        if self.bot.user is not None and payload.user_id == self.bot.user.id:
            return

        configured_message_id = self.server_config_store.get_rules_acknowledgement_message_id()
        if configured_message_id is None or payload.message_id != configured_message_id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        member = guild.get_member(payload.user_id)
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return

        # Always clean up the user's acknowledgement reaction so the rules message keeps a single
        # canonical bot-owned check mark instead of growing a long per-user reaction roster.
        await self._remove_member_reaction_from_message(payload, member)

        # Ignore all non-checkmark reactions after cleanup so only ✅ can trigger staging logic.
        if str(payload.emoji) != WHITE_CHECK_MARK_EMOJI:
            return

        # Members already stored in VerifiedUsers.json have already completed verification, so
        # the rules acknowledgement should not reassign their staging role.
        if self.verified_store.is_verified(str(payload.user_id)):
            return

        role = discord.utils.get(guild.roles, name="Awaiting Verification")
        if role is None or role in member.roles:
            return

        try:
            # Grant the staging role only after rules acknowledgement for users who still need verification.
            await member.add_roles(role, reason="Reacted with white check mark on rules acknowledgement message")
        except (discord.Forbidden, discord.HTTPException):
            return

        # Send one tailored onboarding embed per newly staged member so they know the exact next steps.
        await self._send_awaiting_verification_embed(guild=guild, member=member)

    async def _remove_member_reaction_from_message(
        self,
        payload: discord.RawReactionActionEvent,
        member: discord.Member,
    ) -> None:
        """Remove the reacting member's reaction so the saved rules message stays bot-owned."""

        channel = self.bot.get_channel(payload.channel_id)
        if channel is None:
            return

        try:
            message = await channel.fetch_message(payload.message_id)
            await message.remove_reaction(payload.emoji, member)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

    @commands.command(name="rules", help="Display the RPA rules and regulations in separate embeds.")
    async def rules(self, ctx: commands.Context) -> None:
        """Send the full ruleset in ordered embeds, one embed per section."""

        # Resolve the bot avatar once and propagate it to every embed thumbnail.
        bot_avatar = getattr(getattr(ctx, "me", None), "display_avatar", None)
        thumbnail_url = str(bot_avatar.url) if bot_avatar and getattr(bot_avatar, "url", None) else None

        # Build the requested branded footer format using the bot's current guild nickname.
        bot_nickname = getattr(getattr(ctx, "me", None), "display_name", None)
        if bot_nickname:
            footer_text = f"Royal Protection Agency - {bot_nickname}"
        else:
            # Gracefully fall back when guild member context is unavailable.
            footer_text = "Royal Protection Agency"

        embeds = self._build_rule_embeds(thumbnail_url=thumbnail_url, footer_text=footer_text)

        closing_message: discord.Message | None = None
        for index, embed in enumerate(embeds):
            await asyncio.sleep(1)
            sent_message = await ctx.send(embed=embed)

            # Persist only the final agreement embed message ID because that is the one users
            # should react to later and the only one the listener should watch.
            if index == len(embeds) - 1:
                closing_message = sent_message

        if closing_message is None:
            return

        try:
            await closing_message.add_reaction(WHITE_CHECK_MARK_EMOJI)
        except (discord.Forbidden, discord.HTTPException):
            return

        self.server_config_store.set_rules_acknowledgement_message_id(closing_message.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RulesRegulationsCog(bot))
