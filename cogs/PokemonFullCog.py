import discord
from discord import app_commands
from discord.ext import commands
import requests

POKEMON_API_KEY = "YOUR_API_KEY_HERE"  # <-- Top of file
GUILD_ID = 1234567890  # Replace with your guild ID

# Example set list
SETS = [
    {"name": "Base Set", "size": 102},
    {"name": "Jungle", "size": 64},
    {"name": "Fossil", "size": 62},
    # Add all sets here
]

class PokemonPriceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def get_exchange_rate(self):
        # Get USD -> GBP conversion
        response = requests.get("https://api.exchangerate.host/latest?base=USD&symbols=GBP")
        data = response.json()
        return data["rates"]["GBP"]

    async def get_card_info(self, set_name, card_number):
        # Fetch from TCGplayer (example)
        headers = {"Authorization": f"Bearer {POKEMON_API_KEY}"}
        # Replace with actual endpoint
        url = f"https://api.tcgplayer.com/cards/{set_name}/{card_number}"
        r = requests.get(url, headers=headers)
        return r.json()

    @app_commands.command(name="price", description="Get Pokémon card price")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.describe(set_name="Set of the card", card_number="Card number in set")
    async def price(self, interaction: discord.Interaction, set_name: str, card_number: str):
        await interaction.response.defer()
        card_info = await self.get_card_info(set_name, card_number)
        rate = await self.get_exchange_rate()

        usd_price = float(card_info["price_usd"])
        gbp_price = usd_price * rate

        embed = discord.Embed(
            title=f"{card_info['name']} ({set_name} {card_number})",
            description=f"Price: ${usd_price:.2f} / £{gbp_price:.2f}\nRelease Date: {card_info['release_date']}",
            color=discord.Color.blue()
        )
        embed.set_image(url=card_info["image_url"])
        await interaction.followup.send(embed=embed)

    # Autocomplete for set names
    @price.autocomplete("set_name")
    async def set_name_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=s["name"], value=s["name"])
            for s in SETS if current.lower() in s["name"].lower()
        ][:25]

    # Autocomplete for card numbers
    @price.autocomplete("card_number")
    async def card_number_autocomplete(self, interaction: discord.Interaction, current: str):
        # Get selected set from options
        options = interaction.namespace
        selected_set = next((s for s in SETS if s["name"] == getattr(options, "set_name", "")), None)
        if not selected_set:
            return []

        return [
            app_commands.Choice(name=f"{i}/{selected_set['size']}", value=f"{i}/{selected_set['size']}")
            for i in range(1, selected_set["size"] + 1)
            if current in str(i)
        ][:25]

async def setup(bot: commands.Bot):
    await bot.add_cog(PokemonPriceCog(bot))
