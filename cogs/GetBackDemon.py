import discord
from discord.ext import commands
from discord import app_commands
import os

# -------- Customisable Command Name --------
COMMAND_NAME = "getbackdemon"
GUILD_ID = 1097913605082579024

# -------- Options (choice name -> dict with text + optional local GIF file + optional color + optional author) --------
TEXT_OPTIONS = {
    "Disarm Demon": {
        "text": "Demon disarmed, armless fuck",
        "gif_file": "disarm.gif",  # Stored in cogs/demongifs/
        "color": 0xFF0000,
        "author": None
    },
    "Banish Demon": {
        "text": "Demon banished, back to the void",
        "gif_file": "tenor.gif",
        "color": 0x800080,
        "author": "Exorcist user_name chants"
    },
    "Mock Demon": {
        "text": "Demon mocked into submission, no GIF needed",
        "gif_file": None,
        "color": 0x00FF00,
        "author": None
    },
}

class GetBackDemon(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name=COMMAND_NAME,
        description="Post a preset text with optional local GIF, color, and author."
    )
    @app_commands.describe(choice="Pick a response option.")
    @app_commands.choices(
        choice=[app_commands.Choice(name=key, value=key) for key in TEXT_OPTIONS.keys()]
    )
    async def getbackdemon(self, interaction: discord.Interaction, choice: app_commands.Choice[str]):
        option = TEXT_OPTIONS.get(choice.value)
        if not option:
            await interaction.response.send_message("Invalid choice.", ephemeral=True)
            return

        await interaction.response.defer()

        user_name = interaction.user.display_name
        author_text = option.get("author")
        author_name = author_text.replace("user_name", user_name) if author_text else None

        embed = discord.Embed(description=option["text"], color=option.get("color", 0x808080))
        if author_name:
            embed.set_author(name=author_name)

        gif_file_name = option.get("gif_file")
        if gif_file_name:
            gif_path = os.path.join(os.path.dirname(__file__), "demongifs", gif_file_name)
            # Use attachment://filename.gif to embed local file
            embed.set_image(url=f"attachment://{gif_file_name}")
            await interaction.followup.send(embed=embed, file=discord.File(gif_path))
        else:
            await interaction.followup.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(GetBackDemon(bot))
