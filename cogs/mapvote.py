import discord
from discord.ext import commands
import asyncio
import requests

# --------------------------------------------------
# CONFIG
# --------------------------------------------------
RCON_HOST = "176.57.140.181"
RCON_PORT = 30216
RCON_PASSWORD = "bedcc53"
MAPVOTE_CHANNEL_ID = 1441751747935735878
GUILD_ID = 1097913605082579024

# List of maps and optional "timer" (e.g., how recently played)
MAPS = {
    "Foy": "06:30",
    "Sainte-Mère-Église": "06:30",
    "Omaha Beach": "06:30",
    "Utah Beach": "06:30"
}

VOTE_DURATION = 60  # seconds


# --------------------------------------------------
# RCON HELPER
# --------------------------------------------------
def send_rcon(command: str):
    """
    Sends a command to the RCON server and returns the response.
    """
    url = f"http://{RCON_HOST}:{RCON_PORT}/rcon"
    payload = {"password": RCON_PASSWORD, "command": command}
    try:
        r = requests.post(url, json=payload, timeout=5)
        r.raise_for_status()
        return r.text
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
        current_map = send_rcon("getmap") or "Unknown"

        # ---------------- CREATE BUTTONS ----------------
        view = discord.ui.View(timeout=VOTE_DURATION)
        for map_name, timer in MAPS.items():
            label = f"{map_name} ⏱️ {timer}"
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.primary)

            async def callback(interact, map_name=map_name):
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
            winner = counter.most_common(1)[0][0]

            # Send RCON switchmap command
            rcon_response = send_rcon(f'switchmap "{winner}"')

            # Post results in channel
            await channel.send(
                f"🏆 Map vote ended! Winning map: **{winner}**\n"
                f"💻 RCON response:\n```{rcon_response}```"
            )
        else:
            await channel.send("No votes were cast.")


# --------------------------------------------------
# SETUP
# --------------------------------------------------
async def setup(bot):
    await bot.add_cog(MapVote(bot))
