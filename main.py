import os
import logging
from logging.handlers import RotatingFileHandler
import discord
from discord.ext import commands
from dotenv import load_dotenv
import asyncio

from config import BOT_LOG_PATH, MAIN_GUILD_ID
from config.hll_API_config import get_hll_backend_status

load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")

EXTENSIONS = (
    "cogs.quick_exit",
    "cogs.bulkrole",
    "cogs.certify",
    "cogs.recruitform",
    "cogs.EmbedManager",
    "cogs.SquadUp",
    "cogs.eventscalendar",
    "cogs.BirthdayCog",
    "cogs.contentfeed",
    "cogs.discordgreeting",
    "cogs.echo",
    "cogs.HLLInfLeaderboard",
    "cogs.HLLArmLeaderboard",
    "cogs.GameMonCog",
    "cogs.multi_trainee_tracker",
    "cogs.t17_role_index",
    "cogs.rollcall",
    "cogs.nameshame",
    "cogs.outofoffice",
    "cogs.wardiary",
    "cogs.t17lookup",
    "cogs.t17serveradmin",
    "cogs.applyroletomessage",
    "cogs.hellorleaderboard",
    "cogs.docsync",
    "cogs.supporters_embed",
    "cogs.raid",
)

DISABLED_EXTENSIONS = (
    "cogs.rosterizer",
    "cogs.mapvote",
)


def validate_runtime_configuration() -> None:
    if not TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set in your environment or .env file!")

    status = get_hll_backend_status()
    provider = str(status.get("provider") or "").lower()
    missing: list[str] = []

    if provider == "bifrost":
        if not status.get("client_id_present"):
            missing.append(str(status.get("client_id_env") or "BIFROST_CLIENT_ID"))
        if not status.get("client_secret_present"):
            missing.append(str(status.get("client_secret_env") or "BIFROST_CLIENT_SECRET"))
        if not status.get("server_id"):
            missing.append("BIFROST_SERVER_ID")
    elif provider == "crcon":
        if not status.get("panel_url"):
            missing.append("CRCON_PANEL_URL")
        if not status.get("api_key_present"):
            missing.append(str(status.get("api_key_env") or "CRCON_API_KEY"))
    else:
        missing.append(f"supported HLL backend provider (got {provider or 'empty'})")

    if missing:
        logging.warning(
            "Optional HLL features may be unavailable; missing configuration: %s",
            ", ".join(missing),
        )

# Setup logging (console + file)
logger = logging.getLogger()
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')

# Console logging
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)

# Rotating file logging (.txt) - 5 MB per file, keep 3 backups
log_file_path = BOT_LOG_PATH
file_handler = RotatingFileHandler(log_file_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')
file_handler.setFormatter(formatter)

# Apply handlers
logger.handlers.clear()
logger.addHandler(console_handler)
logger.addHandler(file_handler)

# Intents setup
intents = discord.Intents.default()
intents.members = True
intents.message_content = True  # Needed for on_message and message content in DMs
intents.presences = True  # This is critical for tracking game activity
intents.reactions = True  # Needed for raw reaction events
intents.guild_scheduled_events = True  # Needed for scheduled event create/update/delete listeners


class RatBot(commands.Bot):
    async def setup_hook(self) -> None:
        loaded_extensions: list[str] = []
        for extension in EXTENSIONS:
            try:
                await self.load_extension(extension)
            except Exception:
                logging.exception("Failed to load optional extension %s", extension)
            else:
                loaded_extensions.append(extension)
                logging.info("Loaded extension %s", extension)

        if not loaded_extensions:
            raise RuntimeError("No bot extensions loaded successfully")

        logging.info(
            "Loaded %d/%d extensions; disabled extensions: %s",
            len(loaded_extensions),
            len(EXTENSIONS),
            ", ".join(DISABLED_EXTENSIONS) or "none",
        )

        try:
            synced = await self.tree.sync()
            logging.info("Synced %d global command(s)", len(synced))
        except Exception:
            logging.exception("Failed to sync global commands")

        main_guild = discord.Object(id=MAIN_GUILD_ID)
        try:
            guild_synced = await self.tree.sync(guild=main_guild)
            logging.info("Synced %d guild command(s) to %s", len(guild_synced), main_guild.id)
        except Exception:
            logging.exception("Failed to sync commands to guild %s", main_guild.id)


# Command prefix does not affect slash commands.
bot = RatBot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    logging.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    logging.info("------")
    print(f"Bot is ready! Logged in as {bot.user} (ID: {bot.user.id})")

# Only process commands in guild channels, NOT in DMs
@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if not isinstance(message.channel, discord.DMChannel):
        await bot.process_commands(message)
    # Do NOT process commands in DMs; your cogs handle DMs

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound) and isinstance(ctx.channel, discord.DMChannel):
        return  # Silently ignore CommandNotFound in DMs
    
    # Add logging for other errors
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You don't have permission to use this command.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Missing required argument: {error.param.name}")
    else:
        logging.error(f"Error in command {ctx.command}: {error}", exc_info=error)

async def main():
    validate_runtime_configuration()
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot shut down manually.")
