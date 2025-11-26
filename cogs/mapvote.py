# ---IMAGE FIX INCLUDED---

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
# CONFIG YOU EDIT
# --------------------------------------------------

GUILD_ID = 1097913605082579024
MAPVOTE_CHANNEL_ID = 1441751747935735878

VOTE_END_OFFSET_SECONDS = 120
EMBED_UPDATE_INTERVAL = 5
OPTIONS_PER_VOTE = 10

MAPS = {
    "Elsenborn Ridge Warfare": "elsenbornridge_warfare_day",
    "Carentan Warfare": "carentan_warfare",
    "Foy Warfare": "foy_warfare",
    "Hill 400 Warfare": "hill400_warfare",
}

# CDN IMAGES — OPTIONAL
MAP_CDN_IMAGES = {
    "Elsenborn Ridge Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365408158012407840/Elsenborn_Custom_MLL.png",
    "Carentan Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365403110197166191/Carentan_SP_NoHQ.png",
    "Foy Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365404141337186304/Foy_SP_NoHQ.png",
    "Hill 400 Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365404269116919930/Hill400_SP_NoHQ_1.png",
}

BROADCAST_START = "test"
BROADCAST_ENDING_SOON = "test"
BROADCAST_NO_VOTES = "No votes, the map rotation wins :("

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


async def get_gamestate():
    data = rcon_get("get_gamestate")

    if (
        not data
        or data.get("failed")
        or (isinstance(data.get("error"), str) and data.get("error"))
    ):
        print("[MapVote] Could not read gamestate:", data)
        return None

    res = data.get("result") or {}
    current_map = res.get("current_map") or {}

    return {
        "current_map_id": current_map.get("id"),
        "current_map_pretty": current_map.get("pretty_name"),
        "current_image_name": current_map.get("image_name"),
        "time_remaining": float(res.get("time_remaining") or 0.0),
        "raw_time_remaining": res.get("raw_time_remaining") or "0:00:00",
        "match_time": int(res.get("match_time") or 0),
        "next_map_id": (res.get("next_map") or {}).get("id"),
        "game_mode": res.get("game_mode", "unknown"),
    }


async def broadcast_ingame(message: str):
    if not message:
        return None
    return rcon_post("broadcast", {"message": message})


async def rot_add_map(map_name: str, after_map_name: str, after_map_ordinal: int = 1):
    try_methods = [
        ("rot_add", {
            "map_name": map_name,
            "after_map_name": after_map_name,
            "after_map_ordinal": after_map_ordinal
        }),
        ("run_command", {
            "command": "RotAdd",
            "arguments": {
                "map_name": map_name,
                "after_map_name": after_map_name,
                "after_map_ordinal": after_map_ordinal
            }
        }),
        ("command", {
            "command": "RotAdd",
            "arguments": {
                "map_name": map_name,
                "after_map_name": after_map_name,
                "after_map_ordinal": after_map_ordinal
            }
        }),
    ]

    last_err = None
    for endpoint, payload in try_methods:
        result = rcon_post(endpoint, payload)
        if result and "error" not in result and not result.get("failed"):
            return result
        last_err = result

    return last_err or {"error": "rot_add failed"}


def fmt_vote_secs(seconds):
    if seconds is None:
        return "Unknown"
    seconds = max(0, int(seconds))
    return f"{seconds//60:02d}:{seconds%60:02d}"


# --------------------------------------------------
# VOTE STATE
# --------------------------------------------------

class VoteState:
    def __init__(self):
        self.active = False
        self.vote_channel = None
        self.vote_message_id = None
        self.match_map_id = None
        self.match_map_pretty = None
        self.vote_start_at = None
        self.vote_end_at = None
        self.warning_sent = False
        self.options_pretty_to_id = {}
        self.user_votes = {}
        self.vote_counts = {}

    def reset_for_new_match(self, gs):
        self.active = True
        self.vote_message_id = None
        self.vote_channel = None
        self.match_map_id = gs["current_map_id"]
        self.match_map_pretty = gs["current_map_pretty"]

        now = datetime.now(timezone.utc)
        self.vote_start_at = now
        self.warning_sent = False
        self.user_votes.clear()
        self.vote_counts.clear()

        tr = float(gs["time_remaining"])
        if tr > 0:
            end_in = max(0, tr - VOTE_END_OFFSET_SECONDS)
        else:
            mt = int(gs.get("match_time", 5400))
            end_in = max(0, mt - VOTE_END_OFFSET_SECONDS)

        self.vote_end_at = now + timedelta(seconds=end_in)

    def set_options(self, mapping):
        self.options_pretty_to_id = mapping

    def set_user_vote(self, user_id, map_id):
        old = self.user_votes.get(user_id)
        if old == map_id:
            return

        if old:
            self.vote_counts[old] = max(0, self.vote_counts.get(old, 1) - 1)
            if self.vote_counts[old] == 0:
                del self.vote_counts[old]

        self.user_votes[user_id] = map_id
        self.vote_counts[map_id] = self.vote_counts.get(map_id, 0) + 1

    def winner_map_id(self):
        if not self.vote_counts:
            return None
        return max(self.vote_counts.items(), key=lambda kv: kv[1])[0]


# --------------------------------------------------
# SELECT MENU
# --------------------------------------------------

class MapVoteSelect(discord.ui.Select):
    def __init__(self, state: VoteState, cog):
        self.state = state
        self.cog = cog

        options = [
            discord.SelectOption(label=p, value=id, description="Vote for this map")
            for p, id in state.options_pretty_to_id.items()
        ]

        super().__init__(
            placeholder="Vote for the next map…",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        if not self.state.active:
            return await interaction.response.send_message("Vote is not active.", ephemeral=True)

        chosen = self.values[0]
        self.state.set_user_vote(interaction.user.id, chosen)

        await interaction.response.send_message(
            f"🗳️ Vote recorded for `{chosen}`.", ephemeral=True
        )

        await self.cog.update_vote_embed()


class MapVoteView(discord.ui.View):
    def __init__(self, state: VoteState, cog):
        super().__init__(timeout=None)
        self.add_item(MapVoteSelect(state, cog))


# --------------------------------------------------
# MAIN COG
# --------------------------------------------------

class MapVote(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.state = VoteState()
        self.last_map_id = None
        self.tick_task.start()

    def cog_unload(self):
        self.tick_task.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        guild = discord.Object(id=GUILD_ID)
        try:
            await self.bot.tree.sync(guild=guild)
            print("[MapVote] Commands synced.")
        except Exception as e:
            print("[MapVote] Sync error:", e)

    # ------------------------------------------------------
    # /force_mapvote
    # ------------------------------------------------------
    @app_commands.command(
        name="force_mapvote",
        description="Force start a new map vote now."
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def force_mapvote(self, interaction: discord.Interaction):
        await interaction.response.send_message("Starting vote…", ephemeral=True)

        gs = await get_gamestate()
        if not gs:
            return await interaction.followup.send("❌ Could not read gamestate.", ephemeral=True)

        await self.start_new_vote_for_match(gs)
        await interaction.followup.send("Vote created.", ephemeral=True)

    # ------------------------------------------------------
    # EMBED BUILDER — FIXED WITH CRCON IMAGE FALLBACK
    # ------------------------------------------------------
    def build_embed(self, gs):
        current_pretty = gs["current_map_pretty"] or "Unknown"
        raw_remaining = gs["raw_time_remaining"]

        now = datetime.now(timezone.utc)
        if self.state.vote_end_at:
            vote_remaining = max(0, (self.state.vote_end_at - now).total_seconds())
        else:
            vote_remaining = None

        # vote display
        if self.state.vote_counts:
            sorted_votes = sorted(self.state.vote_counts.items(), key=lambda x: x[1], reverse=True)
            vote_lines = []
            for map_id, count in sorted_votes:
                pretty = next((n for n, mid in MAPS.items() if mid == map_id), map_id)
                vote_lines.append(f"**{pretty}** — {count} vote(s)")
            votes_text = "\n".join(vote_lines)
        else:
            votes_text = "*No votes yet.*"

        embed = discord.Embed(
            title="🗺️ Next Map Vote",
            description=(
                f"**Current map:** {current_pretty}\n"
                f"**Match remaining:** `{raw_remaining}`\n"
                f"**Vote closes in:** `{fmt_vote_secs(vote_remaining)}`\n\n"
                f"**Live votes:**\n{votes_text}"
            ),
            color=discord.Color.red()
        )

        # --------------------------------------------------
        # IMAGE FIX — CDN FIRST → CRCON STATIC FALLBACK
        # --------------------------------------------------
        img_url = MAP_CDN_IMAGES.get(current_pretty)

        if not img_url and gs["current_image_name"]:
            img_url = f"https://7dr.hlladmin.com/static/maps/{gs['current_image_name']}"

        if img_url:
            embed.set_image(url=img_url)

        embed.set_footer(text="Winner is added to next rotation.")
        return embed

    async def update_vote_embed(self):
        if not self.state.vote_channel or not self.state.vote_message_id:
            return

        gs = await get_gamestate()
        if not gs:
            return

        try:
            msg = await self.state.vote_channel.fetch_message(self.state.vote_message_id)
            await msg.edit(
                embed=self.build_embed(gs),
                view=MapVoteView(self.state, self)
            )
        except Exception as e:
            print("[MapVote] Failed updating embed:", e)

    # ------------------------------------------------------
    # MATCH START → CREATE NEW VOTE
    # ------------------------------------------------------
    async def start_new_vote_for_match(self, gs):
        channel = self.bot.get_channel(MAPVOTE_CHANNEL_ID)
        if not channel:
            print("[MapVote] vote channel not found")
            return

        self.state.vote_channel = channel
        self.state.reset_for_new_match(gs)

        # build option pool
        pool = [(p, id) for p, id in MAPS.items() if id != gs["current_map_id"]]
        random.shuffle(pool)
        pool = pool[: min(len(pool), OPTIONS_PER_VOTE, 25)]

        self.state.set_options({p: id for p, id in pool})

        embed = self.build_embed(gs)
        view = MapVoteView(self.state, self)

        # clear old bot messages
        try:
            async for m in channel.history(limit=50):
                if m.author == self.bot.user:
                    await m.delete()
        except:
            pass

        msg = await channel.send(embed=embed, view=view)
        self.state.vote_message_id = msg.id

        if BROADCAST_START:
            await broadcast_ingame(BROADCAST_START)

    # ------------------------------------------------------
    # END VOTE
    # ------------------------------------------------------
    async def end_vote_and_queue_winner(self):
        self.state.active = False
        channel = self.state.vote_channel
        if not channel:
            return

        winner = self.state.winner_map_id()
        if not winner:
            await channel.send("🏁 Vote closed — no votes.")
            await broadcast_ingame(BROADCAST_NO_VOTES)
            return

        pretty = next((p for p, id in MAPS.items() if id == winner), winner)

        result = await rot_add_map(winner, self.state.match_map_id, 1)

        await broadcast_ingame(f"{pretty} has won the vote!")

        await channel.send(
            f"🏆 **Winner:** {pretty}\n"
            f"Queued next.\n\n"
            f"```{result}```"
        )

    # ------------------------------------------------------
    # BACKGROUND LOOP
    # ------------------------------------------------------
    @tasks.loop(seconds=EMBED_UPDATE_INTERVAL)
    async def tick_task(self):
        gs = await get_gamestate()
        if not gs:
            return

        current_map = gs["current_map_id"]

        if self.last_map_id and current_map != self.last_map_id:
            print(f"[MapVote] New match detected: {gs['current_map_pretty']}")
            await self.start_new_vote_for_match(gs)
            self.last_map_id = current_map
            return

        self.last_map_id = current_map

        if not self.state.active:
            return

        await self.update_vote_embed()

        if self.state.vote_end_at:
            now = datetime.now(timezone.utc)
            remaining = (self.state.vote_end_at - now).total_seconds()

            if remaining <= 120 and not self.state.warning_sent:
                self.state.warning_sent = True
                if BROADCAST_ENDING_SOON:
                    await broadcast_ingame(BROADCAST_ENDING_SOON)
                await self.state.vote_channel.send("⏳ Vote closes in **2 minutes**!")

            if remaining <= 0:
                await self.end_vote_and_queue_winner()

    @tick_task.before_loop
    async def before_tick(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(MapVote(bot))
