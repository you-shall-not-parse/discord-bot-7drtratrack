import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import aiosqlite
import os
import requests

# Load API key from .env if needed
from dotenv import load_dotenv
load_dotenv()
POKEMON_API_KEY = os.getenv("POKEMON_TCG_API_KEY")

# 🔴 Replace this with your actual guild ID
GUILD_ID = 1097913605082579024  # Set your guild/server ID here
API_BASE = "https://api.pokemontcg.io/v2"

def fetch_all_sets():
    try:
        url = "https://api.pokemontcg.io/v2/sets"
        headers = {"X-Api-Key": "YOUR_POKEMON_TCG_API_KEY"}
        resp = requests.get(url, headers=headers)
        data = resp.json()
        return [s["name"] for s in data.get("data", [])]
    except Exception as e:
        print("Error fetching sets:", e)
        return []

ALL_SETS = fetch_all_sets()

# Cache of set -> card numbers
SET_CARD_NUMBERS = {}

class PokemonFullCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def get_usd_to_gbp(self):
        try:
            resp = requests.get("https://api.exchangerate.host/latest?base=USD&symbols=GBP")
            return resp.json()['rates']['GBP']
        except:
            return None

    def get_card_info(self, set_name, card_number):
        try:
            url = f"https://api.pokemontcg.io/v2/cards?q=set.name:{set_name}+number:{card_number}"
            headers = {"X-Api-Key": "YOUR_POKEMON_TCG_API_KEY"}
            data = requests.get(url, headers=headers).json()
            if not data.get("data"):
                return None
            card = data["data"][0]
            return {
                "name": card.get("name"),
                "image": card.get("images", {}).get("large"),
                "release_date": card.get("set", {}).get("releaseDate"),
                "usd_price": card.get("tcgplayer", {}).get("prices", {}).get("normal", {}).get("market"),
            }
        except Exception as e:
            print("Error fetching card info:", e)
            return None

    async def set_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=s, value=s)
            for s in ALL_SETS if current.lower() in s.lower()
        ][:25]

    async def card_number_autocomplete(self, interaction: discord.Interaction, current: str):
        set_name = interaction.namespace.set_name
        if not set_name:
            return []

        # Fetch all card numbers for this set if not cached
        if set_name not in SET_CARD_NUMBERS:
            try:
                url = f"https://api.pokemontcg.io/v2/cards?q=set.name:{set_name}"
                headers = {"X-Api-Key": "YOUR_POKEMON_TCG_API_KEY"}
                resp = requests.get(url, headers=headers)
                data = resp.json()
                SET_CARD_NUMBERS[set_name] = [card["number"] for card in data.get("data", [])]
            except:
                SET_CARD_NUMBERS[set_name] = []

        numbers = SET_CARD_NUMBERS[set_name]
        return [
            app_commands.Choice(name=n, value=n)
            for n in numbers if current in n
        ][:25]

    @app_commands.command(name="price", description="Get the price of a Pokemon card")
    @app_commands.guilds(GUILD_ID)
    @app_commands.describe(
        set_name="Select the card set",
        card_number="Card number e.g., 1/180"
    )
    @app_commands.autocomplete(set_name=set_autocomplete, card_number=card_number_autocomplete)
    async def price(self, interaction: discord.Interaction, set_name: str, card_number: str):
        await interaction.response.defer()
        card = self.get_card_info(set_name, card_number)
        if not card:
            await interaction.followup.send("Card not found.")
            return

        usd_to_gbp = self.get_usd_to_gbp()
        gbp_price = round(card["usd_price"] * usd_to_gbp, 2) if usd_to_gbp and card["usd_price"] else None

        embed = discord.Embed(
            title=card["name"],
            description=f"Set: {set_name}\nCard Number: {card_number}\nRelease Date: {card['release_date']}",
            color=discord.Color.blue()
        )
        if card["image"]:
            embed.set_image(url=card["image"])
        if card["usd_price"]:
            embed.add_field(name="Price (USD)", value=f"${card['usd_price']}", inline=True)
        if gbp_price:
            embed.add_field(name="Price (GBP)", value=f"£{gbp_price}", inline=True)

        await interaction.followup.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(PokemonFullCog(bot))
