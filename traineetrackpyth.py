# How to update git in umbuntu environment - in the project folder, type 'git pull', then use a personal access token (from developer settings in github) as password, username is you-shall-not-parse 
import os
from dotenv import load_dotenv
import discord
import asyncio
from datetime import datetime, timedelta

# ---------------- CONFIGURATION ----------------
load_dotenv() # Load variables from .env
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID = 1097913605082579024
INFANTRY_ROLE_ID = 1099596178141757542
SUPPORT_ROLE_ID = 1100005693546844242
ENGINEER_ROLE_ID = 1100005700106719312
RECRUITFORM_CHANNEL_ID = 1098331019364552845
TRACKING_CHANNEL_ID = 1368543744193990676
CHANNEL_IDS = {
    "Training Sign-ups": 1097984129854869515,
    "Comp Match Sign-ups": 1101226715763720272,
    "Friday Event Sign-ups": 1317153335354327060
}

trainee_messages = {}
trainee_data = {}

# ---------------- DISCORD BOT SETUP ----------------
intents = discord.Intents.default()
intents.members = True
intents.messages = True
intents.message_content = True
bot = discord.Client(intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    guild = bot.get_guild(GUILD_ID)
    track_channel = bot.get_channel(TRACKING_CHANNEL_ID)
    if not guild or not track_channel:
        print("Guild or tracking channel not found.")
        return

    for member in guild.members:
        if any(role.id == INFANTRY_ROLE_ID for role in member.roles):
            nickname = member.display_name
            profile_name = member.name
            join_date = member.joined_at or datetime.utcnow()
            joined_plus_4_weeks = join_date + timedelta(days=28)
            has_support = any(role.id == SUPPORT_ROLE_ID for role in member.roles)
            has_engineer = any(role.id == ENGINEER_ROLE_ID for role in member.roles)
            trainee_data[nickname] = {
                "profile_name": profile_name,
                "join_date": join_date,
                "joined_plus_4_weeks": joined_plus_4_weeks,
                "has_support": has_support,
                "has_engineer": has_engineer,
                "recruitform_posted": False,
                "left_server": False,
                "graduated": False,
                "graduation_date": None,
                "signups": {ch: 0 for ch in CHANNEL_IDS}
            }

    recruitform_channel = bot.get_channel(RECRUITFORM_CHANNEL_ID)
    if recruitform_channel:
        async for message in recruitform_channel.history(limit=1000):
            if not message.author.bot and message.author.display_name in trainee_data:
                trainee_data[message.author.display_name]["recruitform_posted"] = True

    for channel_name, channel_id in CHANNEL_IDS.items():
        channel = bot.get_channel(channel_id)
        if channel:
            async for message in channel.history(limit=500):
                if message.author.bot:
                    continue
                if message.embeds:
                    for embed in message.embeds:
                        if not embed.description:
                            continue
                        for trainee in trainee_data:
                            if trainee in embed.description:
                                trainee_data[trainee]["signups"][channel_name] += 1
                else:
                    for trainee in trainee_data:
                        if trainee in message.content:
                            trainee_data[trainee]["signups"][channel_name] += 1

    await track_channel.purge(limit=100, check=lambda m: m.author == bot.user)

    sorted_trainees = sorted(trainee_data.items(), key=lambda x: x[1]['join_date'])
    for nickname, data in sorted_trainees:
        embed = generate_report_embed(nickname)
        msg = await track_channel.send(embed=embed)
        trainee_messages[nickname] = msg.id

    summary = generate_summary_and_legend_embed(sorted_trainees)
    await track_channel.send(embed=summary)

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    nickname = message.author.display_name
    track_channel = bot.get_channel(TRACKING_CHANNEL_ID)
    if message.channel.id == RECRUITFORM_CHANNEL_ID:
        if nickname in trainee_messages:
            trainee_data[nickname]["recruitform_posted"] = True
            await update_trainee_embed(nickname, track_channel)
    elif message.channel.id in CHANNEL_IDS.values():
        for trainee in trainee_messages:
            if trainee in message.content:
                channel_name = next((name for name, cid in CHANNEL_IDS.items() if cid == message.channel.id), None)
                if channel_name:
                    trainee_data[trainee]["signups"][channel_name] += 1
                    await update_trainee_embed(trainee, track_channel)

@bot.event
async def on_member_update(before, after):
    nickname = after.display_name
    track_channel = bot.get_channel(TRACKING_CHANNEL_ID)
    if nickname not in trainee_data:
        return

    was_trainee = any(role.id == INFANTRY_ROLE_ID for role in before.roles)
    is_trainee = any(role.id == INFANTRY_ROLE_ID for role in after.roles)

    if not was_trainee and is_trainee:
        # New Infantry Trainee - Add data and create a new embed
        trainee_data[nickname]["graduated"] = False
        trainee_data[nickname]["graduation_date"] = None
        
        # Generate and send new embed
        embed = generate_report_embed(nickname)
        msg = await track_channel.send(embed=embed)
        trainee_messages[nickname] = msg.id

    elif was_trainee and not is_trainee:
        # Graduation detected
        trainee_data[nickname]["graduated"] = True
        trainee_data[nickname]["graduation_date"] = datetime.utcnow()

    # Update other role statuses
    trainee_data[nickname]["has_support"] = any(role.id == SUPPORT_ROLE_ID for role in after.roles)
    trainee_data[nickname]["has_engineer"] = any(role.id == ENGINEER_ROLE_ID for role in after.roles)

    # Update the trainee's embed
    await update_trainee_embed(nickname, track_channel)

    # Update the summary message
    await update_summary_message(track_channel)


@bot.event
async def on_member_remove(member):
    nickname = member.display_name
    if nickname in trainee_data:
        trainee_data[nickname]["left_server"] = True
        track_channel = bot.get_channel(TRACKING_CHANNEL_ID)
        await update_trainee_embed(nickname, track_channel)

# ---------------- HELPERS ----------------
def generate_report_embed(nickname):
    data = trainee_data[nickname]
    joined_days_ago = (datetime.utcnow().replace(tzinfo=None) - data['join_date'].replace(tzinfo=None)).days
    embed = discord.Embed(title=nickname)
    if data['graduated']:
        embed.color = discord.Color.greyple()
    elif data['has_support'] and data['has_engineer'] and joined_days_ago >= 28:
        embed.color = discord.Color.purple()
        embed.title = f"**{nickname}**"
    elif data['has_support'] and data['has_engineer']:
        embed.color = discord.Color.green()
    elif data['has_support'] or data['has_engineer']:
        embed.color = discord.Color.blue()
    elif joined_days_ago > 28:
        embed.color = discord.Color.orange()
    else:
        embed.color = discord.Color.dark_grey()
    total_signups = sum(data["signups"].values())
    embed.add_field(name="Profile", value=data["profile_name"], inline=True)
    embed.add_field(name="Joined", value=data["join_date"].strftime("%d-%m-%Y"), inline=True)
    embed.add_field(name="4 Week Mark", value=data["joined_plus_4_weeks"].strftime("%d-%m-%Y"), inline=True)
    embed.add_field(name="Support Role", value="✅" if data["has_support"] else "❌", inline=True)
    embed.add_field(name="Engineer Role", value="✅" if data["has_engineer"] else "❌", inline=True)
    embed.add_field(name="Recruitform Posted", value="✅" if data["recruitform_posted"] else "❌", inline=True)
    embed.add_field(name="Total Sign-ups", value=str(total_signups), inline=True)
    if data["left_server"]:
        embed.set_footer(text="⚠️ This member has left the server")
    elif data["graduated"]:
        embed.set_footer(text=f"🎓 Graduated on {data['graduation_date'].strftime('%d-%m-%Y')}")
    return embed

async def update_trainee_embed(nickname, track_channel):
    if nickname in trainee_messages:
        try:
            msg = await track_channel.fetch_message(trainee_messages[nickname])
            embed = generate_report_embed(nickname)
            await msg.edit(embed=embed)
        except discord.NotFound:
            pass


def generate_summary_and_legend_embed(trainees_sorted):
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
        elif data["has_support"] or data["has_engineer"] or joined_days_ago <= 28:
            summary["On-Track"].append(nickname)
        else:
            summary["Behind"].append(nickname)

    embed = discord.Embed(title="**Trainee Tracker: Legend & Summary**", color=discord.Color.blurple())

    # Legend section
    embed.add_field(name="Legend", value=(
        "🟪 **Purple** — Ready to Graduate! Has both roles AND 2+ weeks, amazing! \n"
        "🟩 **Green** — Has both Support and Engineer but not done 2 weeks yet, great\n"
        "🟦 **Blue** — Has one of Support or Engineer, good \n"
        "⬛ **Grey** — No roles but under 4 weeks, not bad\n"
        "🟧 **Orange** — No roles and in server over 4 weeks, bad\n"
        "🟥 **Graduate** — Graduated"
    ), inline=False)

    # Spacing line
    embed.add_field(name="\u200b", value="—" * 30, inline=False)

    # Summary section
    for category, names in summary.items():
        if names:
            embed.add_field(name=category, value="\n".join(names), inline=False)

    return embed

async def update_summary_message(track_channel):
    sorted_trainees = sorted(trainee_data.items(), key=lambda x: x[1]['join_date'])
    summary = generate_summary_and_legend_embed(sorted_trainees)

    # Find the summary message (assuming it's the last message or has a specific identifier)
    async for message in track_channel.history(limit=50):  # Adjust limit as needed
        if message.author == bot.user and "Trainee Tracker: Legend & Summary" in message.embeds[0].title:
            await message.edit(embed=summary)
            return

    # If no summary message was found, send a new one
    await track_channel.send(embed=summary)

# ---------------- RUN BOT ----------------
if __name__ == "__main__":
    bot.run(TOKEN)
