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
        self._ran_once = False

    async def _send_long_message(self, channel: discord.abc.Messageable, content: str):
        # Discord hard limit is 2000 characters per message.
        max_len = 2000

        if len(content) <= max_len:
            await channel.send(content)
            return

        # Prefer splitting on line boundaries for readability.
        lines = content.split("\n")
        chunk = ""
        for line in lines:
            # If a single line is too long, hard-split it.
            while len(line) > max_len:
                prefix_space = "" if not chunk else "\n"
                remaining = max_len - len(chunk) - len(prefix_space)
                if remaining <= 0:
                    await channel.send(chunk)
                    chunk = ""
                    continue
                part, line = line[:remaining], line[remaining:]
                chunk = (chunk + prefix_space + part) if chunk else part
                await channel.send(chunk)
                chunk = ""

            proposed = (chunk + "\n" + line) if chunk else line
            if len(proposed) > max_len:
                if chunk:
                    await channel.send(chunk)
                chunk = line
            else:
                chunk = proposed

        if chunk:
            await channel.send(chunk)

    @commands.Cog.listener()
    async def on_ready(self):
        if self._ran_once:
            return
        self._ran_once = True

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

        target_channel = message.channel

        if OUTPUT_CHANNEL_ID is not None:
            # Prefer guild-local lookups first
            target_channel = message.guild.get_channel(OUTPUT_CHANNEL_ID)
            if target_channel is None:
                try:
                    target_channel = await message.guild.fetch_channel(OUTPUT_CHANNEL_ID)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    target_channel = None

            # Fallback to global lookup (handles cases where the output channel is in a different guild)
            if target_channel is None:
                target_channel = self.bot.get_channel(OUTPUT_CHANNEL_ID)
            if target_channel is None:
                try:
                    target_channel = await self.bot.fetch_channel(OUTPUT_CHANNEL_ID)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    target_channel = None

        if target_channel is None:
            print(
                f"Output channel not found/accessible (OUTPUT_CHANNEL_ID={OUTPUT_CHANNEL_ID}); "
                f"posting in the source channel instead."
            )
            target_channel = message.channel

        try:
            await self._send_long_message(target_channel, output)
        except discord.Forbidden:
            print(
                f"Missing permission to send messages in channel {getattr(target_channel, 'id', None)}. "
                f"Check the bot's permissions and channel overrides."
            )
        except discord.HTTPException as e:
            print(f"Failed to send output due to HTTPException: {e}")

        print("ReactionReader complete — unload when ready")


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionReader(bot))
