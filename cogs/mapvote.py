import discord
from discord.ext import commands
import asyncio
import requests
import json

# --------------------------------------------------
# CONFIG
# --------------------------------------------------
RCON_HOST = "176.57.140.181"
RCON_PORT = 30216
RCON_PASSWORD = "bedcc53"
MAPVOTE_CHANNEL_ID = 1441751747935735878
GUILD_ID = 1097913605082579024

# User-friendly map names -> server map names
MAPS = {
    "Elsenborn": "elsenbornridge_warfare_day",
}

# Optional timers (how recently played)
MAP_TIMERS = {
    "Foy": "06:30",
    "Sainte-Mère-Église": "06:30",
    "Omaha Beach": "06:30",
    "Utah Beach": "06:30"
}

VOTE_DURATION = 60  # seconds

# --------------------------------------------------
# RCON HELPER
# --------------------------------------------------
def send_rcon_set_map(map_name: str):
    """
    Sends the set_map command to the server with JSON arguments.
    Returns the server JSON response as a formatted string.
    """
    url = f"http://{RCON_HOST}:{RCON_PORT}/rcon"
    payload = {
        "password": RCON_PASSWORD,
        "command": "set_map",
        "arguments": {"map_name": map_name}
    }

    try:
        r = requests.post(url, json=payload, timeout=5)
        r.raise_for_status()
        return json.dumps(r.json(), indent=2)
    except Exception as e:
        return f"Error: {e}"

def get_current_map():
    """
    Query the current map from the server.
    Replace 'getmap' with the correct command if needed.
    """
    url = f"http://{RCON_HOST}:{RCON_PORT}/rcon"
    payload = {"password": RCON_PASSWORD, "command": "getmap"}
    try:
        r = requests.post(url, json=payload, timeout=5)
        r.raise_for_status()
        result = r.json()
        # If the server returns {"result":"map_name"} or similar
        return result.get("result", "Unknown") if isinstance(result, dict) else str(result)
    except Exception as e:
        return f"Error: {e}"


# --------------------------------------------------
# COG
# --------------------------------------------------
class MapVote(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.votes = {}

    # ---------------- GUILD-SYNC SLASH COMMANDS ----------------
    @commands.Cog.listener()
    async def on_ready(self):
        guild = discord.Object(id=GUILD_ID)
        try:
            synced = await self.bot.tree.sync(guild=guild)
            print(f"[MapVote] Guild commands synced: {len(synced)}")
        except Exception as e:
            print(f"[MapVote] Guild sync error: {e}")

    # ---------------- START MAP VOTE ----------------
    @discord.app_commands.command(
        name="start_mapvote",
        description="Start a map vote with buttons"
    )
    @discord.app_commands.guilds(discord.Object(id=GUILD_ID))
    async def start_mapvote(self, interaction: discord.Interaction):
        self.votes = {}  # reset votes

        channel = self.bot.get_channel(MAPVOTE_CHANNEL_ID)
        if channel is None:
            await interaction.response.send_message(
                "Map vote channel not found", ephemeral=True
            )
            return

        # ---------------- GET CURRENT MAP ----------------
        current_map = get_current_map()

        # ---------------- CREATE BUTTONS ----------------
        view = discord.ui.View(timeout=VOTE_DURATION)
        for display_name, server_name in MAPS.items():
            timer = MAP_TIMERS.get(display_name, "")
            label = f"{display_name} ⏱️ {timer}" if timer else display_name
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.primary)

            async def callback(interact, map_name=display_name):
                self.votes[interact.user.id] = map_name
                await interact.response.send_message(
                    f"You voted for {map_name}", ephemeral=True
                )

            button.callback = callback
            view.add_item(button)

        # ---------------- SEND POLL ----------------
        await channel.send(
            f"🗺️ **Vote for the next map!**\n"
            f"🎯 Current map: **{current_map}**",
            view=view
        )

        await interaction.response.send_message(
            "Map vote started!", ephemeral=True
        )

        # ---------------- WAIT AND PROCESS RESULTS ----------------
        await asyncio.sleep(VOTE_DURATION)

        if self.votes:
            from collections import Counter
            counter = Counter(self.votes.values())
            winner_display = counter.most_common(1)[0][0]
            winner_map_name = MAPS[winner_display]

            # Send RCON set_map command
            rcon_response = send_rcon_set_map(winner_map_name)

            # Post results in channel
            await channel.send(
                f"🏆 Map vote ended! Winning map: **{winner_display}**\n"
                f"💻 RCON response:\n```{rcon_response}```"
            )
        else:
            await channel.send("No votes were cast.")


# --------------------------------------------------
# SETUP
# --------------------------------------------------
async def setup(bot):
    await bot.add_cog(MapVote(bot))
