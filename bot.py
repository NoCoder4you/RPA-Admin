import os
import sys
import discord
from discord.ext import commands, tasks
import asyncio
import logging
import random

# ------------------------------------------------------------------
# LOGGING
# ------------------------------------------------------------------

class UnicodeSafeStreamHandler(logging.StreamHandler):
    """Write log messages to the console without crashing on Windows code pages."""

    def emit(self, record):
        try:
            message = self.format(record)
            stream = self.stream
            try:
                stream.write(message + self.terminator)
            except UnicodeEncodeError:
                # Fall back to an escaped representation so important log lines still reach the console.
                encoding = getattr(stream, "encoding", None) or "utf-8"
                safe_message = message.encode(encoding, errors="backslashreplace").decode(encoding)
                stream.write(safe_message + self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)


LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_errors.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s:%(levelname)s:%(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        UnicodeSafeStreamHandler()
    ]
)
logger = logging.getLogger("rpa_admin_bot")

# ------------------------------------------------------------------
# TOKEN
# ------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_bot_token_from_env_file():
    env_file = os.path.join(BASE_DIR, "ENV", ".env")

    # Give a clear startup error when the expected ENV/.env file does not exist.
    if not os.path.isfile(env_file):
        raise FileNotFoundError(f"Missing environment file: {env_file}")

    with open(env_file, "r", encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()

            # Ignore blank lines and comments so standard .env formatting is supported.
            if not line or line.startswith("#"):
                continue

            # Ignore malformed lines that do not contain a key/value pair.
            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            normalized_key = key.strip().removeprefix("export ").strip()

            if normalized_key == "BOT_TOKEN":
                # Remove optional surrounding quotes from the token value.
                return value.strip().strip('"').strip("'")

    raise RuntimeError(f"BOT_TOKEN was not found in {env_file}")


TOKEN = load_bot_token_from_env_file()


# ------------------------------------------------------------------
# BOT SETUP
# ------------------------------------------------------------------

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="RPA ", intents=intents, help_command=None)
BACKGROUND_LOG_CHANNEL_ID = 1484064305732259940

# ------------------------------------------------------------------
# COMMAND LOGGING HELPERS
# ------------------------------------------------------------------


def safe_display_name(user):
    """Return a readable username without failing if discord fields are unavailable."""
    return getattr(user, "display_name", None) or getattr(user, "name", "Unknown User")



def format_channel_location(channel):
    """Describe the channel so logs show where a command was triggered."""
    if channel is None:
        return "Direct Message"

    guild = getattr(channel, "guild", None)
    guild_name = guild.name if guild else "Direct Message"
    channel_name = getattr(channel, "name", str(channel))
    return f"{guild_name} -> #{channel_name}"



def format_command_arguments(positional_arguments=None, keyword_arguments=None):
    """Build a compact argument summary for both prefix and slash commands."""
    argument_parts = []

    for value in positional_arguments or []:
        argument_parts.append(repr(value))

    for key, value in (keyword_arguments or {}).items():
        argument_parts.append(f"{key}={value!r}")

    return ", ".join(argument_parts) if argument_parts else "None"



def build_prefix_command_log(ctx):
    """Create a single structured log line for text command usage."""
    return (
        "Prefix command used | "
        f"user={safe_display_name(ctx.author)} ({ctx.author.id}) | "
        f"channel={format_channel_location(ctx.channel)} | "
        f"command={ctx.command.qualified_name if ctx.command else ctx.invoked_with} | "
        f"message={ctx.message.content} | "
        f"arguments={format_command_arguments(ctx.args[2:], ctx.kwargs)}"
    )



def build_slash_command_log(interaction, command):
    """Create a structured log line for slash command usage, including options."""
    command_name = command.qualified_name if command else interaction.command.name
    return (
        "Slash command used | "
        f"user={safe_display_name(interaction.user)} ({interaction.user.id}) | "
        f"channel={format_channel_location(interaction.channel)} | "
        f"command=/{command_name} | "
        f"arguments={format_command_arguments(keyword_arguments=interaction.namespace.__dict__)}"
    )



def log_failed_command(command_type, command_name, actor, channel, error, *, raw_input=None, arguments=None):
    """Capture failures with enough context to reproduce the command invocation."""
    logger.error(
        "%s failed | user=%s (%s) | channel=%s | command=%s | raw_input=%s | arguments=%s | error=%s",
        command_type,
        safe_display_name(actor),
        getattr(actor, "id", "Unknown ID"),
        format_channel_location(channel),
        command_name,
        raw_input or "None",
        arguments or "None",
        error,
        exc_info=True,
    )


def truncate_log_value(value, limit=1000):
    """Keep embed fields within Discord limits without dropping the important context."""
    if value is None:
        return "None"

    text_value = str(value)
    if len(text_value) <= limit:
        return text_value

    return f"{text_value[:limit - 3]}..."


async def send_background_log(title, color, *, actor=None, channel=None, command_name=None, arguments=None, raw_input=None, error_text=None):
    """Mirror log events into the dedicated Discord background log channel."""
    # Skip background logging until the bot cache is ready and the channel can be resolved safely.
    if not bot.is_ready():
        return

    log_channel = bot.get_channel(BACKGROUND_LOG_CHANNEL_ID)
    if log_channel is None:
        try:
            log_channel = await bot.fetch_channel(BACKGROUND_LOG_CHANNEL_ID)
        except Exception as fetch_error:
            logger.error(f"Failed to fetch background log channel: {fetch_error}", exc_info=True)
            return

    embed = discord.Embed(title=title, color=color)

    if actor is not None:
        embed.add_field(
            name="User",
            value=truncate_log_value(f"{safe_display_name(actor)} ({getattr(actor, 'id', 'Unknown ID')})"),
            inline=False,
        )

    if command_name is not None:
        embed.add_field(name="Command", value=truncate_log_value(command_name), inline=False)

    if channel is not None:
        embed.add_field(name="Channel", value=truncate_log_value(format_channel_location(channel)), inline=False)

    if arguments is not None:
        embed.add_field(name="Arguments", value=truncate_log_value(arguments), inline=False)

    if raw_input is not None:
        embed.add_field(name="Raw Input", value=truncate_log_value(raw_input), inline=False)

    if error_text is not None:
        embed.add_field(name="Error", value=truncate_log_value(error_text), inline=False)

    try:
        await log_channel.send(embed=embed)
    except Exception as send_error:
        logger.error(f"Failed to send background log message: {send_error}", exc_info=True)


def queue_background_log(title, color, **payload):
    """Schedule Discord log delivery without blocking the live command flow."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    loop.create_task(send_background_log(title, color, **payload))

# ------------------------------------------------------------------
# COMMAND LOGGING HELPERS
# ------------------------------------------------------------------


def safe_display_name(user):
    """Return a readable username without failing if discord fields are unavailable."""
    return getattr(user, "display_name", None) or getattr(user, "name", "Unknown User")



def format_channel_location(channel):
    """Describe the channel so logs show where a command was triggered."""
    if channel is None:
        return "Direct Message"

    guild = getattr(channel, "guild", None)
    guild_name = guild.name if guild else "Direct Message"
    channel_name = getattr(channel, "name", str(channel))
    return f"{guild_name} -> #{channel_name}"



def format_command_arguments(positional_arguments=None, keyword_arguments=None):
    """Build a compact argument summary for both prefix and slash commands."""
    argument_parts = []

    for value in positional_arguments or []:
        argument_parts.append(repr(value))

    for key, value in (keyword_arguments or {}).items():
        argument_parts.append(f"{key}={value!r}")

    return ", ".join(argument_parts) if argument_parts else "None"



def build_prefix_command_log(ctx):
    """Create a single structured log line for text command usage."""
    return (
        "Prefix command used | "
        f"user={safe_display_name(ctx.author)} ({ctx.author.id}) | "
        f"channel={format_channel_location(ctx.channel)} | "
        f"command={ctx.command.qualified_name if ctx.command else ctx.invoked_with} | "
        f"message={ctx.message.content} | "
        f"arguments={format_command_arguments(ctx.args[2:], ctx.kwargs)}"
    )



def build_slash_command_log(interaction, command):
    """Create a structured log line for slash command usage, including options."""
    command_name = command.qualified_name if command else interaction.command.name
    return (
        "Slash command used | "
        f"user={safe_display_name(interaction.user)} ({interaction.user.id}) | "
        f"channel={format_channel_location(interaction.channel)} | "
        f"command=/{command_name} | "
        f"arguments={format_command_arguments(keyword_arguments=interaction.namespace.__dict__)}"
    )



def log_failed_command(command_type, command_name, actor, channel, error, *, raw_input=None, arguments=None):
    """Capture failures with enough context to reproduce the command invocation."""
    logger.error(
        "%s failed | user=%s (%s) | channel=%s | command=%s | raw_input=%s | arguments=%s | error=%s",
        command_type,
        safe_display_name(actor),
        getattr(actor, "id", "Unknown ID"),
        format_channel_location(channel),
        command_name,
        raw_input or "None",
        arguments or "None",
        error,
        exc_info=True,
    )

# ------------------------------------------------------------------
# COG DISCOVERY / LOADING (from ./COGS)
# ------------------------------------------------------------------

COGS_DIR = os.path.join(BASE_DIR, "COGS")

# Ensure imports like "COGS.SomeCog" work regardless of cwd
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

def discover_extensions():
    if not os.path.isdir(COGS_DIR):
        raise FileNotFoundError(f"COGS folder not found: {COGS_DIR}")

    return [
        file[:-3] for file in os.listdir(COGS_DIR)
        if file.endswith(".py") and file != "__init__.py" and not file.startswith("_")
    ]

async def load_cogs():
    extensions = discover_extensions()
    for extension in extensions:
        try:
            await bot.load_extension(f"COGS.{extension}")
            print(f"[LOADED] - COGS.{extension}")
        except Exception as e:
            logger.error(f"Failed to load cog COGS.{extension}: {e}")
            print(f"--- !!! [FAILED] !!! --- - COGS.{extension}: {e}")
    print("All Cogs Loaded")

# ------------------------------------------------------------------
# HELP COMMAND (same behaviour as your CDA Admin file)
# ------------------------------------------------------------------

@bot.command(name="help")
async def custom_help(ctx):
    if ctx.author.id != 298121351871594497:
        embed = discord.Embed(
            title="Support",
            description="Message this bot, and a message will be sent to Noah's Discord Server.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed, delete_after=10)
        return

    cog_commands = {}
    for command in bot.commands:
        if command.hidden:
            continue
        cog_name = command.cog_name or "Uncategorized"
        cog_commands.setdefault(cog_name, []).append(command)

    embeds = []
    for cog_name, commands_list in cog_commands.items():
        embed = discord.Embed(
            title=f"Help - {cog_name}",
            description=f"Commands in the `{cog_name}` category",
            color=discord.Color.blue()
        )
        for cmd in commands_list:
            embed.add_field(
                name=f"`{ctx.prefix}{cmd.name}`",
                value=cmd.help or "No description provided.",
                inline=False
            )
        embeds.append(embed)

    if not embeds:
        await ctx.send("No commands available.", delete_after=5)
        return

    current_page = 0
    message = await ctx.send(embed=embeds[current_page])
    reactions = ["\u2B05\uFE0F", "\u27A1\uFE0F"]  # ⬅️ ➡️

    for reaction in reactions:
        await message.add_reaction(reaction)

    def check(reaction, user):
        return (
            user == ctx.author
            and str(reaction.emoji) in reactions
            and reaction.message.id == message.id
        )

    while True:
        try:
            reaction, user = await bot.wait_for("reaction_add", timeout=30.0, check=check)
            if str(reaction.emoji) == "\u2B05\uFE0F":
                current_page = (current_page - 1) % len(embeds)
            elif str(reaction.emoji) == "\u27A1\uFE0F":
                current_page = (current_page + 1) % len(embeds)

            await message.edit(embed=embeds[current_page])
            await message.remove_reaction(reaction.emoji, user)

        except asyncio.TimeoutError:
            try:
                await message.clear_reactions()
            except Exception as e:
                logger.error(f"Failed to clear reactions: {e}")
            break

# ------------------------------------------------------------------
# LOAD / UNLOAD / RELOAD COMMANDS
# ------------------------------------------------------------------

@bot.command(name="load")
@commands.is_owner()
async def load(ctx, extension: str):
    try:
        ext = extension if extension.startswith("COGS.") else f"COGS.{extension}"
        await bot.load_extension(ext)
        await ctx.send(f"Loaded `{ext}` successfully.", delete_after=2.5)
    except Exception as e:
        logger.error(f"Failed to load cog {extension}: {e}")
        await ctx.send(f"Failed to load `{extension}`: {e}", delete_after=2.5)

@bot.command(name="unload")
@commands.is_owner()
async def unload(ctx, extension: str):
    try:
        ext = extension if extension.startswith("COGS.") else f"COGS.{extension}"
        await bot.unload_extension(ext)
        await ctx.send(f"Unloaded `{ext}` successfully.", delete_after=2.5)
    except Exception as e:
        logger.error(f"Failed to unload cog {extension}: {e}")
        await ctx.send(f"Failed to unload `{extension}`: {e}", delete_after=2.5)

@bot.command(name="rc")
@commands.is_owner()
async def reload(ctx, extension: str):
    try:
        ext = extension if extension.startswith("COGS.") else f"COGS.{extension}"
        await bot.reload_extension(ext)
        await ctx.send(f"Reloaded `{ext}` successfully.", delete_after=2.5)
    except Exception as e:
        logger.error(f"Failed to reload cog {extension}: {e}")
        await ctx.send(f"Failed to reload `{extension}`: {e}", delete_after=2.5)

@bot.command(name="reload")
@commands.is_owner()
async def reload_all(ctx):
    try:
        await ctx.message.delete()
        extensions = discover_extensions()
        for extension in extensions:
            await asyncio.sleep(1)
            await bot.reload_extension(f"COGS.{extension}")
        await ctx.send("All cogs reloaded successfully.", delete_after=2.5)
    except Exception as e:
        logger.error(f"Failed to reload all cogs: {e}")
        await ctx.send(f"Failed to reload cogs: {e}", delete_after=2.5)

# ------------------------------------------------------------------
# RESTART / STOP / SYNC
# ------------------------------------------------------------------

@bot.command(name="restart")
@commands.is_owner()
async def restart(ctx):
    try:
        await ctx.send("Restarting the bot... Please wait!", delete_after=2.5)
        print("Bot is restarting...")
        await bot.close()
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        logger.error(f"Failed to restart the bot: {e}")
        await ctx.send(f"Failed to restart the bot: {e}", delete_after=5)

@bot.command(name="sync")
@commands.is_owner()
async def sync(ctx):
    await bot.tree.sync()

@bot.command(name="stop")
@commands.is_owner()
async def stop(ctx):
    await bot.close()

# ------------------------------------------------------------------
# STATUS LOOP (relative to bot folder)
# ------------------------------------------------------------------

def load_statuses(file_path=None):
    file_path = file_path or os.path.join(BASE_DIR, "statuses.txt")
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            statuses = [line.strip() for line in file if line.strip()]
            if not statuses:
                raise ValueError("Status file is empty.")
            return statuses
    except Exception as e:
        logger.error(f"Error loading statuses: {e}")
        return ["Default status message."]

@tasks.loop(minutes=0.25)
async def update_status():
    statuses = load_statuses()
    current_status = discord.Activity(
        type=discord.ActivityType.watching,
        name=random.choice(statuses)
    )
    await bot.change_presence(activity=current_status)

# ------------------------------------------------------------------
# ERROR LOGGING HOOKS
# ------------------------------------------------------------------

@bot.event
async def on_command(ctx):
    logger.info(build_prefix_command_log(ctx))
    queue_background_log(
        "Command Used",
        discord.Color.blue(),
        actor=ctx.author,
        channel=ctx.channel,
        command_name=ctx.command.qualified_name if ctx.command else ctx.invoked_with,
        arguments=format_command_arguments(ctx.args[2:], ctx.kwargs),
        raw_input=ctx.message.content,
    )

@bot.event
async def on_error(event_method, *args, **kwargs):
    logger.error(f"Unhandled exception in event: {event_method}", exc_info=True)

@bot.event
async def on_command_error(ctx, error):
    command_name = ctx.command.qualified_name if ctx.command else ctx.invoked_with
    arguments = format_command_arguments(ctx.args[2:], ctx.kwargs)
    log_failed_command(
        "Prefix command",
        command_name,
        ctx.author,
        ctx.channel,
        error,
        raw_input=ctx.message.content,
        arguments=arguments,
    )
    queue_background_log(
        "Failed Command",
        discord.Color.red(),
        actor=ctx.author,
        channel=ctx.channel,
        command_name=command_name,
        arguments=arguments,
        raw_input=ctx.message.content,
        error_text=str(error),
    )

@bot.listen("on_interaction")
async def log_slash_command_usage(interaction: discord.Interaction):
    # Listen for application commands without overriding discord.py's default interaction flow.
    if interaction.type == discord.InteractionType.application_command:
        command = interaction.command
        if command is not None:
            arguments = format_command_arguments(keyword_arguments=interaction.namespace.__dict__)
            logger.info(build_slash_command_log(interaction, command))
            queue_background_log(
                "Slash Command Used",
                discord.Color.teal(),
                actor=interaction.user,
                channel=interaction.channel,
                command_name=f"/{command.qualified_name}",
                arguments=arguments,
            )


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    if isinstance(error, discord.app_commands.CheckFailure):
        return

    command = interaction.command
    command_name = f"/{command.qualified_name}" if command else "/unknown"
    arguments = format_command_arguments(keyword_arguments=interaction.namespace.__dict__)
    log_failed_command(
        "Slash command",
        command_name,
        interaction.user,
        interaction.channel,
        error,
        raw_input=None,
        arguments=arguments,
    )
    queue_background_log(
        "Failed Slash Command",
        discord.Color.red(),
        actor=interaction.user,
        channel=interaction.channel,
        command_name=command_name,
        arguments=arguments,
        error_text=str(error),
    )

# ------------------------------------------------------------------
# READY
# ------------------------------------------------------------------

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}")

    try:
        await load_cogs()
    except Exception as e:
        logger.error(f"Failed during load_cogs(): {e}", exc_info=True)

    if not update_status.is_running():
        update_status.start()
    print("Status update task started.")

    for command in bot.tree.walk_commands():
        print(f"Command: {command.name} (Group: {command.parent})")

# ------------------------------------------------------------------
# RUN
# ------------------------------------------------------------------

bot.run(TOKEN)
