import os
import discord
from discord import app_commands
from discord.ext import commands


# Guild-scoped commands require a guild sync (see on_ready below).
# Default matches other cogs in this repo; override via env var if needed.
ECHO_GUILD_ID = int(os.getenv("ECHO_GUILD_ID", "1097913605082579024"))

# Role required to use /7drecho (can be overridden via env var ECHO_ROLE_ID)
ECHO_ROLE_ID = int(os.getenv("ECHO_ROLE_ID", "1213495462632361994"))
TARGET_GUILD = discord.Object(id=ECHO_GUILD_ID)


def _has_echo_role(interaction: discord.Interaction) -> bool:
	# If not configured, deny by default.
	if not isinstance(ECHO_ROLE_ID, int) or ECHO_ROLE_ID <= 0:
		return False
	user = interaction.user
	if not isinstance(user, discord.Member):
		return False
	return any(role.id == ECHO_ROLE_ID for role in user.roles)


class Echo(commands.Cog):
	def __init__(self, bot: commands.Bot):
		self.bot = bot

	@app_commands.guilds(TARGET_GUILD)
	@app_commands.guild_only()
	@app_commands.command(name="7drecho", description="Send a user-defined message.")
	@app_commands.describe(message="The message to send")
	@app_commands.check(_has_echo_role)
	async def seven_drecho(self, interaction: discord.Interaction, message: str):
		# Ephemeral ack so the channel doesn't show "<user> used /7drecho".
		await interaction.response.send_message("Sent.", ephemeral=True)
		if interaction.channel is not None:
			await interaction.channel.send(message)

	@seven_drecho.error
	async def seven_drecho_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
		if isinstance(error, app_commands.CheckFailure):
			msg = "You don't have permission to use this command."
			if not isinstance(ECHO_ROLE_ID, int) or ECHO_ROLE_ID <= 0:
				msg = "This command isn't configured yet (ECHO_ROLE_ID is not set)."
			if interaction.response.is_done():
				await interaction.followup.send(msg, ephemeral=True)
			else:
				await interaction.response.send_message(msg, ephemeral=True)
			return
		raise error

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

