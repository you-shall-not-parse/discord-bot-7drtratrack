import discord
from discord.ext import commands
from discord import app_commands
from datetime import timedelta
import asyncio
import os

# Load env
from dotenv import load_dotenv
load_dotenv()

import rcon.rcon as rcon
from lib.config import config


# --------------------------------------------------
# RCON CONFIG OVERRIDE
# --------------------------------------------------
config._config_data = {
    "rcon": {
        0: {
            "host": os.getenv("RCON_HOST"),
            "port": int(os.getenv("RCON_PORT")),
            "api_key": os.getenv("RCON_API_KEY"),
            "ssl": False,
            "timeout": 5
        }
    }
}


# --------------------------------------------------
# LOCAL CONFIG VALUES (NOT SECRET)
# --------------------------------------------------
GUILD_ID = 1097913605082579024
MAPVOTE_CHANNEL_ID = 1441751747935735878
VOTE_DURATION_SECONDS = 60  # <==== Your vote duration

MAPS = {
    "Elsenborn": "elsenbornridge_warfare_day",
}

MAP_TIMERS = {
    "Elsenborn": "06:30",
}


# -------------------------
# RCON HELPERS
# -------------------------
async def get_current_map():
    try:
        cur = await rcon.get_Current_Map()
        if hasattr(cur, "pretty_name"):
            return cur.pretty_name
        return str(cur)
    except Exception as e:
        return f"Unknown ({e})"


async def set_map(map_id: str):
    """Try multiple RCON methods."""
    try:
        payload_b = {"map_name": map_id}
        payload_rot = {"map_names": [map_id]}

        if hasattr(rcon, "set_map"):
            res = await rcon.set_map(payload_b)
            return f"[set_map] {res}"

        if hasattr(rcon, "set_Map"):
            res = await rcon.set_Map(payload_b)
            return f"[set_Map] {res}"

        res = await rcon.set_Map_Rotation(payload_rot)
        return f"[set_Map_Rotation] {res}"

    except Exception as e:
        return f"ERROR: {e}"


# --------------------------------------------------
# COG USING REAL DISCORD POLLS
# --------------------------------------------------
class MapVote(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        guild = discord.Object(id=GUILD_ID)
        await self.bot.tree.sync(guild=guild)
        print("MapVote commands synced.")

    # ----------------------- COMMAND -----------------------
    @app_commands.command(
        name="start_mapvote",
        description="Start a poll for the next HLL map."
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def start_mapvote(self, interaction: discord.Interaction):

        channel = self.bot.get_channel(MAPVOTE_CHANNEL_ID)
        if not channel:
            return await interaction.response.send_message(
                "❌ Map vote channel not found!",
                ephemeral=True
            )

        await interaction.response.send_message("Starting poll...", ephemeral=True)

        current = await get_current_map()

        # --------------------
        # Create the poll
        # --------------------
        poll = discord.Poll(
            question=discord.PollMedia(
                f"🗺️ Vote for the next map!\n(Current: {current})",
                emoji=None
            ),
            duration=timedelta(seconds=VOTE_DURATION_SECONDS),
            multiple=False
        )

        # Add map choices
        for pretty, internal in MAPS.items():
            label = f"{pretty} ⏱️ {MAP_TIMERS.get(pretty, '?')}"
            poll.add_answer(text=label, emoji=None)

        # Send it
        msg = await channel.send(poll=poll)

        # -------- WAIT FOR POLL TO END --------
        await asyncio.sleep(VOTE_DURATION_SECONDS + 2)

        # Re-fetch to get results
        msg = await channel.fetch_message(msg.id)
        results = msg.poll.answers

        # Determine winner
        winner = max(results, key=lambda a: a.vote_count)
        winner_name = winner.text.split(" ⏱️")[0]
        winner_id = MAPS[winner_name]

        # Set the map
        rcon_result = await set_map(winner_id)

        await channel.send(
            f"🏆 **Poll finished!**\n"
            f"Winner: **{winner_name}** ({winner.vote_count} votes)\n"
            f"📡 RCON ID: `{winner_id}`\n"
            f"💬 RCON response:\n```{rcon_result}```"
        )


# --------------------------------------------------
# SETUP
# --------------------------------------------------
async def setup(bot):
    await bot.add_cog(MapVote(bot))
