import os
import logging
import discord
from discord.ext import commands
from dotenv import load_dotenv
import asyncio
from rcon.source import Client # Tests the connection to RCON

load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Setup logging
logging.basicConfig(level=logging.INFO)

# Intents setup
intents = discord.Intents.default()
intents.members = True
intents.message_content = True  # Needed for on_message and message content in DMs

# Command prefix (won't affect slash commands)
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    logging.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    logging.info("------")
    print(f"Bot is ready! Logged in as {bot.user} (ID: {bot.user.id})")

# Tests the RCON connection
def test_rcon_connection():
    host = os.getenv("RCON_HOST")
    port = int(os.getenv("RCON_PORT"))
    password = os.getenv("RCON_PASSWORD")

    try:
        with Client(host, port, passwd=password) as client:
            test = client.run("GetLogLines 1")
            print("✅ RCON test successful: received log line.")
            return True
    except Exception as e:
        print(f"❌ RCON test failed: {e}")
        return False

if not test_rcon_connection():
    print("🛑 Exiting: Unable to connect to RCON.")
    exit(1)

# Only process commands in guild channels, NOT in DMs
@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if not isinstance(message.channel, discord.DMChannel):
        await bot.process_commands(message)
    # Do NOT process commands in DMs; your cogs handle DMs

# Suppress CommandNotFound in DMs
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound) and isinstance(ctx.channel, discord.DMChannel):
        return  # Silently ignore CommandNotFound in DMs
    raise error

async def main():
    if not TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set in your environment or .env file!")
    async with bot:
        # Load your cogs
        await bot.load_extension("cogs.bulkrole")
        await bot.load_extension("cogs.trainee_tracker")
        await bot.load_extension("cogs.armour_trainee_tracker")
        await bot.load_extension("cogs.recon_troop_tracker")
        await bot.load_extension("cogs.certify")
        await bot.load_extension("cogs.rcon_tracker")
        await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot shut down manually.")
