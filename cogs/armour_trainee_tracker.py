import discord
from discord.ext import commands
from datetime import datetime, timedelta

class ArmourTraineeTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.GUILD_ID = 1097913605082579024
        self.ARMOUR_ROLE_ID = 1099615408518070313 #changed
        self.BAC_ROLE_ID = 1182154521129009202 #changed from BAC
        self.DRIVER_ROLE_ID = 1100005700106719312 #changed from Driver
        self.GUNNER_ROLE_ID = 1108426942610407494 #changed from none - new
        self.RECRUITFORM_CHANNEL_ID = 1098331019364552845
        self.TRACKING_CHANNEL_ID = 1391119412047777974 #changed
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
            if any(role.id == self.ARMOUR_ROLE_ID for role in member.roles):
                nickname = member.display_name
                profile_name = member.name
                join_date = member.joined_at or datetime.utcnow()
                joined_plus_2_weeks = join_date + timedelta(days=14)
                has_BAC = any(role.id == self.BAC_ROLE_ID for role in member.roles)
                has_Driver = any(role.id == self.DRIVER_ROLE_ID for role in member.roles)
                has_Gunner = any(role.id == self.GUNNER_ROLE_ID for role in member.roles)
                self.trainee_data[nickname] = {
                    "profile_name": profile_name,
                    "join_date": join_date,
                    "joined_plus_2_weeks": joined_plus_2_weeks,
                    "has_BAC": has_BAC,
                    "has_Driver": has_Driver,
                    "has_Gunner": has_Gunner,
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

        gained_infantry_role = self.ARMOUR_ROLE_ID not in [r.id for r in before.roles] and \
                               self.ARMOUR_ROLE_ID in [r.id for r in after.roles]

        if nickname not in self.trainee_data and gained_infantry_role:
            join_date = after.joined_at or datetime.utcnow()
            self.trainee_data[nickname] = {
                "profile_name": after.name,
                "join_date": join_date,
                "joined_plus_2_weeks": join_date + timedelta(days=14),
                "has_BAC": any(role.id == self.BAC_ROLE_ID for role in after.roles),
                "has_Driver": any(role.id == self.DRIVER_ROLE_ID for role in after.roles),
                "has_Gunner": any(role.id == self.GUNNER_ROLE_ID for role in after.roles),
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

        was_trainee = self.ARMOUR_ROLE_ID in [r.id for r in before.roles]
        is_trainee = self.ARMOUR_ROLE_ID in [r.id for r in after.roles]

        if was_trainee and not is_trainee:
            self.trainee_data[nickname]["graduated"] = True
            self.trainee_data[nickname]["graduation_date"] = datetime.utcnow()

        self.trainee_data[nickname]["has_BAC"] = any(role.id == self.BAC_ROLE_ID for role in after.roles)
        self.trainee_data[nickname]["has_Driver"] = any(role.id == self.DRIVER_ROLE_ID for role in after.roles)
        self.trainee_data[nickname]["has_Gunner"] = any(role.id == self.GUNNER_ROLE_ID for role in after.roles)

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
            embed.title = f"**{nickname}**"
        elif data['has_BAC']:
            embed.color = discord.Color.green()
        elif data['has_Driver'] or data['has_Gunner']:
            embed.color = discord.Color.blue()
        elif joined_days_ago > 28:
            embed.color = discord.Color.orange()
        else:
            embed.color = discord.Color.dark_grey()

        embed.add_field(name="Profile", value=data["profile_name"], inline=True)
        embed.add_field(name="Join Date", value=data["join_date"].strftime('%d-%m-%Y'), inline=True)
        embed.add_field(name="+14 Days", value=data["joined_plus_2_weeks"].strftime('%d-%m-%Y'), inline=True)
        embed.add_field(name="BAC Role", value="✅" if data["has_BAC"] else "❌", inline=True)
        embed.add_field(name="Driver Role", value="✅" if data["has_Driver"] else "❌", inline=True)
        embed.add_field(name="Gunner Role", value="✅" if data["has_Gunner"] else "❌", inline=True)
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
        "Active": [],
        "Need Training": [],
        "Behind": [],
        "Now Inactive": [],
        "Graduated": []
    }

for nickname, data in trainees_sorted:
    joined_days_ago = (datetime.utcnow().replace(tzinfo=None) - data['join_date'].replace(tzinfo=None)).days
    has_cert = data["has_BAC"] or data["has_Driver"] or data["has_Gunner"]

    if data["graduated"]:
        summary["Graduated"].append(nickname)
    elif has_cert and joined_days_ago < 14:
        summary["Active"].append(nickname)
    elif not has_cert and joined_days_ago < 14:
        summary["Need Training"].append(nickname)
    elif not has_cert and joined_days_ago >= 28:
        summary["Now Inactive"].append(nickname)
    elif not has_cert and joined_days_ago >= 14:
        summary["Behind"].append(nickname)
    else:
        summary["Behind"].append(nickname)

    embed = discord.Embed(title="Trainee Tracker: Legend & Summary", color=discord.Color.blurple())
    embed.add_field(name="Legend", value=(
        "🟩 **Active** — Has a cert and in server less than 2 weeks, great\n"
        "⬛ **Need Training** — No certs but under 2 weeks, not bad\n"
        "🟧 **Behind** — No certs and in server over 2 weeks, bad\n"
        "🟥 **Now Inactive** — Been tank crew trainee over 4 weeks, terrible (unless they're a joiner from infantry)\n"
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
    await bot.add_cog(ArmourTraineeTracker(bot))
