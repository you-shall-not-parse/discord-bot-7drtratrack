
# trainee_tracker.py
import discord
from discord.ext import commands
from datetime import datetime, timedelta

class TraineeTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.GUILD_ID = 1097913605082579024
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

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"Logged in as {self.bot.user}")
        guild = self.bot.get_guild(self.GUILD_ID)
        track_channel = self.bot.get_channel(self.TRACKING_CHANNEL_ID)
        if not guild or not track_channel:
            print("Guild or tracking channel not found.")
            return

        for member in guild.members:
            if any(role.id == self.INFANTRY_ROLE_ID for role in member.roles):
                nickname = member.display_name
                profile_name = member.name
                join_date = member.joined_at or datetime.utcnow()
                joined_plus_2_weeks = join_date + timedelta(days=14)
                has_support = any(role.id == self.SUPPORT_ROLE_ID for role in member.roles)
                has_engineer = any(role.id == self.ENGINEER_ROLE_ID for role in member.roles)
                self.trainee_data[nickname] = {
                    "profile_name": profile_name,
                    "join_date": join_date,
                    "joined_plus_2_weeks": joined_plus_2_weeks,
                    "has_support": has_support,
                    "has_engineer": has_engineer,
                    "recruitform_posted": False,
                    "left_server": False,
                    "graduated": False,
                    "graduation_date": None,
                    "signups": {ch: 0 for ch in self.CHANNEL_IDS}
                }

        recruitform_channel = self.bot.get_channel(self.RECRUITFORM_CHANNEL_ID)
        if recruitform_channel:
            async for message in recruitform_channel.history(limit=1000):
                if not message.author.bot and message.author.display_name in self.trainee_data:
                    self.trainee_data[message.author.display_name]["recruitform_posted"] = True

        for channel_name, channel_id in self.CHANNEL_IDS.items():
            channel = self.bot.get_channel(channel_id)
            if channel:
                async for message in channel.history(limit=500):
                    if message.author.bot:
                        continue
                    if message.embeds:
                        for embed in message.embeds:
                            if not embed.description:
                                continue
                            for trainee in self.trainee_data:
                                if trainee in embed.description:
                                    self.trainee_data[trainee]["signups"][channel_name] += 1
                    else:
                        for trainee in self.trainee_data:
                            if trainee in message.content:
                                self.trainee_data[trainee]["signups"][channel_name] += 1

        await track_channel.purge(limit=100, check=lambda m: m.author == self.bot.user)

        sorted_trainees = sorted(self.trainee_data.items(), key=lambda x: x[1]['join_date'])
        for nickname, data in sorted_trainees:
            embed = self.generate_report_embed(nickname)
            msg = await track_channel.send(embed=embed)
            self.trainee_messages[nickname] = msg.id

        summary = self.generate_summary_and_legend_embed(sorted_trainees)
        await track_channel.send(embed=summary)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        nickname = message.author.display_name
        track_channel = self.bot.get_channel(self.TRACKING_CHANNEL_ID)
        if message.channel.id == self.RECRUITFORM_CHANNEL_ID:
            if nickname in self.trainee_messages:
                self.trainee_data[nickname]["recruitform_posted"] = True
                await self.update_trainee_embed(nickname, track_channel)
        elif message.channel.id in self.CHANNEL_IDS.values():
            for trainee in self.trainee_messages:
                if trainee in message.content:
                    channel_name = next((name for name, cid in self.CHANNEL_IDS.items() if cid == message.channel.id), None)
                    if channel_name:
                        self.trainee_data[trainee]["signups"][channel_name] += 1
                        await self.update_trainee_embed(trainee, track_channel)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        nickname = after.display_name
        track_channel = self.bot.get_channel(self.TRACKING_CHANNEL_ID)
        if not track_channel:
            return

        gained_infantry_role = self.INFANTRY_ROLE_ID not in [r.id for r in before.roles] and \
                               self.INFANTRY_ROLE_ID in [r.id for r in after.roles]

        if nickname not in self.trainee_data and gained_infantry_role:
            join_date = after.joined_at or datetime.utcnow()
            self.trainee_data[nickname] = {
                "profile_name": after.name,
                "join_date": join_date,
                "joined_plus_2_weeks": join_date + timedelta(days=14),
                "has_support": any(role.id == self.SUPPORT_ROLE_ID for role in after.roles),
                "has_engineer": any(role.id == self.ENGINEER_ROLE_ID for role in after.roles),
                "recruitform_posted": False,
                "left_server": False,
                "graduated": False,
                "graduation_date": None,
                "signups": {ch: 0 for ch in self.CHANNEL_IDS}
            }

            embed = self.generate_report_embed(nickname)
            msg = await track_channel.send(embed=embed)
            self.trainee_messages[nickname] = msg.id

            async for m in track_channel.history(limit=50):
                if m.author == self.bot.user and m.embeds:
                    if m.embeds[0].title and "Trainee Tracker: Legend & Summary" in m.embeds[0].title:
                        await m.delete()
                        break

            sorted_trainees = sorted(self.trainee_data.items(), key=lambda x: x[1]['join_date'])
            summary = self.generate_summary_and_legend_embed(sorted_trainees)
            await track_channel.send(embed=summary)

        if nickname not in self.trainee_data:
            return

        was_trainee = self.INFANTRY_ROLE_ID in [r.id for r in before.roles]
        is_trainee = self.INFANTRY_ROLE_ID in [r.id for r in after.roles]

        if was_trainee and not is_trainee:
            self.trainee_data[nickname]["graduated"] = True
            self.trainee_data[nickname]["graduation_date"] = datetime.utcnow()

        self.trainee_data[nickname]["has_support"] = any(role.id == self.SUPPORT_ROLE_ID for role in after.roles)
        self.trainee_data[nickname]["has_engineer"] = any(role.id == self.ENGINEER_ROLE_ID for role in after.roles)

        await self.update_trainee_embed(nickname, track_channel)
        await self.update_existing_summary_message(track_channel)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        nickname = member.display_name
        if nickname in self.trainee_data:
            self.trainee_data[nickname]["left_server"] = True
            track_channel = self.bot.get_channel(self.TRACKING_CHANNEL_ID)
            await self.update_trainee_embed(nickname, track_channel)

    def generate_report_embed(self, nickname):
        data = self.trainee_data[nickname]
        embed = discord.Embed(
            title=f"Trainee Tracker: {nickname}",
            color=discord.Color.blue()
        )
        embed.add_field(name="Profile", value=data["profile_name"], inline=True)
        embed.add_field(name="Join Date", value=data["join_date"].strftime('%Y-%m-%d'), inline=True)
        embed.add_field(name="+14 Days", value=data["joined_plus_2_weeks"].strftime('%Y-%m-%d'), inline=True)

        embed.add_field(name="Support Role", value="✅" if data["has_support"] else "❌", inline=True)
        embed.add_field(name="Engineer Role", value="✅" if data["has_engineer"] else "❌", inline=True)
        embed.add_field(name="Recruit Form Posted", value="✅" if data["recruitform_posted"] else "❌", inline=True)

        embed.add_field(name="Training Signups", value=str(data["signups"]["Training Sign-ups"]), inline=True)
        embed.add_field(name="Comp Match Signups", value=str(data["signups"]["Comp Match Sign-ups"]), inline=True)
        embed.add_field(name="Friday Event Signups", value=str(data["signups"]["Friday Event Sign-ups"]), inline=True)

        embed.add_field(name="Graduated", value="✅" if data["graduated"] else "❌", inline=True)
        if data["graduated"] and data["graduation_date"]:
        embed.add_field(name="Graduation Date", value=data["graduation_date"].strftime('%Y-%m-%d'), inline=True)

        embed.add_field(name="Left Server", value="✅" if data["left_server"] else "❌", inline=True)

        embed.set_footer(text="Auto-generated trainee report")
        return embed


    def generate_summary_and_legend_embed(self, trainees_sorted):
        if nickname in trainee_messages:
            try:
                msg = await track_channel.fetch_message(trainee_messages[nickname])
                embed = generate_report_embed(nickname)
                await msg.edit(embed=embed)
            except discord.NotFound:
                pass

    async def update_trainee_embed(self, nickname, track_channel):
        summary = {
            "Behind": [],
            "On-Track": [],
            "Ready to Graduate": [],
            "Graduated": []
        }

        for nickname, data in trainees_sorted:
            joined_days_ago = (datetime.utcnow().replace(tzinfo=None) - data['join_date'].replace(tzinfo=None)).days
            if data["graduated"]:
                summary["Graduated"].append(nickname)
            elif data["has_support"] and data["has_engineer"] and joined_days_ago >= 28:
                summary["Ready to Graduate"].append(nickname)
            elif data["has_support"] or data["has_engineer"] or joined_days_ago <= 14:
                summary["On-Track"].append(nickname)
            else:
                summary["Behind"].append(nickname)

        embed = discord.Embed(title="**Trainee Tracker: Legend & Summary**", color=discord.Color.blurple())

        # Legend section
        embed.add_field(name="Legend", value=(
            "🟪 **Purple** — Ready to Graduate! Has both roles AND 2+ weeks, amazing! \n"
            "🟩 **Green** — Has both Support and Engineer but not done 2 weeks yet, great\n"
            "🟦 **Blue** — Has one of Support or Engineer, good \n"
            "⬛ **Grey** — No roles but under 2 weeks, not bad\n"
            "🟧 **Orange** — No roles and in server over 4 weeks, bad\n"
            "🎓 **Graduate** — Graduated"
        ), inline=False)

        # Spacing line
        embed.add_field(name="\u200b", value="—" * 30, inline=False)

        # Summary section
        for category, names in summary.items():
            if names:
                embed.add_field(name=category, value="\n".join(names), inline=False)
        return embed
    
        async def update_existing_summary_message(self, track_channel):
        sorted_trainees = sorted(trainee_data.items(), key=lambda x: x[1]['join_date'])
        summary = generate_summary_and_legend_embed(sorted_trainees)

        # Locate the existing summary message
        async for message in track_channel.history(limit=50):  # Adjust limit as needed
            if message.author == bot.user and "Trainee Tracker: Legend & Summary" in message.embeds[0].title:
                await message.edit(embed=summary)
                return

    async def setup(bot):
        await bot.add_cog(TraineeTracker(bot))
