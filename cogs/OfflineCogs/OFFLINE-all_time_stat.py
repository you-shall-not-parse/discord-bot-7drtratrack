import discord
from discord.ext import commands, tasks
from discord import app_commands
import requests
import asyncio
from datetime import datetime, timezone
import os
from dotenv import load_dotenv

load_dotenv()

# CRCON API Configuration
CRCON_PANEL_URL = "https://7dr.hlladmin.com/api/"
CRCON_API_KEY = os.getenv("CRCON_API_KEY")

GUILD_ID = 1097913605082579024  # Replace with your guild ID

# Helper function to make GET requests to the CRCON API
def rcon_get(endpoint: str):
    try:
        r = requests.get(
            CRCON_PANEL_URL + endpoint,
            headers={"Authorization": f"Bearer {CRCON_API_KEY}"},
            timeout=10
        )
        return r.json()
    except Exception as e:
        print(f"[AllTimeStat] rcon_get error on {endpoint}: {e}")
        return {"error": str(e)}

# Cog for All-Time Stats
class AllTimeStat(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.message_sent = False  # Track if the message has been sent for the current match

    @commands.Cog.listener()
    async def on_ready(self):
        print("[AllTimeStat] Cog is ready.")

    @tasks.loop(seconds=5)  # Check every 5 seconds for match events
    async def check_match_time(self):
        # Fetch the current game state
        gamestate = rcon_get("get_gamestate")
        if not gamestate or gamestate.get("error") or gamestate.get("failed"):
            print("[AllTimeStat] Failed to fetch game state:", gamestate)
            return

        result = gamestate.get("result", {})
        time_remaining = float(result.get("time_remaining", 0))  # Time remaining in seconds

        # If less than 2 minutes (120 seconds) remain and the message hasn't been sent yet
        if 0 < time_remaining <= 120 and not self.message_sent:
            print("[AllTimeStat] Less than 2 minutes remaining, sending top combat scores.")
            await self.send_top_combat_scores()
            self.message_sent = True  # Prevent sending the message multiple times

        # Reset the flag if a new match starts (time_remaining resets to a high value)
        if time_remaining > 1200:  # Assuming matches are longer than 20 minutes
            self.message_sent = False

    async def send_top_combat_scores(self):
        # Fetch live scoreboard data
        data = rcon_get("get_live_scoreboard")
        if not data or data.get("error") or data.get("failed"):
            print("[AllTimeStat] Failed to fetch live scoreboard:", data)
            return

        players = data.get("result", {}).get("players", [])
        if not players:
            print("[AllTimeStat] No players found in scoreboard.")
            return

        # Sort players by combat score and get the top 5
        top_players = sorted(players, key=lambda p: p.get("combat", 0), reverse=True)[:5]

        # Format the top 5 scores
        top_scores = "\n".join(
            f"**{i+1}. {p.get('name', 'Unknown')}** - Combat Score: {p.get('combat', 0)}"
            for i, p in enumerate(top_players)
        )

        # Broadcast the top scores to all players
        message = f"🏆 **Top 5 Combat Scores** 🏆\n\n{top_scores}"
        await self.broadcast_to_all(message)

    async def broadcast_to_all(self, message: str):
        if not message:
            return

        data = rcon_get("get_players")
        if not data or data.get("error") or data.get("failed"):
            print("[AllTimeStat] broadcast_to_all: failed to get players:", data)
            return

        players = data.get("result") or []
        if not players:
            return

        for p in players:
            uid = p.get("player_id")
            if not uid:
                continue

            payload = {
                "player_id": uid,
                "message": message,
                "by": "7DRBot",
                "save_message": False,
            }
            _ = rcon_get("message_player", payload)
            await asyncio.sleep(0.1)  # Avoid spamming the API

    @check_match_time.before_loop
    async def before_check_match_time(self):
        print("[AllTimeStat] Waiting until bot is ready before starting check_match_time...")
        await self.bot.wait_until_ready()
        print("[AllTimeStat] Bot ready, check_match_time will now run.")

async def setup(bot: commands.Bot):
    await bot.add_cog(AllTimeStat(bot))