import discord
from discord.ext import commands
from discord import app_commands

# -------- Customisable Command Name --------
COMMAND_NAME = "getbackdemon"  # Change this to rename the slash command

# -------- Guild ID (replace with your server's ID) --------
GUILD_ID = 1097913605082579024  # 👈 Put your guild/server ID here

# -------- Options (choice name -> dict with text + optional gif + optional color + optional author) --------
TEXT_OPTIONS = {
    "Disarm Demon": {
        "text": "Demon disarmed, armless fuck",
        "gif": "https://media.giphy.com/media/3oriO0OEd9QIDdllqo/giphy.gif",
        "color": 0xFF0000,  # Red
        "author": None  # None = shows user display name
    },
    "Banish Demon": {
        "text": "Demon banished, back to the void",
        "gif": "https://media.giphy.com/media/l41YxH9zV2Q0k6dny/giphy.gif",
        "color": 0x800080,  # Purple
        "author": "Exorcist chants"
    },
    "Mock Demon": {
        "text": "Demon mocked into submission, no GIF needed",
        "color": 0x00FF00,  # Green
        "author": None
    },
}


class GetBackDemon(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name=COMMAND_NAME,
        description="Post a preset text (with optional GIF, custom color, and author)."
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
        """Slash command that posts a chosen text response with optional GIF, custom color, and optional author."""
        option = TEXT_OPTIONS.get(choice.value)
        if not option:
            await interaction.response.send_message("Invalid choice.", ephemeral=True)
            return

        embed_color = option.get("color", 0xFF0000)  # Default red
        author_name = option.get("author") or f"{interaction.user.display_name} says:"

        embed = discord.Embed(
            description=option["text"],
            color=embed_color
        )
        embed.set_author(name=author_name)

        if option.get("gif"):
            embed.set_image(url=option["gif"])

        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    cog = GetBackDemon(bot)
    await bot.add_cog(cog)

    # --- Sync to guild (instant) ---
    guild = discord.Object(id=GUILD_ID)
    bot.tree.add_command(cog.getbackdemon, guild=guild)
    await bot.tree.sync(guild=guild)

    # --- Sync globally (takes up to 1 hour) ---
    await bot.tree.sync()
