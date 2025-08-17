import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import asyncio

GUILD_ID = 1097913605082579024  # replace with your guild ID
POKEMON_API_KEY = "POKEMON_API_KEY"  # replace with your TCG API key

class PokemonFullCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.sets_cache = []  # cache of sets
        self.cards_cache = {}  # cache of cards per set

    async def fetch_json(self, url):
        headers = {"X-Api-Key": POKEMON_API_KEY}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as r:
                return await r.json()

    async def get_sets(self):
        if self.sets_cache:
            return self.sets_cache
        data = await self.fetch_json("https://api.pokemontcg.io/v2/sets")
        self.sets_cache = data.get("data", [])
        return self.sets_cache

    async def get_cards(self, set_id):
        if set_id in self.cards_cache:
            return self.cards_cache[set_id]
        data = await self.fetch_json(f"https://api.pokemontcg.io/v2/cards?q=set.id:{set_id}")
        self.cards_cache[set_id] = data.get("data", [])
        return self.cards_cache[set_id]

    @app_commands.command(name="price", description="Get Pokémon card price")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.describe(set_name="Choose the set", card_number="Choose the card number")
    async def price(self, interaction: discord.Interaction, set_name: str, card_number: str):
        await interaction.response.defer()
        sets = await self.get_sets()
        selected_set = next((s for s in sets if s["name"].lower() == set_name.lower()), None)
        if not selected_set:
            return await interaction.followup.send("Set not found.")
        cards = await self.get_cards(selected_set["id"])
        selected_card = next((c for c in cards if c["number"] == card_number), None)
        if not selected_card:
            return await interaction.followup.send("Card not found.")
        # get price in USD then convert to GBP
        price_usd = selected_card.get("tcgplayer", {}).get("prices", {}).get("normal", {}).get("market", 0)
        # get conversion rate
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.exchangerate.host/latest?base=USD&symbols=GBP") as r:
                conv = await r.json()
        rate = conv.get("rates", {}).get("GBP", 0)
        price_gbp = round(price_usd * rate, 2) if price_usd and rate else None
        embed = discord.Embed(title=f"{selected_card['name']} ({selected_card['number']})", 
                              description=f"Set: {selected_set['name']}\nRelease Date: {selected_set.get('releaseDate', 'Unknown')}")
        if price_gbp:
            embed.add_field(name="Price (GBP)", value=f"£{price_gbp}")
        if selected_card.get("images", {}).get("large"):
            embed.set_image(url=selected_card["images"]["large"])
        await interaction.followup.send(embed=embed)

    @price.autocomplete("set_name")
    async def set_name_autocomplete(self, interaction: discord.Interaction, current: str):
        sets = await self.get_sets()
        choices = [app_commands.Choice(name=s["name"], value=s["name"]) for s in sets if current.lower() in s["name"].lower()]
        return choices[:25]  # Discord max

    @price.autocomplete("card_number")
    async def card_number_autocomplete(self, interaction: discord.Interaction, current: str):
        # get the set from the command
        set_name = interaction.namespace.set_name
        sets = await self.get_sets()
        selected_set = next((s for s in sets if s["name"].lower() == set_name.lower()), None)
        if not selected_set:
            return []
        cards = await self.get_cards(selected_set["id"])
        choices = [app_commands.Choice(name=c["number"], value=c["number"]) for c in cards if current in c["number"]]
        return choices[:25]

async def setup(bot):
    await bot.add_cog(PokemonFullCog(bot))
