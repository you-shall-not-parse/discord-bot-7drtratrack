import discord
from discord.ext import commands

# ========= CONFIG =========
TARGET_MESSAGE_ID = 1458515177438838979
OUTPUT_CHANNEL_ID = 1099806153170489485  # set to None to post in same channel
VALID_REACTIONS = {
    "I": "I",
    "🇮": "I",
    "A": "A",
    "🇦": "A",
    "R": "R",
    "🇷": "R",
}
# ==========================


class ReactionReader(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        print("ReactionReader loaded — running one-time scan")
        await self.run_once()

    async def run_once(self):
        message = None

        # Find the message in all guilds/channels the bot can see
        for guild in self.bot.guilds:
            for channel in guild.text_channels:
                try:
                    message = await channel.fetch_message(TARGET_MESSAGE_ID)
                    if message:
                        break
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    continue
            if message:
                break

        if not message:
            print("Target message not found")
            return

        results = {"I": [], "A": [], "R": []}

        for reaction in message.reactions:
            key = VALID_REACTIONS.get(str(reaction.emoji))
            if not key:
                continue

            async for user in reaction.users():
                if user.bot:
                    continue

                member = message.guild.get_member(user.id)

                nickname = member.display_name if member else "No Nickname"
                username = f"{user.name}#{user.discriminator}"

                entry = f"{nickname} ({username})"
                if entry not in results[key]:
                    results[key].append(entry)

        # Build output
        lines = []
        for key in ["I", "A", "R"]:
            lines.append(f"**{key} ({len(results[key])})**")
            if results[key]:
                lines.extend(f"- {name}" for name in results[key])
            else:
                lines.append("- None")
            lines.append("")

        output = "\n".join(lines)

        target_channel = (
            message.channel
            if OUTPUT_CHANNEL_ID is None
            else message.guild.get_channel(OUTPUT_CHANNEL_ID)
        )

        await target_channel.send(output)

        print("ReactionReader complete — unload when ready")


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionReader(bot))
