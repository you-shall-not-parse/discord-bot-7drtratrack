import discord
from discord.ext import commands
import socket
import asyncio
import time

# --------------------------------------------------
# CONFIG
# --------------------------------------------------
RCON_HOST = "176.57.140.181"      # your server IP
RCON_PORT = 30216               # your RCON port
RCON_PASSWORD = "yourPassword"

MAP_TIMERS = {
    "Foy Offensive US": 1800,
    "Foy Offensive GER": 900,
    "Utah Beach Warfare": 2500,
    "SME Warfare": 400,
    "Kursk Warfare": 6000,
}

POLL_DURATION = 60  # seconds


# --------------------------------------------------
# RAW TCP RCON FUNCTION
# --------------------------------------------------
def send_rcon(command: str):
    """
    Sends a raw TCP RCON command to a Hell Let Loose server.
    Protocol:
      1) connect TCP
      2) send: 'password <pass>\n'
      3) send: '<command>\n'
    """
    try:
        # open socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((RCON_HOST, RCON_PORT))

        # authenticate
        s.sendall(f"password {RCON_PASSWORD}\n".encode("utf-8"))
        auth_reply = s.recv(4096).decode()

        if "accepted" not in auth_reply.lower():
            s.close()
            return False, f"Auth failed: {auth_reply}"

        # send command
        s.sendall(f"{command}\n".encode("utf-8"))
        result = s.recv(4096).decode()

        s.close()
        return True, result

    except Exception as e:
        return False, str(e)


# --------------------------------------------------
# COG — POLL BASED MAP VOTING
# --------------------------------------------------
class MapVote(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="mapvote")
    async def mapvote(self, ctx):
        """Starts a map vote using Discord's native poll system."""

        # Build Poll Answers with timer text
        answers = []
        for mapname, seconds in MAP_TIMERS.items():
            timer = self.format_time(seconds)
            answers.append(discord.PollAnswer(text=f"{mapname} — ⏱️ {timer}"))

        # Create poll
        poll = discord.Poll(
            question="🗺️ Vote for the next map!",
            duration=POLL_DURATION,
            allow_multiselect=False,
            answers=answers
        )

        # Send poll
        msg = await ctx.send(poll=poll)
        await ctx.send(f"🗳️ **Map vote open for {POLL_DURATION} seconds!**")

        # Wait for Discord to close the poll
        await asyncio.sleep(POLL_DURATION + 2)

        # Re-fetch message to get the closed poll results
        msg = await msg.channel.fetch_message(msg.id)
        poll_obj = msg.poll

        if not poll_obj:
            return await ctx.send("❌ Could not read poll results (Discord API issue).")

        # Determine winning answer
        winner = max(poll_obj.answers, key=lambda a: a.vote_count)
        clean_name = winner.text.split(" —")[0]

        # Execute RCON command
        success, reply = send_rcon(f'switchmap "{clean_name}"')

        if success:
            await ctx.send(f"🏆 Winning Map: **{clean_name}**\n✔️ Map switched successfully!")
        else:
            await ctx.send(f"🏆 Winning Map: **{clean_name}**\n❌ RCON Error: `{reply}`")

    # --------------------------
    # Time formatting
    # --------------------------
    def format_time(self, seconds):
        if seconds < 60:
            return f"{seconds}s ago"
        minutes = seconds // 60
        hours = minutes // 60
        if hours > 0:
            return f"{hours}h {minutes % 60}m ago"
        return f"{minutes}m ago"


def setup(bot):
    bot.add_cog(MapVote(bot))
