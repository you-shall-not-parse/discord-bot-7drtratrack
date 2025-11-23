import discord
from discord.ext import commands, tasks
from discord import app_commands
import requests
import datetime

# ===================== CONFIG =====================
MAP_VOTE_CHANNEL_ID = 1441751747935735878   # <<--- YOUR CHANNEL ID HERE

RCON_IP = "176.57.140.181"
RCON_PORT = 30216
RCON_PASSWORD = "bedcc53"

MAP_LIST = [
    "Foy",
    "Stalingrad",
    "Purple Heart Lane",
    "Omaha Beach",
]

# How long a map vote lasts
VOTE_DURATION_SECONDS = 180


class MapVote(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # -----------------------------------------------------------
    # RCON REQUEST FUNCTION
    # -----------------------------------------------------------
    def rcon_command(self, command: str):
        url = f"http://{RCON_IP}:{RCON_PORT}/rcon"
        payload = {"password": RCON_PASSWORD, "command": command}

        try:
            response = requests.post(url, json=payload, timeout=5)
            response.raise_for_status()
            return response.text
        except Exception as e:
            print(f"RCON ERROR: {e}")
            return None

    # -----------------------------------------------------------
    # SLASH COMMAND – START MAP VOTE
    # -----------------------------------------------------------
    @app_commands.command(name="start_mapvote", description="Start a map vote poll")
    async def start_mapvote(self, interaction: discord.Interaction):
        await interaction.response.send_message("Starting map vote…", ephemeral=True)

        channel = self.bot.get_channel(MAP_VOTE_CHANNEL_ID)
        if channel is None:
            await interaction.followup.send("❌ Map vote channel not found.", ephemeral=True)
            return

        # -------------------------------------------------------
        # TIMER DISPLAY
        # -------------------------------------------------------
        end_time = datetime.datetime.utcnow() + datetime.timedelta(seconds=VOTE_DURATION_SECONDS)
        end_unix = int(end_time.timestamp())

        description = (
            f"🗳️ **Vote for the next map!**\n"
            f"Voting ends <t:{end_unix}:R>\n"
            f"\n**Maps:**\n"
        )
        for m in MAP_LIST:
            description += f"• {m}\n"

        # -------------------------------------------------------
        # CREATE POLL (Discord API 2024+)
        # -------------------------------------------------------
        poll = discord.Poll(question="Which map should we play next?")
        for name in MAP_LIST:
            poll.add_answer(discord.PollAnswer(text=name))

        await channel.send(content=description, poll=poll)

    # -----------------------------------------------------------
    # READY EVENT (sync commands)
    # -----------------------------------------------------------
    @commands.Cog.listener()
    async def on_ready(self):
        try:
            synced = await self.bot.tree.sync()
            print(f"Synced {len(synced)} slash command(s).")
        except Exception as e:
            print(f"Slash sync error: {e}")


async def setup(bot):
    await bot.add_cog(MapVote(bot))
