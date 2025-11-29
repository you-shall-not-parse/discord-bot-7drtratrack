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

# Vote ends this many seconds before match end when > 90m
VOTE_END_OFFSET_SECONDS = 120

# Embed update speed (testing = 1 sec)
EMBED_UPDATE_INTERVAL = 1

# How many map options to show
OPTIONS_PER_VOTE = 10

# Pretty name → CRCON ID
MAPS = {
    "Elsenborn Ridge Warfare": "elsenbornridge_warfare_day",
    "Carentan Warfare": "carentan_warfare",
    "Foy Warfare": "foy_warfare",
    "Hill 400 Warfare": "hill400_warfare",
}

# CDN images by pretty_name
MAP_CDN_IMAGES = {
    "Elsenborn Ridge Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365408158012407840/Elsenborn_Custom_MLL.png",
    "Carentan Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365403110197166191/Carentan_SP_NoHQ.png",
    "Foy Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365404141337186304/Foy_SP_NoHQ.png",
    "Hill 400 Warfare": "https://cdn.discordapp.com/attachments/1365401621110067281/1365404269116919930/Hill400_SP_NoHQ_1.png",
}

# Broadcast templates
BROADCAST_START = "🗳️ Next-map voting is OPEN on Discord!"
BROADCAST_ENDING_SOON = "⏳ Vote closes in 2 minutes!"
BROADCAST_NO_VOTES = "No votes, the map rotation wins :("

# --------------------------------------------------
# CRCON API (Bearer token)
# --------------------------------------------------

CRCON_PANEL_URL = "https://7dr.hlladmin.com/api/"
CRCON_API_KEY = os.getenv("CRCON_API_KEY")


def rcon_get(endpoint: str):
    # CRCON GET endpoints exist but most things use POST
    try:
        r = requests.get(
            CRCON_PANEL_URL + endpoint,
            headers={"Authorization": f"Bearer {CRCON_API_KEY}"},
            timeout=10
        )
        return r.json()
    except Exception as e:
        print(f"[MapVote] rcon_get error on {endpoint}: {e}")
        return {"error": str(e)}


def rcon_post(endpoint: str, payload: dict = None):
    if payload is None:
        payload = {}

    try:
        r = requests.post(
            CRCON_PANEL_URL + endpoint,
            json=payload,
            headers={"Authorization": f"Bearer {CRCON_API_KEY}"},
            timeout=10
        )
        try:
            return r.json()
        except Exception:
            return {"status": r.status_code, "text": r.text or ""}
    except Exception as e:
        print(f"[MapVote] rcon_post error on {endpoint}: {e}")
        return {"error": str(e)}


# CRCON wrappers
async def rcon_get_players():
    return rcon_post("get_players")


async def rcon_message_player(uid: str, message: str):
    payload = {
        "player": uid,
        "steam_id_64": uid,
        "message": message
    }
    return rcon_post("do_message_player", payload)


async def get_gamestate():
    data = rcon_post("get_gamestate")
    if not data or data.get("error") or data.get("failed"):
        print("[MapVote] Gamestate read failed:", data)
        return None

    res = data.get("result") or {}
    cur = res.get("current_map") or {}

    try:
        return {
            "current_map_id": cur.get("id"),
            "current_map_pretty": cur.get("pretty_name"),
            "current_image_name": cur.get("image_name"),
            "time_remaining": float(res.get("time_remaining") or 0),
            "raw_time_remaining": res.get("raw_time_remaining") or "0:00:00",
            "match_time": int(res.get("match_time") or 0),
            "axis_players": int(res.get("num_axis_players") or 0),
            "allied_players": int(res.get("num_allied_players") or 0),
        }
    except Exception as e:
        print("[MapVote] Error parsing gamestate:", e, data)
        return None


async def rot_add_map(map_name: str, after_map_name: str, ordinal=1):
    payload = {
        "map_name": map_name,
        "after_map_name": after_map_name,
        "after_map_name_number": ordinal
    }
    return rcon_post("add_map_to_rotation", payload)


def fmt_vote_secs(sec):
    if sec is None:
        return "Unknown"
    sec = max(0, int(sec))
    m = sec // 60
    s = sec % 60
    return f"{m:02d}:{s:02d}"


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

        self.options = {}
        self.user_votes = {}
        self.vote_counts = {}

        self.total_match_length = None

    def reset_for_match(self, gs):
        self.active = True
        self.vote_message_id = None
        self.vote_channel = None

        self.match_map_id = gs["current_map_id"]
        self.match_map_pretty = gs["current_map_pretty"]

        now = datetime.now(timezone.utc)
        tr = float(gs["time_remaining"] or 0)
        mt = int(gs["match_time"] or 0)

        total_len = tr + mt if (tr > 0 or mt > 0) else None
        self.total_match_length = int(total_len) if total_len else None

        # Closing logic as per your original code
        if tr > 0:
            if self.total_match_length and self.total_match_length < 5400:
                end_in = max(0, tr - 120)
            else:
                end_in = max(0, tr - VOTE_END_OFFSET_SECONDS)
        else:
            if mt > 0:
                end_in = max(0, mt - VOTE_END_OFFSET_SECONDS)
            else:
                end_in = max(0, VOTE_END_OFFSET_SECONDS)

        self.vote_start_at = now
        self.vote_end_at = now + timedelta(seconds=end_in)
        self.warning_sent = False

        self.user_votes.clear()
        self.vote_counts.clear()

    def set_options(self, mapping):
        self.options = mapping

    def record_vote(self, user_id, map_id):
        old = self.user_votes.get(user_id)
        if old == map_id:
            return

        if old:
            self.vote_counts[old] = max(0, self.vote_counts.get(old, 1) - 1)
            if self.vote_counts[old] == 0:
                self.vote_counts.pop(old, None)

        self.user_votes[user_id] = map_id
        self.vote_counts[map_id] = self.vote_counts.get(map_id, 0) + 1

    def winner(self):
        if not self.vote_counts:
            return None
        return max(self.vote_counts.items(), key=lambda kv: kv[1])[0]


# --------------------------------------------------
# UI ELEMENTS
# --------------------------------------------------

class MapVoteSelect(discord.ui.Select):
    def __init__(self, vote_state, cog_ref):
        self.state = vote_state
        self.cog = cog_ref

        options = [
            discord.SelectOption(label=pretty, value=map_id)
            for pretty, map_id in vote_state.options.items()
        ]

        super().__init__(
            placeholder="Vote for next map…",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction):
        if not self.state.active:
            return await interaction.response.send_message(
                "Voting not active.",
                ephemeral=True
            )

        map_id = self.values[0]
        self.state.record_vote(interaction.user.id, map_id)

        await interaction.response.send_message(
            f"Vote recorded for `{map_id}`",
            ephemeral=True
        )

        await self.cog.update_vote_embed()


class MapVoteView(discord.ui.View):
    def __init__(self, state, cog):
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
        self.vote_view = None

    def cog_unload(self):
        if self.tick_task.is_running():
            self.tick_task.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        try:
            await self.bot.tree.sync(guild=discord.Object(id=GUILD_ID))
            print("[MapVote] Commands synced.")
        except Exception as e:
            print("[MapVote] Sync error:", e)

        if not self.tick_task.is_running():
            self.tick_task.start()
            print("[MapVote] tick_task started")

    # --------------------------------------------------
    # MESSAGE ALL PLAYERS
    # --------------------------------------------------
    async def broadcast_to_all(self, message: str):
        if not message:
            return

        data = await rcon_get_players()
        if not data or data.get("error") or data.get("failed"):
            print("[MapVote] broadcast_to_all get_players failed:", data)
            return

        players = data.get("result") or []
        if not players:
            return

        for p in players:
            uid = p.get("steam_id_64")
            if not uid:
                continue

            await rcon_message_player(uid, message)
            await asyncio.sleep(0.1)

    # --------------------------------------------------
    # FORCE START SLASH COMMAND
    # --------------------------------------------------
    @app_commands.command(name="force_mapvote", description="Force start a map vote")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def force_mapvote_cmd(self, interaction):
        await interaction.response.send_message("Fetching gamestate…", ephemeral=True)
        gs = await get_gamestate()
        if not gs:
            return await interaction.followup.send("❌ Could not read gamestate.", ephemeral=True)

        await self.start_vote(gs)
        await interaction.followup.send("Vote started!", ephemeral=True)

    # --------------------------------------------------
    # BUILD EMBED
    # --------------------------------------------------
    def build_embed(self, gs):
        current = gs["current_map_pretty"] or "Unknown"
        raw_time = gs["raw_time_remaining"]

        axis = gs["axis_players"]
        allied = gs["allied_players"]

        now = datetime.now(timezone.utc)
        if self.state.vote_end_at:
            vote_left = (self.state.vote_end_at - now).total_seconds()
        else:
            vote_left = None

        if self.state.total_match_length:
            total_m = self.state.total_match_length // 60
            total_s = self.state.total_match_length % 60
            length_line = f"**Match length (detected):** `{total_m:02d}:{total_s:02d}`\n"
        else:
            length_line = ""

        if self.state.vote_counts:
            sorted_votes = sorted(self.state.vote_counts.items(), key=lambda x: x[1], reverse=True)
            lines = []
            for map_id, count in sorted_votes:
                pretty = next((p for p, mid in MAPS.items() if mid == map_id), map_id)
                lines.append(f"**{pretty}** — {count} vote{'s' if count != 1 else ''}")
            votetext = "\n".join(lines)
        else:
            votetext = "*No votes yet.*"

        embed = discord.Embed(
            title="🗺️ Next Map Vote",
            description=(
                f"**Current map:** {current}\n"
                f"**Match remaining:** `{raw_time}`\n"
                f"{length_line}"
                f"**Players:** Allied: `{allied}` — Axis: `{axis}`\n"
                f"**Vote closes in:** `{fmt_vote_secs(vote_left)}`\n\n"
                f"**Live votes:**\n{votetext}"
            ),
            color=discord.Color.red()
        )

        img = MAP_CDN_IMAGES.get(current)
        if img:
            embed.set_image(url=img)

        return embed

    # --------------------------------------------------
    # UPDATE EMBED
    # --------------------------------------------------
    async def update_vote_embed(self):
        if not (self.state.vote_channel and self.state.vote_message_id):
            return

        gs = await get_gamestate()
        if not gs:
            return

        try:
            msg = await self.state.vote_channel.fetch_message(self.state.vote_message_id)

            if self.vote_view is None:
                self.vote_view = MapVoteView(self.state, self)

            await msg.edit(embed=self.build_embed(gs), view=self.vote_view)
        except Exception as e:
            print("[MapVote] Failed to update embed:", e)

    # --------------------------------------------------
    # START A NEW VOTE
    # --------------------------------------------------
    async def start_vote(self, gs):
        channel = self.bot.get_channel(MAPVOTE_CHANNEL_ID)
        if not channel or not isinstance(channel, discord.TextChannel):
            print("[MapVote] Vote channel invalid")
            return

        self.state.reset_for_match(gs)
        self.state.vote_channel = channel

        pool = [(p, mid) for p, mid in MAPS.items() if mid != gs["current_map_id"]]
        random.shuffle(pool)
        pool = pool[:min(len(pool), OPTIONS_PER_VOTE, 25)]
        self.state.set_options({p: mid for p, mid in pool})

        try:
            async for m in channel.history(limit=50):
                if m.author == self.bot.user:
                    await m.delete()
        except Exception as e:
            print("[MapVote] Failed to clean messages:", e)

        embed = self.build_embed(gs)
        self.vote_view = MapVoteView(self.state, self)
        msg = await channel.send(embed=embed, view=self.vote_view)
        self.state.vote_message_id = msg.id

        print(f"[MapVote] Vote started for {gs['current_map_pretty']}")
        await self.broadcast_to_all(BROADCAST_START)

    # --------------------------------------------------
    # END VOTE
    # --------------------------------------------------
    async def end_vote_and_queue(self):
        self.state.active = False
        channel = self.state.vote_channel

        if not channel:
            print("[MapVote] end_vote_and_queue called with no channel")
            return

        winner_id = self.state.winner()

        if not winner_id:
            await channel.send("No votes — map rotation continues.")
            await self.broadcast_to_all(BROADCAST_NO_VOTES)
            return

        pretty = next((p for p, mid in MAPS.items() if mid == winner_id), winner_id)

        result = await rot_add_map(winner_id, self.state.match_map_id, 1)

        await self.broadcast_to_all(f"{pretty} has won the vote!")
        await channel.send(
            f"🏆 **Winner: {pretty}**\n"
            f"Queued next via RotAdd.\n"
            f"CRCON Response:\n```{result}```"
        )
        print(f"[MapVote] Vote ended, winner {pretty}")

    # --------------------------------------------------
    # TICK LOOP
    # --------------------------------------------------
    @tasks.loop(seconds=EMBED_UPDATE_INTERVAL)
    async def tick_task(self):
        gs = await get_gamestate()
        if not gs:
            return

        # Detect map change
        if self.last_map_id is not None and gs["current_map_id"] != self.last_map_id:
            print(f"[MapVote] New match detected: {gs['current_map_pretty']}")
            await self.start_vote(gs)
            self.last_map_id = gs["current_map_id"]
            return

        if self.last_map_id is None:
            self.last_map_id = gs["current_map_id"]

        if not self.state.active:
            return

        await self.update_vote_embed()

        now = datetime.now(timezone.utc)
        remaining = (self.state.vote_end_at - now).total_seconds() if self.state.vote_end_at else None

        if remaining is None:
            return

        if remaining <= 120 and not self.state.warning_sent:
            self.state.warning_sent = True
            await self.broadcast_to_all(BROADCAST_ENDING_SOON)
            if self.state.vote_channel:
                await self.state.vote_channel.send("⏳ Vote closes in 2 minutes!")

        if remaining <= 0:
            await self.end_vote_and_queue()

    @tick_task.before_loop
    async def before_tick(self):
        print("[MapVote] Waiting until bot is ready before starting tick_task...")
        await self.bot.wait_until_ready()
        print("[MapVote] Bot ready, tick_task will now run.")


async def setup(bot):
    await bot.add_cog(MapVote(bot))
