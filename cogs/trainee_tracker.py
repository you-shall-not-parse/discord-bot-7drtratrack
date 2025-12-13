import discord
from discord.ext import commands
from datetime import datetime, timedelta
import asyncio

class TraineeTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._send_lock = asyncio.Lock()
        self.GUILD_ID = 1097913605082579024
        self.INFANTRY_ROLE_ID = 1099596178141757542
        self.SUPPORT_ROLE_ID = 1100005693546844242
        self.ENGINEER_ROLE_ID = 1100005700106719312
        self.RECRUITFORM_CHANNEL_ID = 1098331019364552845
        self.TRACKING_CHANNEL_ID = 1368543744193990676
        self.trainee_data = {}
        self.trainee_messages = {}
        self.summary_message_id = None

# rate limiter
    
    async def send_rate_limited(self, channel, *, content=None, embed=None):
        async with self._send_lock:
            try:
                msg = await channel.send(content=content, embed=embed)
                await asyncio.sleep(3)
                return msg
            except discord.HTTPException as e:
                print(f"[Send Failed] {e}")
                return None

    async def edit_rate_limited(self, message, *, content=None, embed=None):
        async with self._send_lock:
            try:
                await message.edit(content=content, embed=embed)
                await asyncio.sleep(3)
            except discord.HTTPException as e:
                print(f"[Edit Failed] {e}")

    # New: safe history fetch with basic backoff to avoid 429
    async def fetch_history_with_backoff(self, channel, *, limit=200, before=None):
        fetched = []
        remaining = limit
        last_message_id = before.id if isinstance(before, discord.Message) else before
        while remaining > 0:
            batch_size = min(remaining, 100)
            try:
                async for msg in channel.history(limit=batch_size, before=last_message_id):
                    fetched.append(msg)
                    last_message_id = msg.id
                remaining -= batch_size
                # Small delay to be gentle on rate limits
                await asyncio.sleep(0.3)
                # If fewer messages than requested came back, stop early
                if len(fetched) < limit and batch_size > 0 and last_message_id is None:
                    break
            except discord.HTTPException as e:
                if getattr(e, 'status', None) == 429:
                    # Backoff then try smaller batch next iteration
                    print("[History] Rate limited, backing off...")
                    await asyncio.sleep(2)
                    # Reduce next batch size to be safer
                    remaining = max(0, remaining - max(10, batch_size // 2))
                    continue
                else:
                    print(f"[History] Fetch failed: {e}")
                    break
        return fetched

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
                    "graduation_date": None
                }

        recruitform_channel = self.bot.get_channel(self.RECRUITFORM_CHANNEL_ID)
        if recruitform_channel:
            # Use safer, lower limit with backoff
            messages = await self.fetch_history_with_backoff(recruitform_channel, limit=200)
            for message in messages:
                if not message.author.bot and message.author.display_name in self.trainee_data:
                    self.trainee_data[message.author.display_name]["recruitform_posted"] = True

        # Scan channel history to rebuild trainee_messages and summary
        self.trainee_messages.clear()
        self.summary_message_id = None
        async for m in track_channel.history(limit=200):
            if m.author != self.bot.user or not m.embeds:
                continue
            title = m.embeds[0].title or ""
            if "Trainee Tracker: Legend & Summary" in title:
                self.summary_message_id = m.id
            else:
                nickname = title.replace("**", "")
                if nickname in self.trainee_data:
                    self.trainee_messages[nickname] = m.id

        sorted_trainees = sorted(self.trainee_data.items(), key=lambda x: x[1]['join_date'])
        for nickname, _ in sorted_trainees:
            if nickname not in self.trainee_messages:
                embed = self.generate_report_embed(nickname)
                msg = await self.send_rate_limited(track_channel, embed=embed)
                if msg:
                    self.trainee_messages[nickname] = msg.id

        summary = self.generate_summary_and_legend_embed(sorted_trainees)
        if self.summary_message_id:
            try:
                smsg = await track_channel.fetch_message(self.summary_message_id)
                await self.edit_rate_limited(smsg, embed=summary)
            except discord.NotFound:
                smsg = await self.send_rate_limited(track_channel, embed=summary)
                if smsg:
                    self.summary_message_id = smsg.id
        else:
            smsg = await self.send_rate_limited(track_channel, embed=summary)
            if smsg:
                self.summary_message_id = smsg.id

        # Ensure summary is last
        await self.ensure_summary_bottom(track_channel, summary)

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
                "graduation_date": None
            }

            embed = self.generate_report_embed(nickname)
            msg = await self.send_rate_limited(track_channel, embed=embed)
            if msg:
                self.trainee_messages[nickname] = msg.id

            async for m in track_channel.history(limit=50):
                if m.author == self.bot.user and m.embeds:
                    if m.embeds[0].title and "Trainee Tracker: Legend & Summary" in m.embeds[0].title:
                        await m.delete()
                        break

            sorted_trainees = sorted(self.trainee_data.items(), key=lambda x: x[1]['join_date'])
            summary = self.generate_summary_and_legend_embed(sorted_trainees)
            await self.send_rate_limited(track_channel, embed=summary)

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
        embed = discord.Embed(title=f"{nickname}")
        joined_days_ago = (datetime.utcnow().replace(tzinfo=None) - data['join_date'].replace(tzinfo=None)).days

        if data['graduated']:
            embed.color = discord.Color.greyple()
        elif data['has_support'] and data['has_engineer'] and joined_days_ago >= 14:
            embed.color = discord.Color.purple()
            embed.title = f"**{nickname}**"
        elif data['has_support'] and data['has_engineer']:
            embed.color = discord.Color.green()
        elif data['has_support'] or data['has_engineer']:
            embed.color = discord.Color.blue()
        elif joined_days_ago > 14:
            embed.color = discord.Color.orange()
        else:
            embed.color = discord.Color.dark_grey()
        
        embed.add_field(name="Profile", value=data["profile_name"], inline=True)
        embed.add_field(name="Join Date", value=data["join_date"].strftime('%d-%m-%Y'), inline=True)
        embed.add_field(name="+14 Days", value=data["joined_plus_2_weeks"].strftime('%d-%m-%Y'), inline=True)
        embed.add_field(name="Support Role", value="✅" if data["has_support"] else "❌", inline=True)
        embed.add_field(name="Engineer Role", value="✅" if data["has_engineer"] else "❌", inline=True)
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
                await self.edit_rate_limited(msg, embed=embed)
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
            elif data["has_support"] and data["has_engineer"] and joined_days_ago >= 14:
                summary["Ready to Graduate"].append(nickname)
            elif data["has_support"] or data["has_engineer"] or joined_days_ago <= 13:
                summary["On-Track"].append(nickname)
            else:
                summary["Behind"].append(nickname)

        embed = discord.Embed(title="Trainee Tracker: Legend & Summary", color=discord.Color.blurple())
        embed.add_field(name="Legend", value=(
            "🟪 **Purple** — Ready to Graduate! Has both roles AND 2+ weeks, amazing!\n"
            "🟩 **Green** — Has both Support and Engineer but not done 2 weeks yet, great\n"
            "🟦 **Blue** — Has one of Support or Engineer, good\n"
            "⬛ **Grey** — No roles but under 2 weeks, not bad\n"
            "🟧 **Orange** — No roles and in server over 2 weeks, bad\n"
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
        if self.summary_message_id:
            try:
                message = await track_channel.fetch_message(self.summary_message_id)
                await self.edit_rate_limited(message, embed=summary)
                # Ensure summary is last
                await self.ensure_summary_bottom(track_channel, summary)
                return
            except discord.NotFound:
                pass
        async for message in track_channel.history(limit=50):
            if message.author == self.bot.user and message.embeds and "Trainee Tracker: Legend & Summary" in message.embeds[0].title:
                self.summary_message_id = message.id
                await self.edit_rate_limited(message, embed=summary)
                return
        msg = await self.send_rate_limited(track_channel, embed=summary)
        if msg:
            self.summary_message_id = msg.id
            await self.ensure_summary_bottom(track_channel, summary)

    async def ensure_summary_bottom(self, track_channel, summary_embed):
        # If summary isn’t last, re-post it to move it to the bottom
        try:
            last_msg = None
            async for m in track_channel.history(limit=1):
                last_msg = m
            if last_msg and last_msg.id != self.summary_message_id:
                # Repost summary and delete old one
                new_msg = await self.send_rate_limited(track_channel, embed=summary_embed)
                if new_msg:
                    old_id = self.summary_message_id
                    self.summary_message_id = new_msg.id
                    if old_id:
                        try:
                            old = await track_channel.fetch_message(old_id)
                            await old.delete()
                        except discord.NotFound:
                            pass
        except discord.HTTPException:
            pass

async def setup(bot):
    await bot.add_cog(TraineeTracker(bot))
