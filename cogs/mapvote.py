import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import os
import random
import requests
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

load_dotenv()

# --------------------------------------------------
# CONFIG
# --------------------------------------------------

GUILD_ID = 1097913605082579024
MAPVOTE_CHANNEL_ID = 1441751747935735878

# How early voting stops
VOTE_END_OFFSET_SECONDS = 120  

# For testing: update every second
EMBED_UPDATE_INTERVAL = 1   

OPTIONS_PER_VOTE = 10  

MAPS = {
    "Elsenborn Ridge Warfare": "elsenbornridge_warfare_day",
    "Carentan Warfare": "carentan_warfare",
    "Foy Warfare": "foy_warfare",
    "Hill 400 Warfare": "hill400_warfare",
}

# MUST match CRCON pretty_name EXACTLY
MAP_CDN_IMAGES = {
    "Elsenborn Ridge Warfare": "https://cdn.discordapp.com/.../elsenborn.png",
    "Carentan Warfare": "https://cdn.discordapp.com/.../carentan.png",
    "Foy Warfare": "https://cdn.discordapp.com/.../foy.png",
    "Hill 400 Warfare": "https://cdn.discordapp.com/.../hill400.png",
}

BROADCAST_START = "Map vote is OPEN on Discord!"
BROADCAST_ENDING_SOON = "Map vote closes in 2 minutes!"
BROADCAST_NO_VOTES = "No votes, the map rotation wins :("

# --------------------------------------------------
# CRCON API
# --------------------------------------------------

CRCON_PANEL_URL = "https://7dr.hlladmin.com/api/"
CRCON_API_KEY = os.getenv("CRCON_API_KEY")

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
# HELPERS
# --------------------------------------------------

async def get_gamestate():
    data = rcon_get("get_gamestate")

    if not data or data.get("failed"):
        return None

    res = data.get("result") or {}
    current = res.get("current_map") or {}

    return {
        "current_map_id": current.get("id"),
        "current_pretty": current.get("pretty_name"),
        "image_name": current.get("image_name"),
        "time_remaining": float(res.get("time_remaining") or 0),
        "raw_time_remaining": res.get("raw_time_remaining") or "0:00:00",
        "match_time": int(res.get("match_time", 5400)),
        "axis_players": int(res.get("num_axis_players", 0)),
        "allied_players": int(res.get("num_allied_players", 0)),
    }

async def broadcast_ingame(msg):
    if not msg:
        return
    return rcon_post("broadcast", {"message": msg})

async def rot_add_map(map_name, after_map_name, after_ordinal=1):
    methods = [
        ("rot_add", {
            "map_name": map_name,
            "after_map_name": after_map_name,
            "after_map_ordinal": after_ordinal
        }),
        ("run_command", {
            "command": "RotAdd",
            "arguments": {
                "map_name": map_name,
                "after_map_name": after_map_name,
                "after_map_ordinal": after_ordinal
            }
        }),
        ("command", {
            "command": "RotAdd",
            "arguments": {
                "map_name": map_name,
                "after_map_name": after_map_name,
                "after_map_ordinal": after_ordinal
            }
        }),
    ]

    for endpoint, payload in methods:
        r = rcon_post(endpoint, payload)
        if r and not r.get("failed") and "error" not in r:
            return r
    return {"error": "rot_add failed"}

def fmt_secs(s):
    s = max(0, int(s))
    m = s // 60
    s2 = s % 60
    return f"{m:02d}:{s2:02d}"

# --------------------------------------------------
# STATE
# --------------------------------------------------

class VoteState:
    def __init__(self):
        self.active = False
        self.vote_channel = None
        self.vote_message_id = None

        self.match_map_id = None
        self.match_pretty = None
        self.vote_end_at = None
        self.warning_sent = False

        self.options = {}
        self.user_votes = {}
        self.vote_counts = {}

    def reset(self, gs):
        self.active = True
        self.vote_message_id = None
        self.match_map_id = gs["current_map_id"]
        self.match_pretty = gs["current_pretty"]
        self.warning_sent = False
        self.user_votes.clear()
        self.vote_counts.clear()

        tr = gs["time_remaining"]
        if tr > 0:
            end_in = tr - VOTE_END_OFFSET_SECONDS
        else:
            end_in = gs["match_time"] - VOTE_END_OFFSET_SECONDS

        now = datetime.now(timezone.utc)
        self.vote_end_at = now + timedelta(seconds=end_in)

    def set_options(self, opts):
        self.options = opts

    def add_vote(self, user_id, map_id):
        prev = self.user_votes.get(user_id)
        if prev:
            self.vote_counts[prev] -= 1
            if self.vote_counts[prev] <= 0:
                del self.vote_counts[prev]

        self.user_votes[user_id] = map_id
        self.vote_counts[map_id] = self.vote_counts.get(map_id, 0) + 1

    def winner(self):
        if not self.vote_counts:
            return None
        return max(self.vote_counts.items(), key=lambda kv: kv[1])[0]

# --------------------------------------------------
# UI
# --------------------------------------------------

class MapSelect(discord.ui.Select):
    def __init__(self, state, cog):
        self.state = state
        self.cog = cog

        opts = [
            discord.SelectOption(label=pretty, value=mapid)
            for pretty, mapid in state.options.items()
        ]

        super().__init__(
            placeholder="Vote for next map…",
            min_values=1,
            max_values=1,
            options=opts
        )

    async def callback(self, interaction: discord.Interaction):
        if not self.state.active:
            return await interaction.response.send_message("Voting is not active.", ephemeral=True)

        map_id = self.values[0]
        self.state.add_vote(interaction.user.id, map_id)

        await interaction.response.send_message("Vote recorded!", ephemeral=True)
        await self.cog.update_embed()

class MapView(discord.ui.View):
    def __init__(self, state, cog):
        super().__init__(timeout=None)
        self.add_item(MapSelect(state, cog))

# --------------------------------------------------
# MAIN COG
# --------------------------------------------------

class MapVote(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.state = VoteState()
        self.last_map = None
        self.tick.start()

    def cog_unload(self):
        self.tick.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        guild = discord.Object(id=GUILD_ID)
        await self.bot.tree.sync(guild=guild)
        print("[MapVote] synced.")

    # ------------------------------------------------------
    # Slash Command – FORCE vote start
    # ------------------------------------------------------
    @app_commands.command(name="force_mapvote", description="Force start map vote now.")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def forcevote(self, interaction: discord.Interaction):
        await interaction.response.send_message("Starting vote…", ephemeral=True)
        gs = await get_gamestate()
        if not gs:
            return await interaction.followup.send("Could not read gamestate.", ephemeral=True)

        await self.start_vote(gs)
        await interaction.followup.send("Vote created!", ephemeral=True)

    # ------------------------------------------------------
    # Embed Builder
    # ------------------------------------------------------
    def make_embed(self, gs):
        pretty = gs["current_pretty"]
        raw = gs["raw_time_remaining"]

        # player detection
        empty = (gs["axis_players"] + gs["allied_players"] == 0)

        now = datetime.now(timezone.utc)
        if self.state.vote_end_at:
            secs = (self.state.vote_end_at - now).total_seconds()
        else:
            secs = 0

        # Live vote list
        if self.state.vote_counts:
            txt = []
            for map_id, cnt in sorted(self.state.vote_counts.items(), key=lambda x: x[1], reverse=True):
                pretty_name = next((k for k, v in MAPS.items() if v == map_id), map_id)
                txt.append(f"**{pretty_name}** — {cnt} votes")
            votes = "\n".join(txt)
        else:
            votes = "*No votes yet.*"

        desc = (
            f"**Current map:** {pretty}\n"
            f"**Match time remaining:** `{raw}`\n"
            f"**Vote closes in:** `{fmt_secs(secs)}`\n\n"
        )

        if empty:
            desc += "**⚠️ Server empty — vote paused.**\n\n"

        desc += f"**Live Votes:**\n{votes}"

        embed = discord.Embed(
            title="🗺️ Next Map Vote",
            description=desc,
            color=discord.Color.red()
        )

        img = MAP_CDN_IMAGES.get(pretty)
        if img:
            embed.set_image(url=img)

        return embed

    # ------------------------------------------------------
    # Update Embed
    # ------------------------------------------------------
    async def update_embed(self):
        if not self.state.vote_channel or not self.state.vote_message_id:
            return

        gs = await get_gamestate()
        if not gs:
            return

        msg = await self.state.vote_channel.fetch_message(self.state.vote_message_id)
        await msg.edit(embed=self.make_embed(gs), view=MapView(self.state, self))

    # ------------------------------------------------------
    # Start Vote
    # ------------------------------------------------------
    async def start_vote(self, gs):
        channel = self.bot.get_channel(MAPVOTE_CHANNEL_ID)
        if not channel:
            return

        self.state.vote_channel = channel
        self.state.reset(gs)

        # Build option list
        pool = [(pretty, mid) for pretty, mid in MAPS.items() if mid != gs["current_map_id"]]
        random.shuffle(pool)
        pool = pool[:min(25, OPTIONS_PER_VOTE)]
        self.state.set_options({p: m for p, m in pool})

        view = MapView(self.state, self)
        embed = self.make_embed(gs)

        # cleanup
        async for m in channel.history(limit=20):
            if m.author == self.bot.user:
                await m.delete()

        msg = await channel.send(embed=embed, view=view)
        self.state.vote_message_id = msg.id

        await broadcast_ingame(BROADCAST_START)

    # ------------------------------------------------------
    # End vote
    # ------------------------------------------------------
    async def end_vote(self):
        self.state.active = False

        winner = self.state.winner()
        ch = self.state.vote_channel

        if not winner:
            await ch.send("🏁 No votes — rotation wins.")
            await broadcast_ingame(BROADCAST_NO_VOTES)
            return

        pretty = next((k for k, v in MAPS.items() if v == winner), winner)
        cur = self.state.match_map_id

        result = await rot_add_map(winner, cur, 1)
        await broadcast_ingame(f"{pretty} has won the vote!")

        await ch.send(
            f"🏆 **Vote closed! Winner: {pretty}**\n"
            f"Queued via RotAdd after `{cur}`.\n\n"
            f"```{result}```"
        )

    # ------------------------------------------------------
    # Main Loop (Every second)
    # ------------------------------------------------------
    @tasks.loop(seconds=EMBED_UPDATE_INTERVAL)
    async def tick(self):
        gs = await get_gamestate()
        if not gs:
            return

        # Detect new match
        if self.last_map and gs["current_map_id"] != self.last_map:
            await self.start_vote(gs)

        self.last_map = gs["current_map_id"]

        if not self.state.active:
            return

        # Update embed
        await self.update_embed()

        # End vote?
        now = datetime.now(timezone.utc)
        if self.state.vote_end_at:
            secs = (self.state.vote_end_at - now).total_seconds()

            if secs <= 120 and not self.state.warning_sent:
                self.state.warning_sent = True
                await broadcast_ingame(BROADCAST_ENDING_SOON)
                await self.state.vote_channel.send("⏳ Vote closes in 2 minutes!")

            if secs <= 0:
                await self.end_vote()

    @tick.before_loop
    async def before_tick(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(MapVote(bot))
