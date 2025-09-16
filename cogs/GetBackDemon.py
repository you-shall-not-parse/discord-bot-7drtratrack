import discord
from discord.ext import commands
from discord import app_commands

# -------- Customisable Command Name --------
COMMAND_NAME = "getbackdemon"
GUILD_ID = 1097913605082579024

# -------- Options (choice name -> dict with text + optional gif + optional color + optional author) --------
TEXT_OPTIONS = {
    "Disarm Demon": {
        "text": "Demon disarmed, armless fuck",
        "gif": "https://media1.tenor.com/images/3zTr3DW-OJ0AAAAd/not-today-satan-nope.gif",
        "color": 0xFF0000,
        "author": None
    },
    "Banish Demon": {
        "text": "Demon banished, back to the void",
        "gif": "https://media.tenor.com/images/bRVAx/gif",
        "color": 0x800080,
        "author": "Exorcist user_name chants"
    },
    "Mock Demon": {
        "text": "Demon mocked into submission, no GIF needed",
        "color": 0x00FF00,
        "author": None
    },
}


class GetBackDemon(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name=COMMAND_NAME,
        description="Post a preset text (with sometimes GIF, custom color, and author)."
    )
    @app_commands.describe(
        choice="Pick a response option."
    )
    @app_commands.choices(
        choice=[
            app_commands.Choice(name=key, value=key)
            for key in TEXT_OPTIONS.keys()
        ]
    )
    async def getbackdemon(
        self,
        interaction: discord.Interaction,
        choice: app_commands.Choice[str],
    ):
        """Slash command that posts a chosen text response with optional GIF, color, and author."""
        option = TEXT_OPTIONS.get(choice.value)
        if not option:
            await interaction.response.send_message("Invalid choice.", ephemeral=True)
            return

        # Defer in case sending takes a moment
        await interaction.response.defer()

        embed_color = option.get("color", 0x808080)  # Default gray
        user_name = interaction.user.display_name

        # Handle author
        author_text = option.get("author")
        author_name = author_text.replace("user_name", user_name) if author_text else None

        embed = discord.Embed(
            description=option["text"],
            color=embed_color
        )
        if author_name:
            embed.set_author(name=author_name)

        if option.get("gif"):
            embed.set_image(url=option["gif"])

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(GetBackDemon(bot))