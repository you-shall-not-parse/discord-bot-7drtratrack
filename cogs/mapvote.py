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

# Debug logging toggle
DEBUG_LOG = True

# Vote ends this many seconds before match end (when timers work)
VOTE_END_OFFSET_SECONDS = 120

# How often to refresh the embed (seconds)
EMBED_UPDATE_INTERVAL = 60  # 60 seconds = 1 minute

# How many options to show each vote (dropdown max is 25)
OPTIONS_PER_VOTE = 10  # set up to 25

# Map pool (Pretty name -> CRCON map id)
MAPS = {
    "Elsenborn Ridge Warfare": "elsenbornridge_warfare_day",
    "Carentan Warfare": "carentan_warfare",
    "Foy Warfare": "foy_warfare",
    "Hill 400 Warfare": "hill400_warfare",
    # add more here
}

# ---------------- Broadcast texts (you fill these) ----------------
BROADCAST_START = "Next-map voting is OPEN on Discord! Use the map-vote channel to pick the next fight."
BROADCAST_ENDING_SOON = "Next-map vote closes in 2 minutes! Make sure your vote is in."
BROADCAST_NO_VOTES = "No votes, the map rotation wins :("

# Winner broadcast is FIXED by your rule:
# "<winning map> has won the vote!"

# --------------------------------------------------
# CRCON API WRAPPER (Bearer token)
# --------------------------------------------------

CRCON_PANEL_URL = "https://7dr.hlladmin.com/api/"
CRCON_API_KEY = os.getenv("CRCON_API_KEY")


def dbg(msg: str):
    if DEBUG_LOG:
        print(f"[MapVote] {msg}")


def rcon_get(endpoint: str):
    try:
        r = requests.get(
            CRCON_PANEL_URL + endpoint,
            headers={"Authorization": f"Bearer {CRCON_API_KEY}"},
            timeout=10
        )
        return r.json()
    except Exception as e:
        dbg(f"rcon_get error on {endpoint}: {e}")
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
        dbg(f"rcon_post error on {endpoint}: {e}")
        return {"error": str(e)}

# --------------------------------------------------
# HELPERS
# --------------------------------------------------


async def get_gamestate():
    """Get current map + time remaining safely from CRCON."""
    data = rcon_get("get_gamestate")

    # Only treat non-null errors as real errors
    if (
        not data
        or data.get("failed")
        or (isinstance(data.get("error"), str) and data.get("error"))
    ):
        dbg(f"Could not read gamestate: {data}")
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
        "axis_players": int(res.get("num_axis_players") or 0),
        "allied_players": int(res.get("num_allied_players") or 0),
    }


async def broadcast_ingame(message: str):
    """Hosted CRCON uses /api/broadcast."""
    if not message:
        return None
    dbg(f"Broadcasting ingame: {message}")
    return rcon_post("broadcast", {"message": message})


async def rot_add_map(map_name: str, after_map_name: str, after_map_ordinal: int = 1):
    """
    Add map immediately after current map without resetting rotation.
    Try multiple CRCON endpoint styles.
    """
    try_methods = [
        ("rot_add", {
            "map_name": map_name,
            "after_map_name": after_map_name,
            "after_map_ordinal": after_map_ordinal
        }),
        # Fallback generic runner if host provides it:
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
        dbg(f"Trying RotAdd via {endpoint} with payload {payload}")
        result = rcon_post(endpoint, payload)
        if result and "error" not in result and not result.get("failed"):
            dbg(f"RotAdd success via {endpoint}: {result}")
            return result
        dbg(f"RotAdd attempt via {endpoint} failed: {result}")
        last_err = result

    return last_err or {"error": "rot_add failed"}


def fmt_vote_secs(seconds: float | None):
    if seconds is None:
        return "Unknown"
    seconds = int(seconds)
    if seconds <= 0:
        return "0:00"
    if seconds < 60:
        return "<1:00"
    m = seconds // 60
    s = seconds % 60
    return f"{m:02d}:{s:02d}"

# --------------------------------------------------
# VOTE STATE
# --------------------------------------------------


class VoteState:
    def __init__(self):
        self.active = False

        self.vote_channel: discord.abc.Messageable | None = None
        self.vote_message_id: int | None = None

        self.match_map_id: str | None = None
        self.match_map_pretty: str | None = None

        self.vote_start_at: datetime | None = None
        self.vote_end_at: datetime | None = None
        self.warning_sent: bool = False

        self.options_pretty_to_id: dict[str, str] = {}
        self.user_votes: dict[int, str] = {}   # user_id -> map_id
        self.vote_counts: dict[str, int] = {}  # map_id -> count

    def reset_for_new_match(self, gs: dict):
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

        # If CRCON gives real remaining time, use it.
        # If 0, we still schedule off match_time as a best-effort.
        tr = float(gs["time_remaining"])
        if tr > 0:
            end_in = max(0, tr - VOTE_END_OFFSET_SECONDS)
        else:
            mt = int(gs.get("match_time", 5400))
            end_in = max(0, mt - VOTE_END_OFFSET_SECONDS)

        self.vote_end_at = now + timedelta(seconds=end_in)
        dbg(f"VoteState reset: match_map_id={self.match_map_id}, vote_end_at={self.vote_end_at}")

    def set_options(self, options_pretty_to_id: dict[str, str]):
        self.options_pretty_to_id = options_pretty_to_id
        dbg(f"Vote options set: {options_pretty_to_id}")

    def set_user_vote(self, user_id: int, map_id: str):
        old = self.user_votes.get(user_id)
        if old == map_id:
            return

        if old:
            self.vote_counts[old] = max(0, self.vote_counts.get(old, 1) - 1)
            if self.vote_counts[old] == 0:
                del self.vote_counts[old]

        self.user_votes[user_id] = map_id
        self.vote_counts[map_id] = self.vote_counts.get(map_id, 0) + 1
        dbg(f"User {user_id} voted for {map_id}. Counts: {self.vote_counts}")

    def winner_map_id(self):
        if not self.vote_counts:
            return None
        # stable winner: highest votes, ties resolved by first encountered
        winner = max(self.vote_counts.items(), key=lambda kv: kv[1])[0]
        dbg(f"Winner map id computed: {winner}")
        return winner

# --------------------------------------------------
# DROPDOWN UI
# --------------------------------------------------


class MapVoteSelect(discord.ui.Select):
    def __init__(self, vote_state: VoteState, cog_ref: "MapVote"):
        self.vote_state = vote_state
        self.cog_ref = cog_ref

        options = [
            discord.SelectOption(
                label=pretty,
                value=map_id,
                description="Vote for this map"
            )
            for pretty, map_id in vote_state.options_pretty_to_id.items()
        ]

        super().__init__(
            placeholder="Vote for the next map…",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        if not self.vote_state.active:
            return await interaction.response.send_message("Vote is not active.", ephemeral=True)

        chosen_map_id = self.values[0]
        self.vote_state.set_user_vote(interaction.user.id, chosen_map_id)

        await interaction.response.send_message(
            f"🗳️ Vote recorded for `{chosen_map_id}`. You can change it any time.",
            ephemeral=True
        )

        # Force immediate embed update (no waiting for the next tick)
        await self.cog_ref.update_vote_embed(force=True)


class MapVoteView(discord.ui.View):
    def __init__(self, vote_state: VoteState, cog_ref: "MapVote"):
        super().__init__(timeout=None)
        self.add_item(MapVoteSelect(vote_state, cog_ref))

# --------------------------------------------------
# MAIN COG
# --------------------------------------------------


class MapVote(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.state = VoteState()

        self.last_map_id: str | None = None

        self.tick_task.start()

    def cog_unload(self):
        self.tick_task.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        guild = discord.Object(id=GUILD_ID)
        try:
            await self.bot.tree.sync(guild=guild)
            dbg("Commands synced.")
        except Exception as e:
            dbg(f"Sync error: {e}")

    # ------------------------------------------------------
    # MANUAL SLASH COMMAND – FORCE START A MAP VOTE
    # ------------------------------------------------------
    @app_commands.command(
        name="force_mapvote",
        description="Force start a new map vote using the current match state."
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def force_mapvote(self, interaction: discord.Interaction):
        await interaction.response.send_message("⏳ Starting map vote…", ephemeral=True)

        gs = await get_gamestate()
        if not gs:
            return await interaction.followup.send("❌ Could not read gamestate.", ephemeral=True)

        await self.start_new_vote_for_match(gs)
        await interaction.followup.send("✅ Map vote created in the vote channel.", ephemeral=True)

    # ---------- EMBED ----------
    def build_embed(self, gs: dict, server_empty: bool) -> discord.Embed:
        current_pretty = gs["current_map_pretty"] or "Unknown"
        raw_remaining = gs["raw_time_remaining"] or "0:00:00"

        now = datetime.now(timezone.utc)
        vote_remaining = None
        if self.state.vote_end_at:
            vote_remaining = max(0, (self.state.vote_end_at - now).total_seconds())

        # live vote summary (only maps with votes)
        if self.state.vote_counts and self.state.active and not server_empty:
            sorted_votes = sorted(
                self.state.vote_counts.items(),
                key=lambda kv: kv[1],
                reverse=True
            )
            lines = []
            for map_id, count in sorted_votes:
                pretty = next((k for k, v in MAPS.items() if v == map_id), map_id)
                lines.append(f"**{pretty}** — {count} vote(s)")
            votes_text = "\n".join(lines)
        else:
            votes_text = "*No votes yet.*"

        status_lines = []
        if server_empty:
            status_lines.append("⚠️ **Server is empty – map voting is currently inactive.**")
        elif not self.state.active:
            status_lines.append("⚠️ **Map voting is not active for this match.**")

        status_block = "\n".join(status_lines)
        if status_block:
            status_block = "\n" + status_block + "\n"

        embed = discord.Embed(
            title="🗺️ Next Map Vote",
            description=(
                f"**Current map:** {current_pretty}\n"
                f"**Match remaining:** `{raw_remaining}`\n"
                f"**Vote closes in:** `{fmt_vote_secs(vote_remaining)}` (2 mins before end)\n"
                f"{status_block}\n"
                f"Vote in the dropdown below. You can change vote any time.\n\n"
                f"**Live votes:**\n{votes_text}"
            ),
            color=discord.Color.red()
        )

        # Map image from CRCON image_name -> panel static path
        image_name = gs.get("current_image_name")
        if image_name:
            img_url = f"https://7dr.hlladmin.com/static/img/maps/{image_name}"
            embed.set_image(url=img_url)

        embed.set_footer(text="Vote runs during the match. Winner is queued as next map.")
        return embed

    async def update_vote_embed(self, force: bool = False):
        if not self.state.vote_channel or not self.state.vote_message_id:
            return

        gs = await get_gamestate()
        if not gs:
            return

        axis_players = gs.get("axis_players", 0)
        allied_players = gs.get("allied_players", 0)
        server_empty = (axis_players == 0 and allied_players == 0)

        try:
            msg = await self.state.vote_channel.fetch_message(self.state.vote_message_id)
            embed = self.build_embed(gs, server_empty)

            # Hide dropdown if server empty or voting inactive
            if self.state.active and not server_empty:
                view = MapVoteView(self.state, self)
            else:
                view = None

            await msg.edit(embed=embed, view=view)
            dbg("Embed updated.")
        except Exception as e:
            dbg(f"Failed updating embed: {e}")

    # ---------- START NEW MATCH VOTE ----------
    async def start_new_vote_for_match(self, gs: dict):
        channel = self.bot.get_channel(MAPVOTE_CHANNEL_ID)
        if not channel:
            dbg("vote channel not found")
            return

        # Check server emptiness at start
        axis_players = gs.get("axis_players", 0)
        allied_players = gs.get("allied_players", 0)
        server_empty = (axis_players == 0 and allied_players == 0)

        self.state.vote_channel = channel
        self.state.reset_for_new_match(gs)

        # options pool (exclude current map)
        pool = [(pretty, mid) for pretty, mid in MAPS.items() if mid != gs["current_map_id"]]
        random.shuffle(pool)

        take = min(OPTIONS_PER_VOTE, 25, len(pool))
        pool = pool[:take]
        options_dict = {pretty: mid for pretty, mid in pool}
        self.state.set_options(options_dict)

        view = MapVoteView(self.state, self) if not server_empty else None
        embed = self.build_embed(gs, server_empty)

        # tidy old bot messages
        try:
            async for m in channel.history(limit=50):
                if m.author == self.bot.user:
                    await m.delete()
        except Exception:
            pass

        msg = await channel.send(embed=embed, view=view)
        self.state.vote_message_id = msg.id
        dbg(f"New vote message sent: id={msg.id}")

        # announce start if server not empty
        if BROADCAST_START and not server_empty:
            await broadcast_ingame(BROADCAST_START)

    # ---------- END VOTE & QUEUE WINNER ----------
    async def end_vote_and_queue_winner(self):
        self.state.active = False
        channel = self.state.vote_channel
        if not channel:
            return

        winner_id = self.state.winner_map_id()

        # no votes -> do nothing to rotation
        if not winner_id:
            await channel.send("🏁 Vote closed — no votes were cast.")
            await broadcast_ingame(BROADCAST_NO_VOTES)
            dbg("Vote ended: no votes.")
            return

        # Insert winner after CURRENT map (A behaviour)
        current_id = self.state.match_map_id
        result = await rot_add_map(winner_id, current_id, 1)

        pretty = next((k for k, v in MAPS.items() if v == winner_id), winner_id)

        # broadcast winner wording fixed by your rule
        await broadcast_ingame(f"{pretty} has won the vote!")

        await channel.send(
            f"🏆 **Vote closed!**\n"
            f"Winner: **{pretty}**\n"
            f"Queued next via RotAdd after `{current_id}`.\n\n"
            f"📡 CRCON response:\n```{result}```"
        )
        dbg(f"Vote ended: winner={pretty}, CRCON result={result}")

    # ---------- BACKGROUND LOOP ----------
    @tasks.loop(seconds=EMBED_UPDATE_INTERVAL)
    async def tick_task(self):
        gs = await get_gamestate()
        if not gs:
            return

        current_map_id = gs["current_map_id"]
        axis_players = gs.get("axis_players", 0)
        allied_players = gs.get("allied_players", 0)
        server_empty = (axis_players == 0 and allied_players == 0)

        # Detect new match by map change (most reliable given timer bug)
        if self.last_map_id and current_map_id != self.last_map_id:
            dbg(f"New match detected: {gs['current_map_pretty']}")
            # Only auto-start vote if server isn't empty
            if not server_empty:
                await self.start_new_vote_for_match(gs)
            self.last_map_id = current_map_id
            return

        self.last_map_id = current_map_id

        if not self.state.active:
            # Still update embed if we have one, to show current map & empty-server status
            if self.state.vote_channel and self.state.vote_message_id:
                await self.update_vote_embed()
            return

        # Update embed live (once per interval)
        await self.update_vote_embed()

        # Handle warnings / end based on vote_end_at
        if self.state.vote_end_at:
            now = datetime.now(timezone.utc)
            remaining_vote = (self.state.vote_end_at - now).total_seconds()

            # 2-minute warning once
            if remaining_vote <= 120 and not self.state.warning_sent and not server_empty:
                self.state.warning_sent = True
                try:
                    if BROADCAST_ENDING_SOON:
                        await broadcast_ingame(BROADCAST_ENDING_SOON)
                    await self.state.vote_channel.send("⏳ **Next-map vote closes in 2 minutes!**")
                except Exception as e:
                    dbg(f"Failed sending 2-minute warning: {e}")

            # End vote
            if remaining_vote <= 0:
                await self.end_vote_and_queue_winner()

    @tick_task.before_loop
    async def before_tick(self):
        await self.bot.wait_until_ready()
        dbg("tick_task starting after bot ready.")


async def setup(bot):
    await bot.add_cog(MapVote(bot))
