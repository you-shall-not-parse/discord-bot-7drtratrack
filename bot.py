# bot.py
import discord
from discord.ext import commands
import asyncio

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Bot is ready: {bot.user}")

async def main():
    await bot.load_extension("traineetrackpyth")  # This loads the cog
    await bot.start("DISCORD_BOT_TOKEN")

asyncio.run(main())
