import os
import logging
from logging.handlers import RotatingFileHandler
import discord
from discord.ext import commands
from dotenv import load_dotenv
import asyncio

load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Setup logging (console + file)
logger = logging.getLogger()
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')

# Console logging
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)

# Rotating file logging (.txt) - 5 MB per file, keep 3 backups
log_file_path = os.path.join(os.path.dirname(__file__), 'bot.log.txt')
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

# Command prefix (won't affect slash commands)
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    logging.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        logging.info(f"Synced {len(synced)} command(s)")
    except Exception as e:
        logging.error(f"Failed to sync commands: {e}")
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
    if not TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set in your environment or .env file!")
    async with bot:
        await bot.load_extension("cogs.botadmin")
        await bot.load_extension("cogs.rosterizer")
        await bot.load_extension("cogs.quick_exit")
        await bot.load_extension("cogs.bulkrole")
        await bot.load_extension("cogs.certify")
        await bot.load_extension("cogs.recruitform")
        await bot.load_extension("cogs.EmbedManager")
        await bot.load_extension("cogs.SquadUp")
        await bot.load_extension("cogs.CalendarCog")
        await bot.load_extension("cogs.BirthdayCog")
        await bot.load_extension("cogs.contentfeed")
        await bot.load_extension("cogs.discordgreeting")
        await bot.load_extension("cogs.echo")
        await bot.load_extension("cogs.mapvote")
        await bot.load_extension("cogs.HLLInfLeaderboard")
        await bot.load_extension("cogs.HLLArmLeaderboard")
        await bot.load_extension("cogs.gohamm")        
        await bot.load_extension("cogs.GameMonCog")
        await bot.load_extension("cogs.trainee_tracker")
        await bot.load_extension("cogs.armour_trainee_tracker")
        await bot.load_extension("cogs.recon_troop_tracker")
        await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot shut down manually.")
