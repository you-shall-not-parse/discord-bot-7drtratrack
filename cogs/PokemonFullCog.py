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

class PokemonCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.sets = []  # will store {"name":..., "id":...}
        self.bot.loop.create_task(self._load_sets())
        self._session = aiohttp.ClientSession()

    async def _load_sets(self):
        url = f"{API_BASE}/sets?pageSize=1000"
        headers = {"X-Api-Key": POKEMON_API_KEY} if POKEMON_API_KEY else {}
        async with self._session.get(url, headers=headers) as r:
            data = await r.json()
        self.sets = data.get("data", [])

    @commands.Cog.listener()
    async def on_ready(self):
        guild = discord.Object(id=GUILD_ID)
        self.bot.tree.copy_global_to(guild=guild)
        await self.bot.tree.sync(guild=guild)
        print(f"Synced commands to guild {GUILD_ID}")

    @app_commands.command(name="price", description="Get Pokémon card price by set and card number")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.describe(
        set_name="Select the card set",
        card_number="Card number in the set (e.g. 1/180)"
    )
    async def price(
        self, interaction: discord.Interaction, 
        set_name: str, card_number: str
    ):
        await interaction.response.defer(thinking=True)

        # Fetch the card
        query = f'set.name:"{set_name}" number:"{card_number}"'
        url = f"{API_BASE}/cards?q={query}"
        headers = {"X-Api-Key": POKEMON_API_KEY} if POKEMON_API_KEY else {}
        async with self._session.get(url, headers=headers) as r:
            data = await r.json()
        if not data.get("data"):
            return await interaction.followup.send("Card not found — check set and number.")

        card = data["data"][0]
        img = card["images"].get("large")
        release = card.get("set", {}).get("releaseDate", "Unknown")
        name = card["name"]

        # Get USD price from tcgplayer
        prices = card.get("tcgplayer", {}).get("prices", {})
        usd_price = None
        for v in prices.values():
            if "market" in v:
                usd_price = v["market"]
                break

        # Fetch exchange rate
        rate = None
        if usd_price:
            exch = await self._session.get("https://api.exchangerate.host/latest", params={"base":"USD","symbols":"GBP"})
            exj = await exch.json()
            rate = exj["rates"]["GBP"]
            gbp_price = round(usd_price * rate, 2)
        else:
            gbp_price = None

        embed = discord.Embed(title=f"{name} — {set_name} {card_number}", color=discord.Color.blue())
        if img:
            embed.set_image(url=img)
        embed.add_field(name="Release Date", value=release, inline=True)
        if usd_price:
            embed.add_field(name="Price (USD)", value=f"${usd_price:.2f}", inline=True)
            embed.add_field(name="Price (GBP)", value=f"£{gbp_price:.2f}", inline=True)
            embed.set_footer(text=f"Exchange rate: 1 USD = {rate:.4f} GBP")
        else:
            embed.add_field(name="Price", value="No market data", inline=False)

        await interaction.followup.send(embed=embed)

async def setup(bot):
    await bot.add_cog(PokemonCog(bot))
