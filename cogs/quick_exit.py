import discord
from discord.ext import commands

# ================== CONFIG ==================

LEAVE_CHANNEL_ID = 1097913605539774484  # 👈 replace with your channel ID

LEAVE_MESSAGE = "🚪 {display} ({name}) has left the server!"

# ================== COG ==================

class QuickExit(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        channel = self.bot.get_channel(LEAVE_CHANNEL_ID)
        if not channel:
            return

        message = LEAVE_MESSAGE.format(
            display=member.display_name,
            name=member.name
        )

        await channel.send(message)

# ================== SETUP ==================

async def setup(bot: commands.Bot):
    await bot.add_cog(QuickExit(bot))