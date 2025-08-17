import discord
from discord.ext import commands
from discord import app_commands
import requests

# === CONFIG ===
GUILD_ID = 1097913605082579024  # placeholder guild ID
POKEMON_TCG_API_KEY = "POKEMON_TCG_API_KEY"  # <-- set your API key here

# Cache
ALL_SETS = []
SET_CARD_NUMBERS = {}  # {set_name: [card_numbers]}

# === HELPER FUNCTIONS ===

def get_usd_to_gbp():
    try:
        resp = requests.get("https://api.exchangerate.host/latest?base=USD&symbols=GBP")
        data = resp.json()
        return data["rates"]["GBP"]
    except Exception:
        return 0.8  # fallback rate

def fetch_all_sets():
    global ALL_SETS
    if ALL_SETS:
        return ALL_SETS
    try:
        url = "https://api.pokemontcg.io/v2/sets"
        headers = {"X-Api-Key": POKEMON_TCG_API_KEY}
        resp = requests.get(url, headers=headers)
        data = resp.json()
        ALL_SETS = [s["name"] for s in data.get("data", [])]
    except Exception:
        ALL_SETS = []
    return ALL_SETS

# === COG ===

class PokemonFullCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def set_autocomplete(self, interaction: discord.Interaction, current: str):
        sets = fetch_all_sets()
        return [
            app_commands.Choice(name=s, value=s)
            for s in sets if current.lower() in s.lower()
        ][:25]

    async def card_number_autocomplete(self, interaction: discord.Interaction, current: str):
        set_name = interaction.namespace.set_name
        if not set_name:
            return []
        if set_name not in SET_CARD_NUMBERS:
            try:
                url = f"https://api.pokemontcg.io/v2/cards?q=set.name:{set_name}"
                headers = {"X-Api-Key": POKEMON_TCG_API_KEY}
                resp = requests.get(url, headers=headers)
                data = resp.json()
                SET_CARD_NUMBERS[set_name] = [card["number"] for card in data.get("data", [])]
            except:
                SET_CARD_NUMBERS[set_name] = []
        numbers = SET_CARD_NUMBERS[set_name]
        return [app_commands.Choice(name=n, value=n) for n in numbers if current in n][:25]

    @app_commands.command(name="price", description="Get Pokémon card price and info")
    @app_commands.describe(set_name="Select set", card_number="Enter card number")
    @app_commands.autocomplete(set_name=set_autocomplete)
    @app_commands.autocomplete(card_number=card_number_autocomplete)
    async def price(self, interaction: discord.Interaction, set_name: str, card_number: str):
        await interaction.response.defer()
        try:
            url = f"https://api.pokemontcg.io/v2/cards?q=set.name:{set_name}+number:{card_number}"
            headers = {"X-Api-Key": POKEMON_TCG_API_KEY}
            resp = requests.get(url, headers=headers)
            data = resp.json()
            card_data = data.get("data", [])[0]

            usd_price = card_data.get("tcgplayer", {}).get("prices", {}).get("normal", {}).get("market", 0)
            gbp_price = round(usd_price * get_usd_to_gbp(), 2)

            embed = discord.Embed(title=card_data.get("name", "Card"), color=discord.Color.blue())
            embed.set_image(url=card_data.get("images", {}).get("large"))
            embed.add_field(name="Set", value=set_name, inline=True)
            embed.add_field(name="Card Number", value=card_number, inline=True)
            embed.add_field(name="Release Date", value=card_data.get("set", {}).get("releaseDate", "Unknown"), inline=True)
            embed.add_field(name="Price", value=f"${usd_price} / £{gbp_price}", inline=True)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"Error fetching card info: {e}")

async def setup(bot):
    await bot.add_cog(PokemonFullCog(bot), guilds=[discord.Object(id=GUILD_ID)])
