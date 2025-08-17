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


class PokemonFullCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # Sync slash commands when bot is ready
    @commands.Cog.listener()
    async def on_ready(self):
        guild = discord.Object(id=GUILD_ID)
        try:
            self.bot.tree.copy_global_to(guild=guild)
            await self.bot.tree.sync(guild=guild)
            print(f"✅ Synced commands to guild {GUILD_ID}")
        except Exception as e:
            print(f"❌ Failed to sync commands: {e}")

    # ----------------------------
    # PRICE LOOKUP
    # ----------------------------
    @app_commands.command(
        name="price",
        description="Lookup a Pokémon card price by set and card number"
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def price(
        self,
        interaction: discord.Interaction,
        set_code: str,
        card_number: str
    ):
        """Get the price of a card from a given set and number"""
        await interaction.response.defer(thinking=True)

        url = f"https://api.pokemontcg.io/v2/cards?q=set.id:{set_code} number:{card_number}"
        headers = {"X-Api-Key": POKEMON_API_KEY} if POKEMON_API_KEY else {}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    await interaction.followup.send("❌ Failed to fetch card data.")
                    return

                data = await resp.json()

        if not data.get("data"):
            await interaction.followup.send("❌ Card not found. Please check the set code and card number.")
            return

        card = data["data"][0]
        name = card["name"]
        image_url = card["images"]["large"]

        # Extract pricing info (market USD → convert to GBP)
        prices = card.get("tcgplayer", {}).get("prices", {})
        market_usd = None
        for rarity in prices.values():
            if "market" in rarity:
                market_usd = rarity["market"]
                break

        if not market_usd:
            await interaction.followup.send(f"ℹ️ {name} ({set_code} {card_number}) has no market price data.")
            return

        # Fake conversion rate for example (use real API in production)
        conversion_rate = 0.79  # USD → GBP approx
        market_gbp = round(market_usd * conversion_rate, 2)

        embed = discord.Embed(
            title=f"{name} - {set_code} {card_number}",
            description=f"💵 Market Price: **${market_usd:.2f} USD**\n💷 Converted: **£{market_gbp:.2f} GBP**",
            color=discord.Color.green()
        )
        embed.set_image(url=image_url)

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(PokemonFullCog(bot))
