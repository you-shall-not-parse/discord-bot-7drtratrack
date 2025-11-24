import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import os
import requests
from dotenv import load_dotenv

load_dotenv()

# -----------------------------
# RCON WEB API (simple wrapper)
# -----------------------------
RCON_HOST = os.getenv("RCON_HOST")
RCON_PORT = os.getenv("RCON_PORT")
RCON_API_KEY = os.getenv("RCON_API_KEY")

BASE_URL = f"http://{RCON_HOST}:{RCON_PORT}/api/"

def rcon_post(endpoint, payload):
    try:
        r = requests.post(
            BASE_URL + endpoint,
            json=payload,
            headers={"x-api-key": RCON_API_KEY},
            timeout=5
        )
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def rcon_get(endpoint):
    try:
        r = requests.get(
            BASE_URL + endpoint,
            headers={"x-api-key": RCON_API_KEY},
            timeout=5
        )
        return r.json()
    except Exception as e:
        return {"error": str(e)}

# -----------------------------
# CONFIG (kept inside cog)
# -----------------------------
GUILD_ID = 1097913605082579024
MAPVOTE_CHANNEL_ID = 1441751747935735878
VOTE_DURATION_SECONDS = 30

MAPS = {
    "Elsenborn": "elsenbornridge_warfare_day",
}

MAP_TIMERS = {
    "Elsenborn": "06:30",
}

async def get_current_map():
    data = rcon_get("get_current_map")
    return data.get("result", {}).get("pretty_name", "Unknown")

async def set_map(map_id: str):
    methods = [
        ("set_map", {"map_name": map_id}),
        ("set_map_rotation", {"map_names": [map_id]})
    ]
    for ep, payload in methods:
        result = rcon_post(ep, payload)
        if "error" not in result:
            return f"[{ep}] {result}"
    return result


# -----------------------------
# VOTE BUTTON VIEW
# -----------------------------
class VoteButton(discord.ui.Button):
    def __init__(self, label, parent_view):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        user = interaction.user.name
        choice = self.label.split(" ⏱️")[0]

        # Record vote
        self.parent_view.votes[user] = choice

        # Update tally display
        await interaction.response.edit_message(
            content=self.parent_view.generate_vote_message(),
            view=self.parent_view
        )


class VoteView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=VOTE_DURATION_SECONDS)
        self.votes = {}  # {username: map_name}

        # Build buttons for each map
        for pretty_name in MAPS.keys():
            button_label = f"{pretty_name} ⏱️ {MAP_TIMERS[pretty_name]}"
            self.add_item(VoteButton(button_label, self))

    def generate_vote_message(self):
        # Count votes
        tally = {}
        for choice in self.votes.values():
            tally[choice] = tally.get(choice, 0) + 1

        # Build readable status
        lines = ["🗳️ **Current Votes:**"]
        if tally:
            for map_name, count in tally.items():
                lines.append(f"• **{map_name}** — {count} votes")
        else:
            lines.append("No votes yet.")

        return "\n".join(lines)


# -----------------------------
# MAIN COG
# -----------------------------
class MapVote(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        try:
            await self.bot.tree.sync(guild=discord.Object(id=GUILD_ID))
            print("[MapVote] Slash commands synced")
        except Exception as e:
            print("Sync error:", e)

    @app_commands.command(name="start_mapvote", description="Start a button-based vote for next map.")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def start_mapvote(self, interaction: discord.Interaction):

        channel = self.bot.get_channel(MAPVOTE_CHANNEL_ID)
        if channel is None:
            return await interaction.response.send_message("❌ Map vote channel not found!", ephemeral=True)

        await interaction.response.send_message("Map vote started!", ephemeral=True)

        current = await get_current_map()

        view = VoteView()

        # Send initial vote message
        msg = await channel.send(
            f"🗺️ **Vote for the next map!**\nCurrent Map: **{current}**\n\n"
            f"Click a button below to vote!",
            view=view
        )

        # Wait for timeout
        await view.wait()

        # Compute final results
        tally = {}
        for choice in view.votes.values():
            tally[choice] = tally.get(choice, 0) + 1

        if not tally:
            return await channel.send("⚠️ No votes were cast.")

        winner = max(tally, key=tally.get)
        winner_map_id = MAPS[winner]

        result = await set_map(winner_map_id)

        await channel.send(
            f"🏆 **Voting complete!**\n"
            f"Winner: **{winner}** ({tally[winner]} votes)\n"
            f"Map ID: `{winner_map_id}`\n\n"
            f"📡 **RCON Response:**\n```{result}```"
        )


async def setup(bot):
    await bot.add_cog(MapVote(bot))
