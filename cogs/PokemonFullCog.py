import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import aiosqlite
import os

# Load API key from .env if needed
from dotenv import load_dotenv
load_dotenv()
POKEMON_API_KEY = os.getenv("POKEMON_TCG_API_KEY")

# 🔴 Replace this with your actual guild ID
GUILD_ID = 1097913605082579024  # Set your guild/server ID here
API_BASE = "https://api.pokemontcg.io/v2"

class PokemonPrice(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # --- helper: fetch sets for autocomplete
    def get_all_sets(self):
        url = "https://api.pokemontcg.io/v2/sets"
        try:
            r = requests.get(url, timeout=10)
            data = r.json()
            return [s["name"] for s in data.get("data", [])]
        except Exception as e:
            print(f"Error fetching sets: {e}")
            return []

    # --- helper: conversion USD -> GBP
    def get_usd_to_gbp(self):
        try:
            r = requests.get("https://api.exchangerate.host/latest?base=USD&symbols=GBP", timeout=5)
            data = r.json()
            return data.get("rates", {}).get("GBP", 0.78)  # fallback avg rate
        except Exception as e:
            print(f"Error fetching conversion rate: {e}")
            return 0.78

    # --- autocomplete for set name
    async def set_autocomplete(self, interaction: discord.Interaction, current: str):
        sets = self.get_all_sets()
        return [
            app_commands.Choice(name=s, value=s)
            for s in sets if current.lower() in s.lower()
        ][:25]  # Discord max 25 choices

    @app_commands.command(name="price", description="Get the price of a Pokémon card")
    @app_commands.describe(set_name="The set name (autocomplete)", card_number="The card number (e.g. 1/180)")
    @app_commands.autocomplete(set_name=set_autocomplete)
    async def price(self, interaction: discord.Interaction, set_name: str, card_number: str):
        await interaction.response.defer()  # allow time

        # fetch cards by set name
        url = f"https://api.pokemontcg.io/v2/cards?q=set.name:\"{set_name}\" number:{card_number}"
        r = requests.get(url, timeout=10)
        data = r.json().get("data", [])

        if not data:
            await interaction.followup.send(f"❌ No card found for {set_name} #{card_number}")
            return

        card = data[0]

        # card info
        name = card["name"]
        image = card["images"]["large"]
        set_release = card["set"].get("releaseDate", "Unknown")
        set_code = card["set"].get("id", "Unknown")

        # tcgplayer price
        usd_price = None
        try:
            prices = card["tcgplayer"]["prices"]
            # try market price first
            if "normal" in prices and "market" in prices["normal"]:
                usd_price = prices["normal"]["market"]
            elif "holofoil" in prices and "market" in prices["holofoil"]:
                usd_price = prices["holofoil"]["market"]
            elif "reverseHolofoil" in prices and "market" in prices["reverseHolofoil"]:
                usd_price = prices["reverseHolofoil"]["market"]
        except Exception:
            usd_price = None

        gbp_price = None
        if usd_price:
            rate = self.get_usd_to_gbp()
            gbp_price = round(usd_price * rate, 2)

        # embed response
        embed = discord.Embed(
            title=f"{name} ({card_number})",
            description=f"Set: **{set_name}**\nRelease Date: {set_release}",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=image)

        if usd_price:
            embed.add_field(name="Price (USD)", value=f"${usd_price:.2f}", inline=True)
        else:
            embed.add_field(name="Price (USD)", value="N/A", inline=True)

        if gbp_price:
            embed.add_field(name="Price (GBP)", value=f"£{gbp_price:.2f}", inline=True)

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(PokemonPrice(bot), guild=discord.Object(id=GUILD_ID))
