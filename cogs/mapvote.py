import discord
from discord import app_commands
from discord.ext import commands
import requests

# --------------------------------------------------
# CONFIG
# --------------------------------------------------
RCON_HOST = "176.57.140.181"      # your server IP
RCON_PORT = 30216                 # your RCON port
RCON_PASSWORD = "bedcc53"
MAPVOTE_CHANNEL_ID = 1441751747935735878
GUILD_ID = 1097913605082579024     # your guild ID


# --------------------------------------------------
# RCON HELPER
# --------------------------------------------------
def send_rcon(command: str):
    """
    Minimal RCON-over-HTTP shim using requests.
    Your real RCON server must support HTTP/HLL-style API calls.
    """
    url = f"http://{RCON_HOST}:{RCON_PORT}/rcon"
    payload = {
        "password": RCON_PASSWORD,
        "command": command
    }

    try:
        r = requests.post(url, json=payload, timeout=5)
        if r.status_code != 200:
            return f"HTTP {r.status_code}: {r.text}"
        return r.text
    except Exception as e:
        return f"Error: {e}"


# --------------------------------------------------
# MAP LIST + TIMERS
# --------------------------------------------------
MAPS = {
    "Foy": "06:30",
    "Sainte-Mère-Église": "06:30",
    "Omaha Beach": "06:30",
    "Utah Beach": "06:30",
    "Carentan": "06:30",
    "Hill 400": "06:30",
    "Purple Heart Lane": "06:30",
    "Kharkov": "06:30",
    "Kursk": "06:30",
    "El Alamein": "06:30",
}


# --------------------------------------------------
# COG
# --------------------------------------------------
class MapVote(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # --------------------------------------------------
    # SYNC SLASH COMMANDS INSTANTLY
    # --------------------------------------------------
    @commands.Cog.listener()
    async def on_ready(self):
        guild = discord.Object(id=GUILD_ID)
        try:
            synced = await self.bot.tree.sync(guild=guild)
            print(f"[MapVote] Guild-sync OK: {len(synced)} command(s).")
        except Exception as e:
            print(f"[MapVote] Guild-sync error: {e}")

    # --------------------------------------------------
    # SLASH COMMAND: START MAP VOTE
    # --------------------------------------------------
    @app_commands.command(
        name="start_mapvote",
        description="Post a map vote poll and read current map timers."
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def start_mapvote(self, interaction: discord.Interaction):

        await interaction.response.send_message(
            "Posting map vote poll...", ephemeral=True
        )

        # Build poll question
        question = "🗺️ **Vote for the next map!**"
        options = []

        for name, timer in MAPS.items():
            options.append(
                discord.PollOption(
                    text=f"{name} — ⏱️ {timer}",
                )
            )

        poll = discord.Poll(
            question=question,
            options=options,
            multiple=False,
            duration=300  # 5 minutes
        )

        # Send to MAPVOTE_CHANNEL_ID
        channel = interaction.guild.get_channel(MAPVOTE_CHANNEL_ID)
        if channel is None:
            await interaction.followup.send(
                "❌ Map vote channel not found. Check channel ID.", ephemeral=True
            )
            return

        await channel.send(poll=poll)

        await interaction.followup.send("✅ Map vote posted.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(MapVote(bot))
