import discord
from discord.ext import commands
from datetime import datetime, timedelta

class ReconTroopTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.GUILD_ID = 1097913605082579024
        self.RECONTRAN_ROLE_ID = 1103626508645453975
        self.SPOTTER_ROLE_ID = 1102199425654333522
        self.SNIPER_ROLE_ID = 1102199204887138324
      # self.VETRANSNIPER_ROLE_ID = 1240610719653957652
      # self.VETRANSPOTTER_ROLE_ID = 1240610936696471572
        self.RECRUITFORM_CHANNEL_ID = 1098331019364552845
        self.TRACKING_CHANNEL_ID = 1391119515609333880
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
            if any(role.id == self.RECONTRAN_ROLE_ID for role in member.roles):
                nickname = member.display_name
                profile_name = member.name
                join_date = member.joined_at or datetime.utcnow()
                joined_plus_2_weeks = join_date + timedelta(days=14)
                has_SPOTTER = any(role.id == self.SPOTTER_ROLE_ID for role in member.roles)
                has_SNIPER = any(role.id == self.SNIPER_ROLE_ID for role in member.roles)
                self.trainee_data[nickname] = {
                    "profile_name": profile_name,
                    "join_date": join_date,
                    "joined_plus_2_weeks": joined_plus_2_weeks,
                    "has_SPOTTER": has_SPOTTER,
                    "has_SNIPER": has_SNIPER,
                    "recruitform_posted": False,
                    "left_server": False,
                    "graduated": False,
                    "graduation_date": None
                }

        recruitform_channel = self.bot.get_channel(self.RECRUITFORM_CHANNEL_ID)
        if recruitform_channel:
            async for message in recruitform_channel.history(limit=1000):
                if not message.author.bot and message.author.display_name in self.trainee_data:
                    self.trainee_data[message.author.display_name]["recruitform_posted"] = True

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
        # Removed: sign-up tracking in signup channels

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        nickname = after.display_name
        track_channel = self.bot.get_channel(self.TRACKING_CHANNEL_ID)
        if not track_channel:
            return

        gained_infantry_role = self.RECONTRAN_ROLE_ID not in [r.id for r in before.roles] and \
                               self.RECONTRAN_ROLE_ID in [r.id for r in after.roles]

        if nickname not in self.trainee_data and gained_infantry_role:
            join_date = after.joined_at or datetime.utcnow()
            self.trainee_data[nickname] = {
                "profile_name": after.name,
                "join_date": join_date,
                "joined_plus_2_weeks": join_date + timedelta(days=14),
                "has_SPOTTER": any(role.id == self.SPOTTER_ROLE_ID for role in after.roles),
                "has_SNIPER": any(role.id == self.SNIPER_ROLE_ID for role in after.roles),
                "recruitform_posted": False,
                "left_server": False,
                "graduated": False,
                "graduation_date": None
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

        was_trainee = self.RECONTRAN_ROLE_ID in [r.id for r in before.roles]
        is_trainee = self.RECONTRAN_ROLE_ID in [r.id for r in after.roles]

        if was_trainee and not is_trainee:
            self.trainee_data[nickname]["graduated"] = True
            self.trainee_data[nickname]["graduation_date"] = datetime.utcnow()

        self.trainee_data[nickname]["has_SPOTTER"] = any(role.id == self.SPOTTER_ROLE_ID for role in after.roles)
        self.trainee_data[nickname]["has_SNIPER"] = any(role.id == self.SNIPER_ROLE_ID for role in after.roles)

        await self.update_trainee_embed(nickname, track_channel)
        await self.update_existing_summary_message(track_channel)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        nickname = member.display_name
        if nickname in self.trainee_data:
            self.trainee_data[nickname]["left_server"] = True
            track_channel = self.bot.get_channel(self.TRACKING_CHANNEL_ID)
            await self.update_trainee_embed(nickname, track_channel)

#new section
    
    def generate_report_embed(self, nickname):
        data = self.trainee_data[nickname]
        embed = discord.Embed(
            title=f"{nickname}"
        )
        joined_days_ago = (datetime.utcnow().replace(tzinfo=None) - data['join_date'].replace(tzinfo=None)).days

        if data['graduated']:
            embed.color = discord.Color.greyple()
        elif data['has_SPOTTER'] or data['has_SNIPER'] and joined_days_ago >= 14:
            embed.color = discord.Color.purple()
            embed.title = f"**{nickname}**"
        elif data["has_SPOTTER"] or data["has_SNIPER"] or joined_days_ago <= 13:
            embed.color = discord.Color.blue()
        elif joined_days_ago > 28:
            embed.color = discord.Color.orange()
        else:
            embed.color = discord.Color.dark_grey()

        embed.add_field(name="Profile", value=data["profile_name"], inline=True)
        embed.add_field(name="Join Date", value=data["join_date"].strftime('%d-%m-%Y'), inline=True)
        embed.add_field(name="+14 Days", value=data["joined_plus_2_weeks"].strftime('%d-%m-%Y'), inline=True)
        embed.add_field(name="Spotter Role", value="✅" if data["has_SPOTTER"] else "❌", inline=True)
        embed.add_field(name="Sniper Role", value="✅" if data["has_SNIPER"] else "❌", inline=True)
        embed.add_field(name="Recruit Form Posted", value="✅" if data["recruitform_posted"] else "❌", inline=True)
        if data["graduated"] and data["graduation_date"]:
            embed.add_field(name="Graduation Date", value=data["graduation_date"].strftime('%Y-%m-%d'), inline=True)
        if data["left_server"]:
            embed.set_footer(text="⚠️ This member has left the server")
        elif data["graduated"]:
            embed.set_footer(text=f"🎓 Graduated on {data['graduation_date'].strftime('%d-%m-%Y')}")
        return embed



    async def update_trainee_embed(self, nickname, track_channel):
        if nickname in self.trainee_messages:
            try:
                msg = await track_channel.fetch_message(self.trainee_messages[nickname])
                embed = self.generate_report_embed(nickname)
                await msg.edit(embed=embed)
            except discord.NotFound:
                pass

    def generate_summary_and_legend_embed(self, trainees_sorted):
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
            elif data["has_SPOTTER"] or data["has_SNIPER"] and joined_days_ago >= 14:
                summary["Ready to Graduate"].append(nickname)
            elif data["has_SPOTTER"] or data["has_SNIPER"] or joined_days_ago <= 13:
                summary["On-Track"].append(nickname)
            else:
                summary["Behind"].append(nickname)

        embed = discord.Embed(title="Trainee Tracker: Legend & Summary", color=discord.Color.blurple())

        embed.add_field(name="Legend", value=(
            "🟪 **Purple** — Ready to Graduate! Has either Spotter or Sniper AND 2+ weeks, amazing!\n"
            "🟦 **Blue** — Has one of Spotter or Sniper but less than 2 weeks, good\n"
            "⬛ **Grey** — No roles but under 2 weeks, not bad\n"
            "🟧 **Orange** — No roles and in server over 4 weeks, bad\n"
            "🎓 **Graduate** — Graduated"
        ), inline=False)

        embed.add_field(name="\u200b", value="—" * 30, inline=False)

        for category, names in summary.items():
            if names:
                embed.add_field(name=category, value="\n".join(names), inline=False)

        return embed

    async def update_existing_summary_message(self, track_channel):
        sorted_trainees = sorted(self.trainee_data.items(), key=lambda x: x[1]['join_date'])
        summary = self.generate_summary_and_legend_embed(sorted_trainees)
        async for message in track_channel.history(limit=50):
            if message.author == self.bot.user and message.embeds and "Trainee Tracker: Legend & Summary" in message.embeds[0].title:
                await message.edit(embed=summary)
                return

async def setup(bot):
    await bot.add_cog(ReconTroopTracker(bot))
