import discord
from discord.ext import commands
from discord import app_commands
from datetime import timedelta
import asyncio
import os
import requests
from dotenv import load_dotenv

load_dotenv()

# --------------------------------------------------
# RCON WEB API SIMPLE WRAPPER
# (No complicated dependencies!)
# --------------------------------------------------

RCON_HOST = os.getenv("RCON_HOST")
RCON_PORT = os.getenv("RCON_PORT")
RCON_API_KEY = os.getenv("RCON_API_KEY")

BASE_URL = f"http://{RCON_HOST}:{RCON_PORT}/api/"


def rcon_post(endpoint: str, payload: dict):
    """Simple POST to HLL Web RCON."""
    try:
        response = requests.post(
            BASE_URL + endpoint,
            json=payload,
            headers={"x-api-key": RCON_API_KEY},
            timeout=5
        )
        return response.json()
    except Exception as e:
        return {"error": str(e)}


def rcon_get(endpoint: str):
    """Simple GET to HLL Web RCON."""
    try:
        response = requests.get(
            BASE_URL + endpoint,
            headers={"x-api-key": RCON_API_KEY},
            timeout=5
        )
        return response.json()
    except Exception as e:
        return {"error": str(e)}


# --------------------------------------------------
# SIMPLE CONFIG (kept inside the cog by design)
# --------------------------------------------------

GUILD_ID = 1097913605082579024
MAPVOTE_CHANNEL_ID = 1441751747935735878
VOTE_DURATION_SECONDS = 10

MAPS = {
    "Elsenborn": "elsenbornridge_warfare_day",
}

MAP_TIMERS = {
    "Elsenborn": "06:30",
}


# --------------------------------------------------
# SMALL HELPERS
# --------------------------------------------------

async def get_current_map():
    data = rcon_get("get_current_map")
    if "result" in data and data["result"]:
        return data["result"].get("pretty_name", "Unknown")
    return "Unknown"


async def set_map(map_id: str):
    """Try both known API formats."""
    try_methods = [
        ("set_map", {"map_name": map_id}),
        ("set_map_rotation", {"map_names": [map_id]}),
    ]

    for endpoint, payload in try_methods:
        result = rcon_post(endpoint, payload)
        if result and "error" not in result:
            return f"[{endpoint}] {result}"

    return f"ERROR: {result}"


# --------------------------------------------------
# MAIN COG
# --------------------------------------------------

class MapVote(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        guild = discord.Object(id=GUILD_ID)
        try:
            await self.bot.tree.sync(guild=guild)
            print("[MapVote] Commands synced.")
        except Exception as e:
            print("Sync error:", e)

    # Slash command to start vote
    @app_commands.command(name="start_mapvote", description="Start a poll to choose the next map.")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def start_mapvote(self, interaction: discord.Interaction):

        channel = self.bot.get_channel(MAPVOTE_CHANNEL_ID)
        if not channel:
            return await interaction.response.send_message("❌ Map vote channel not found!", ephemeral=True)

        await interaction.response.send_message("Map vote started!", ephemeral=True)

        current_map = await get_current_map()

        # Build the poll
        poll = discord.Poll(
            question=discord.PollMedia(
                f"🗺️ Vote for the next map! (Current: {current_map})",
                emoji=None
            ),
            duration=timedelta(seconds=VOTE_DURATION_SECONDS),
            multiple=False
        )

        # Add answers
        for pretty_name in MAPS.keys():
            poll.add_answer(
                text=f"{pretty_name} ⏱️ {MAP_TIMERS.get(pretty_name, '?')}",
                emoji=None
            )

        # Send the poll
        msg = await channel.send(poll=poll)

        # Wait for expiration
        await asyncio.sleep(VOTE_DURATION_SECONDS + 2)

        # Fetch updated poll results
        msg = await channel.fetch_message(msg.id)

        # Determine winner
        answers = msg.poll.answers
        winner = max(answers, key=lambda a: a.vote_count)

        winner_clean_name = winner.text.split(" ⏱️")[0]
        selected_map_id = MAPS[winner_clean_name]

        # Send to RCON
        result = await set_map(selected_map_id)

        # Announce winner
        await channel.send(
            f"🏆 **Map vote finished!**\n"
            f"Winner: **{winner_clean_name}** ({winner.vote_count} votes)\n"
            f"RCON map ID: `{selected_map_id}`\n\n"
            f"📡 RCON response:\n```{result}```"
        )


async def setup(bot):
    await bot.add_cog(MapVote(bot))
