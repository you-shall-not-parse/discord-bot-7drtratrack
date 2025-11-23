import discord
from discord.ext import commands, tasks
import asyncio

# ---------------- CONFIG ----------------
RCON_HOST = "176.57.140.181"
RCON_PORT = 30216
RCON_PASSWORD = "bedcc53"
MAPVOTE_CHANNEL_ID = 1441751747935735878
GUILD_ID = 1097913605082579024

MAPS = ["Foy", "Sainte-Mère-Église", "Omaha Beach", "Utah Beach"]

VOTE_DURATION = 60  # seconds

# ---------------- RCON HELPER ----------------
def send_rcon(command: str):
    import requests
    url = f"http://{RCON_HOST}:{RCON_PORT}/rcon"
    payload = {"password": RCON_PASSWORD, "command": command}
    try:
        r = requests.post(url, json=payload, timeout=5)
        return r.text
    except Exception as e:
        return f"Error: {e}"

# ---------------- COG ----------------
class MapVote(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.votes = {}

    @commands.Cog.listener()
    async def on_ready(self):
        guild = discord.Object(id=GUILD_ID)
        await self.bot.tree.sync(guild=guild)
        print("[MapVote] Commands synced")

    @discord.app_commands.command(name="start_mapvote", description="Start a map vote")
    @discord.app_commands.guilds(discord.Object(id=GUILD_ID))
    async def start_mapvote(self, interaction: discord.Interaction):
        self.votes = {}  # reset votes

        channel = self.bot.get_channel(MAPVOTE_CHANNEL_ID)
        if channel is None:
            await interaction.response.send_message("Map vote channel not found", ephemeral=True)
            return

        # Build buttons for each map
        view = discord.ui.View(timeout=VOTE_DURATION)
        for map_name in MAPS:
            button = discord.ui.Button(label=map_name, style=discord.ButtonStyle.primary)
            
            async def callback(interact, map_name=map_name):
                self.votes[interact.user.id] = map_name
                await interact.response.send_message(f"You voted for {map_name}", ephemeral=True)
            
            button.callback = callback
            view.add_item(button)

        await channel.send("🗺️ **Vote for the next map!**", view=view)
        await interaction.response.send_message("Map vote started!", ephemeral=True)

        # Wait for vote duration
        await asyncio.sleep(VOTE_DURATION)
        if self.votes:
            # Count votes
            from collections import Counter
            counter = Counter(self.votes.values())
            winner = counter.most_common(1)[0][0]
            send_rcon(f'switchmap "{winner}"')
            await channel.send(f"🏆 Map vote ended! Winning map: **{winner}**")
        else:
            await channel.send("No votes were cast.")

# ---------------- SETUP ----------------
async def setup(bot):
    await bot.add_cog(MapVote(bot))
