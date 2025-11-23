import discord
from discord.ext import commands
import requests
import asyncio
import time

# ------------------------------------------
# CONFIG
# ------------------------------------------
RCON_URL = "http://your-rcon-proxy:8000/api/command"
RCON_PASSWORD = "your_rcon_password"

MAP_TIMERS = {
    "Foy Offensive US": 1800,
    "Foy Offensive GER": 900,
    "Utah Beach Warfare": 2500,
    "SME Warfare": 400,
    "Kursk Warfare": 6000,
}

POLL_DURATION = 60  # seconds poll stays open


# ------------------------------------------
# RCON HELPER
# ------------------------------------------
def send_rcon(command: str):
    try:
        payload = {
            "password": RCON_PASSWORD,
            "command": command
        }
        r = requests.post(RCON_URL, json=payload, timeout=5)
        if r.status_code == 200:
            return True, r.json() if r.text else "OK"
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)


# ------------------------------------------
# THE COG
# ------------------------------------------
class MapVote(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ----------------------------------------------------
    # /mapvote — starts a poll-based map vote
    # ----------------------------------------------------
    @commands.command(name="mapvote")
    async def mapvote(self, ctx):
        """Starts a Discord Poll for map voting."""

        # ---- Build poll options ----
        poll_options = []
        for mapname, last_played_seconds in MAP_TIMERS.items():
            readable_time = self.format_time(last_played_seconds)
            poll_options.append(
                discord.PollAnswer(text=f"{mapname} — ⏱️ {readable_time}")
            )

        poll = discord.Poll(
            question="🗺️ Vote for the next map!",
            duration=POLL_DURATION,
            allow_multiselect=False,
            answers=poll_options
        )

        # ---- Send the poll ----
        msg = await ctx.send(poll=poll)
        await ctx.send(f"🗳️ Poll active for **{POLL_DURATION} seconds**!")

        # ---- Wait for poll to end ----
        await asyncio.sleep(POLL_DURATION + 2)

        # ---- Fetch the poll results ----
        msg = await msg.channel.fetch_message(msg.id)
        poll_object = msg.poll

        if not poll_object:
            return await ctx.send("❌ Poll data missing — Discord did not attach a poll object.")

        # ---- Determine winner ----
        top_answer = max(poll_object.answers, key=lambda a: a.vote_count)
        selected_text = top_answer.text.split(" —")[0]
        winner_map = selected_text

        # ---- Execute RCON ----
        success, response = send_rcon(f'switchmap "{winner_map}"')

        if success:
            await ctx.send(f"🏆 **Winning map:** {winner_map}\n✔️ Map switched successfully!")
        else:
            await ctx.send(f"🏆 **Winning map:** {winner_map}\n❌ RCON failed: `{response}`")

    # ----------------------------------------------------
    # Helper: format seconds into natural readable text
    # ----------------------------------------------------
    def format_time(self, seconds):
        if seconds < 60:
            return f"{seconds}s ago"

        minutes = seconds // 60
        hours = minutes // 60

        if hours > 0:
            return f"{hours}h {minutes % 60}m ago"
        return f"{minutes}m ago"


# ----------------------------------------------------
# SETUP
# ----------------------------------------------------
def setup(bot):
    bot.add_cog(MapVote(bot))
