import os
import discord
from discord import app_commands
from discord.ext import commands


# Guild-scoped commands require a guild sync (see on_ready below).
# Default matches other cogs in this repo; override via env var if needed.
ECHO_GUILD_ID = int(os.getenv("ECHO_GUILD_ID", "1097913605082579024"))
TARGET_GUILD = discord.Object(id=ECHO_GUILD_ID)


class Echo(commands.Cog):
	def __init__(self, bot: commands.Bot):
		self.bot = bot

	@app_commands.guilds(TARGET_GUILD)
	@app_commands.guild_only()
	@app_commands.command(name="7drecho", description="Send a user-defined message.")
	@app_commands.describe(message="The message to send")
	async def seven_drecho(self, interaction: discord.Interaction, message: str):
		# Ephemeral ack so the channel doesn't show "<user> used /7drecho".
		await interaction.response.send_message("Sent.", ephemeral=True)
		if interaction.channel is not None:
			await interaction.channel.send(message)

	@commands.Cog.listener()
	async def on_ready(self):
		# Ensure the guild-scoped command is registered quickly.
		try:
			await self.bot.tree.sync(guild=TARGET_GUILD)
			print(f"[Echo] Commands synced to guild {ECHO_GUILD_ID}.")
		except Exception as e:
			print(f"[Echo] Sync error: {e}")


async def setup(bot: commands.Bot):
	await bot.add_cog(Echo(bot))

