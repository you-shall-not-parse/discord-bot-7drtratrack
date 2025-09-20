import discord from discord.ext import commands from discord import app_commands from discord.ui import View, Select, Modal, InputText import sqlite3 import random import datetime

---------------- Config ----------------

GUILD_ID = 123456789012345678  # replace with your guild ID LEADERBOARD_CHANNEL_ID = 123456789012345678  # replace with your leaderboard channel SUBMISSIONS_CHANNEL_ID = 123456789012345678  # replace with your submissions channel DB_FILE = "leaderboard.db"

STATS = ["Kills", "Artillery Kills", "Vehicles Destroyed", "Killstreak", "Satchel Kills"]

---------------- Database ----------------

def init_db(): conn = sqlite3.connect(DB_FILE) c = conn.cursor() c.execute(""" CREATE TABLE IF NOT EXISTS submissions ( id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, stat TEXT, value INTEGER, submitted_at TEXT ) """) c.execute(""" CREATE TABLE IF NOT EXISTS metadata ( key TEXT PRIMARY KEY, value TEXT ) """) conn.commit() conn.close()

init_db()

---------------- Cog ----------------

class HLLLeaderboard(commands.Cog): def init(self, bot): self.bot = bot

async def get_leaderboard_message(self):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT value FROM metadata WHERE key = ?", ("leaderboard_message_id",))
    row = c.fetchone()
    conn.close()
    if row:
        try:
            channel = self.bot.get_channel(LEADERBOARD_CHANNEL_ID)
            return await channel.fetch_message(int(row[0]))
        except Exception:
            return None
    return None

async def set_leaderboard_message(self, message_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO metadata(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", ("leaderboard_message_id", str(message_id)))
    conn.commit()
    conn.close()

async def build_leaderboard_embed(self, monthly=False):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    embed = discord.Embed(title="Hell Let Loose Leaderboard" + (" - This Month" if monthly else ""), color=discord.Color.dark_gold())

    for stat in STATS:
        if monthly:
            now = datetime.datetime.utcnow()
            start_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            c.execute("SELECT user_id, SUM(value) FROM submissions WHERE stat=? AND submitted_at>=? GROUP BY user_id ORDER BY SUM(value) DESC LIMIT 5", (stat, start_month.isoformat()))
        else:
            c.execute("SELECT user_id, SUM(value) FROM submissions WHERE stat=? GROUP BY user_id ORDER BY SUM(value) DESC LIMIT 5", (stat,))

        rows = c.fetchall()
        if rows:
            lines = []
            for idx, (user_id, total) in enumerate(rows, 1):
                user = self.bot.get_user(user_id)
                name = user.mention if user else f"<@{user_id}>"
                lines.append(f"**{idx}.** {name} — {total}")
            embed.add_field(name=stat, value="\n".join(lines), inline=False)
        else:
            embed.add_field(name=stat, value="No data yet", inline=False)

    now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M GMT")
    embed.set_footer(text=f"Last updated: {now_str}")
    conn.close()
    return embed

async def update_leaderboard(self):
    msg = await self.get_leaderboard_message()
    channel = self.bot.get_channel(LEADERBOARD_CHANNEL_ID)
    embed = await self.build_leaderboard_embed(monthly=False)
    if msg:
        await msg.edit(embed=embed, view=LeaderboardView(self))
    else:
        new_msg = await channel.send(embed=embed, view=LeaderboardView(self))
        await self.set_leaderboard_message(new_msg.id)

@commands.Cog.listener()
async def on_ready(self):
    await self.update_leaderboard()

@app_commands.command(name="hlltopscores", description="Show all-time top scores")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def hlltopscores(self, interaction: discord.Interaction):
    embed = await self.build_leaderboard_embed(monthly=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@app_commands.command(name="hllmonthtopscores", description="Show top scores for this month")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def hllmonthtopscores(self, interaction: discord.Interaction):
    embed = await self.build_leaderboard_embed(monthly=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

---------------- Submission Modal ----------------

class SubmissionModal(Modal): def init(self, cog, stat, user): super().init(title=f"Submit {stat}") self.cog = cog self.stat = stat self.user = user self.value_input = InputText(label="Enter your score", placeholder="e.g. 10") self.add_item(self.value_input)

async def callback(self, interaction: discord.Interaction):
    try:
        value = int(self.value_input.value)
    except ValueError:
        await interaction.response.send_message("Please enter a valid number.", ephemeral=True)
        return

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO submissions(user_id, stat, value, submitted_at) VALUES(?, ?, ?, ?)",
              (self.user.id, self.stat, value, datetime.datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()

    await self.cog.update_leaderboard()

    # Random screenshot requirement
    submissions_channel = self.cog.bot.get_channel(SUBMISSIONS_CHANNEL_ID)
    require_ss = random.choice([True, False])
    msg = f"{self.user.mention} submitted {value} {self.stat}!"
    if require_ss:
        msg += " Screenshot required!"
    await submissions_channel.send(msg)

    await interaction.response.send_message("Submission recorded!", ephemeral=True)

---------------- Views ----------------

class LeaderboardView(View): def init(self, cog): super().init(timeout=None) self.cog = cog self.add_item(StatSelect(cog))

class StatSelect(Select): def init(self, cog): self.cog = cog options = [discord.SelectOption(label=stat, value=stat) for stat in STATS] super().init(placeholder="Select stat to submit", options=options) self.cog = cog

async def callback(self, interaction: discord.Interaction):
    stat = self.values[0]
    await interaction.response.send_modal(SubmissionModal(self.cog, stat, interaction.user))

---------------- Setup ----------------

async def setup(bot): await bot.add_cog(HLLLeaderboard(bot))

