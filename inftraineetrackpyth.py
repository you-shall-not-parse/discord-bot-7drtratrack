# trainee_tracker.py
import discord
from discord.ext import commands
from datetime import datetime, timedelta
import os

class TraineeTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.INFANTRY_ROLE_ID = 1099596178141757542
        self.SUPPORT_ROLE_ID = 1100005693546844242
        self.ENGINEER_ROLE_ID = 1100005700106719312
        self.RECRUITFORM_CHANNEL_ID = 1098331019364552845
        self.TRACKING_CHANNEL_ID = 1368543744193990676
        self.CHANNEL_IDS = {
            "Training Sign-ups": 1097984129854869515,
            "Comp Match Sign-ups": 1101226715763720272,
            "Friday Event Sign-ups": 1317153335354327060
        }
        self.trainee_data = {}
        self.trainee_messages = {}

    # put your @bot.event methods as @commands.Cog.listener() here

async def setup(bot):
    await bot.add_cog(TraineeTracker(bot))

