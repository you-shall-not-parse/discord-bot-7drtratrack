import os
import logging
import discord
from discord.ext import commands
from dotenv import load_dotenv
import asyncio

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
