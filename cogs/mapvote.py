import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import os
import requests
from dotenv import load_dotenv

load_dotenv()

# --------------------------------------------------
# CRCON API SIMPLE WRAPPER (Bearer token)
# --------------------------------------------------

CRCON_PANEL_URL = "https://7dr.hlladmin.com/api/"
CRCON_API_KEY = os.getenv("CRCON_API_KEY")  # your Django admin API key

def rcon_get(endpoint: str):
    try:
        r = requests.get(
            CRCON_PANEL_URL + endpoint,
            headers={"Authorization": f"Bearer {CRCON_API_KEY}"},
            timeout=10
        )
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def rcon_post(endpoint: str, payload: dict):
    try:
        r = requests.post(
            CRCON_PANEL_URL + endpoint,
            json=payload,
            headers={"Authorization": f"Bearer {CRCON_API_KEY}"},
            timeout=10
        )
        return r.json()
    except Exception as e:
        return {"error": str(e)}


# --------------------------------------------------
# SIMPLE TEST CONFIG
# --------------------------------------------------

GUILD_ID = 1097913605082579024
MAPVOTE_CHANNEL_ID = 1441751747935735878

# Single-map test
MAPS = {
    "Elsenborn Ridge (Day)": "elsenbornridge_warfare_day"
}


# --------------------------------------------------
# DROPDOWN UI
# --------------------------------------------------

class MapVoteSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label=name,
                description="Select this map",
                value=map_id
            )
            for name, map_id in MAPS.items()
        ]

        super().__init__(
            placeholder="Choose the next map…",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        selected_map_id = self.values[0]

        # Announce selection
        await interaction.response.send_message(
            f"🗳️ **Vote registered:** `{selected_map_id}`",
            ephemeral=True
        )

        # Store result in the parent view
        self.view.selected_map = selected_map_id
        self.view.vote_event.set()


class MapVoteView(discord.ui.View):
    def __init__(self, timeout=30):
        super().__init__(timeout=timeout)
        self.add_item(MapVoteSelect())
        self.selected_map = None
        self.vote_event = asyncio.Event()


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

    # --------------------------------------------------
    # Slash command: start dropdown vote
    # --------------------------------------------------
    @app_commands.command(name="start_mapvote", description="Start a dropdown vote for testing.")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def start_mapvote(self, interaction: discord.Interaction):

        channel = self.bot.get_channel(MAPVOTE_CHANNEL_ID)
        if not channel:
            return await interaction.response.send_message("❌ Map vote channel missing!", ephemeral=True)

        await interaction.response.send_message("Vote started!", ephemeral=True)

        # Create dropdown UI
        view = MapVoteView(timeout=30)

        # Send dropdown vote message
        vote_msg = await channel.send(
            "**🗺️ Test Map Vote**\nSelect the map below:",
            view=view
        )

        # Wait for vote completion or timeout
        try:
            await asyncio.wait_for(view.vote_event.wait(), timeout=30)
        except asyncio.TimeoutError:
            return await channel.send("⏳ Vote expired (no selection).")

        # Determine result
        selected_map_id = view.selected_map

        # Send to CRCON API
        rcon_result = rcon_post("set_map", {"map_name": selected_map_id})

        # Announce final result
        await channel.send(
            f"🏆 **Vote complete!**\n"
            f"Selected map: `{selected_map_id}`\n\n"
            f"📡 **CRCON response:**\n```{rcon_result}```"
        )


async def setup(bot):
    await bot.add_cog(MapVote(bot))
