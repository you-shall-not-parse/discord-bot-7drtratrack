import discord
from discord.ext import commands
import socket
import asyncio

# --------------------------------------------------
# CONFIG
# --------------------------------------------------
RCON_HOST = "176.57.140.181"      # your server IP
RCON_PORT = 30216               # your RCON port
RCON_PASSWORD = "bedcc53"
MAPVOTE_CHANNEL_ID = 1441751747935735878

POLL_DURATION = 60  # Seconds

# Map timers (example values — update with yours)
MAP_TIMERS = {
    "Foy Offensive US": 1800,
    "Foy Offensive GER": 900,
    "Utah Beach Warfare": 2500,
    "SME Warfare": 400,
    "Kursk Warfare": 6000,
}


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
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((RCON_HOST, RCON_PORT))

        # Authenticate
        s.sendall(f"password {RCON_PASSWORD}\n".encode("utf-8"))
        auth_reply = s.recv(4096).decode()

        if "accepted" not in auth_reply.lower():
            s.close()
            return False, f"Auth failed: {auth_reply}"

        # Send command
        s.sendall(f"{command}\n".encode("utf-8"))
        result = s.recv(4096).decode()

        s.close()
        return True, result

    except Exception as e:
        return False, str(e)


# --------------------------------------------------
# MAP VOTE COG
# --------------------------------------------------
class MapVote(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Helper to display timers
    def format_time(self, seconds):
        if seconds < 60:
            return f"{seconds}s ago"
        m = seconds // 60
        h = m // 60
        if h > 0:
            return f"{h}h {m % 60}m ago"
        return f"{m}m ago"

    @commands.command(name="mapvote")
    async def mapvote(self, ctx):
        """Starts a map vote using Discord's native poll system."""

        # Build poll answers with timers
        answers = []
        for mapname, seconds in MAP_TIMERS.items():
            timer_text = self.format_time(seconds)
            answers.append(discord.PollAnswer(text=f"{mapname} — ⏱️ {timer_text}"))

        poll = discord.Poll(
            question="🗺️ Vote for the next map!",
            duration=POLL_DURATION,
            allow_multiselect=False,
            answers=answers
        )

        # --------------------------------------------------
        # ALWAYS POST TO SPECIFIC CHANNEL
        # --------------------------------------------------
        channel = ctx.guild.get_channel(MAPVOTE_CHANNEL_ID)
        if channel is None:
            return await ctx.send(f"❌ Cannot find map vote channel `{MAPVOTE_CHANNEL_ID}`")

        # Send poll
        msg = await channel.send(poll=poll)
        await channel.send(f"🗳️ **Map vote open for {POLL_DURATION} seconds!**")

        # Wait until poll closes
        await asyncio.sleep(POLL_DURATION + 2)

        # Re-fetch message to get closed poll results
        msg = await channel.fetch_message(msg.id)
        poll_obj = msg.poll

        if not poll_obj:
            return await channel.send("❌ Could not read poll results (Discord API issue).")

        # Determine winner
        winner = max(poll_obj.answers, key=lambda a: a.vote_count)
        clean_name = winner.text.split(" —")[0]

        # Run RCON command
        success, reply = send_rcon(f'switchmap "{clean_name}"')

        if success:
            await channel.send(
                f"🏆 Winning Map: **{clean_name}**\n"
                f"✔️ Map switched successfully!"
            )
        else:
            await channel.send(
                f"🏆 Winning Map: **{clean_name}**\n"
                f"❌ RCON Error: `{reply}`"
            )


# --------------------------------------------------
def setup(bot):
    bot.add_cog(MapVote(bot))
