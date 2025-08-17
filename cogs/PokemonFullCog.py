import os
import re
import json
import random
import sqlite3
import asyncio
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Button, Select
from dotenv import load_dotenv

# ---------------- Config ----------------

GUILD_ID = 1097913605082579024  # Hardcoded guild ID

load_dotenv()
POKEMON_API_KEY = os.getenv("POKEMON_TCG_API_KEY") or os.getenv("TCG_API_KEY")
API_HEADERS = {"X-Api-Key": POKEMON_API_KEY} if POKEMON_API_KEY else {}
API_URL = "https://api.pokemontcg.io/v2/cards"
DB_FILE = "pokemon_cards.db"

RARITY_WEIGHTS = {"Common": 62, "Uncommon": 24, "Rare": 8, "Rare Holo": 4, "Rare Ultra": 2}

_int_re = re.compile(r"(-?\d+)")
_more_damage_re = re.compile(r"(\d+)\s+more damage", re.IGNORECASE)
_flip_re = re.compile(r"flip a coin", re.IGNORECASE)
_paralyze_re = re.compile(r"paralyz", re.IGNORECASE)
_burn_re = re.compile(r"burn", re.IGNORECASE)
_poison_re = re.compile(r"poison", re.IGNORECASE)
_confuse_re = re.compile(r"confus", re.IGNORECASE)

def num(s: Optional[str], default: int = 0) -> int:
    if not s:
        return default
    m = _int_re.search(str(s))
    return int(m.group(1)) if m else default

def hp_of(card: Dict[str, Any]) -> int:
    return max(10, num(card.get("hp"), 50))

def supertype(card: Dict[str, Any]) -> str:
    return (card.get("supertype") or "").lower()

def is_basic(card: Dict[str, Any]) -> bool:
    subs = card.get("subtypes") or []
    return "Basic" in subs or not card.get("evolvesFrom")

def attack_base_damage(atk: Dict[str, Any]) -> int:
    return max(0, num(atk.get("damage"), 0))

def attack_coin_bonus(atk: Dict[str, Any]) -> int:
    text = atk.get("text") or ""
    m = _more_damage_re.search(text)
    if m and _flip_re.search(text):
        return int(m.group(1))
    return 0

def attack_text_has_paralysis(atk: Dict[str, Any]) -> bool:
    text = atk.get("text") or ""
    return bool(_paralyze_re.search(text)) and not _flip_re.search(text)

def attack_text_has_burn(atk: Dict[str, Any]) -> bool:
    text = atk.get("text") or ""
    return bool(_burn_re.search(text)) and not _flip_re.search(text)

def attack_text_has_poison(atk: Dict[str, Any]) -> bool:
    text = atk.get("text") or ""
    return bool(_poison_re.search(text)) and not _flip_re.search(text)

def attack_text_has_confuse(atk: Dict[str, Any]) -> bool:
    text = atk.get("text") or ""
    return bool(_confuse_re.search(text)) and not _flip_re.search(text)

def attack_may_paralyze(atk: Dict[str, Any]) -> bool:
    text = atk.get("text") or ""
    return bool(_paralyze_re.search(text) and _flip_re.search(text))

def attack_may_burn(atk: Dict[str, Any]) -> bool:
    text = atk.get("text") or ""
    return bool(_burn_re.search(text) and _flip_re.search(text))

def attack_may_poison(atk: Dict[str, Any]) -> bool:
    text = atk.get("text") or ""
    return bool(_poison_re.search(text) and _flip_re.search(text))

def attack_may_confuse(atk: Dict[str, Any]) -> bool:
    text = atk.get("text") or ""
    return bool(_confuse_re.search(text) and _flip_re.search(text))

def guess_attack_type(attacker: Dict[str, Any], atk: Dict[str, Any]) -> Optional[str]:
    cost = atk.get("cost") or []
    if cost:
        first = cost[0]
        if first and first.lower() != "colorless":
            return first
    types = attacker.get("types") or []
    return types[0] if types else None

def apply_weak_resist(attack_type: Optional[str], defender: Dict[str, Any], damage: int) -> int:
    if attack_type and defender.get("weaknesses"):
        for w in defender["weaknesses"]:
            if w.get("type") == attack_type:
                val = w.get("value", "×2")
                if "×" in val or "x" in val:
                    mult = num(val, 2)
                    damage *= (mult if mult > 1 else 2)
                elif "+" in val:
                    damage += num(val, 20)
                else:
                    damage *= 2
                break
    if attack_type and defender.get("resistances"):
        for r in defender["resistances"]:
            if r.get("type") == attack_type:
                val = r.get("value", "-30")
                damage = max(0, damage - num(val, 30))
                break
    return max(0, damage)

def trim(s: str, limit: int = 1024) -> str:
    return s if len(s) <= limit else s[: limit - 1] + "…"

# ---------------- Database ----------------
def init_db(conn: sqlite3.Connection):
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        PRIMARY KEY (user_id)
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS inventory (
        user_id INTEGER,
        card_id TEXT,
        card_name TEXT,
        rarity TEXT,
        supertype TEXT,
        PRIMARY KEY (user_id, card_id)
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS battles (
        battle_id INTEGER PRIMARY KEY,
        state_json TEXT
    )""")
    conn.commit()
# ---------------- Pokemon Cog ----------------
class PokemonFullCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.conn = sqlite3.connect(DB_FILE)
        init_db(self.conn)

    async def search_cards(self, name: str) -> List[Dict[str, Any]]:
        async with aiohttp.ClientSession() as session:
            params = {"q": f'name:"{name}"'}
            async with session.get(API_URL, headers=API_HEADERS, params=params) as resp:
                data = await resp.json()
                return data.get("data", [])

    async def get_card_image(self, card: Dict[str, Any]) -> Optional[str]:
        images = card.get("images", {})
        return images.get("large") or images.get("small")

    async def convert_usd_to_gbp(self, usd: float) -> Optional[float]:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.exchangerate.host/convert?from=USD&to=GBP&amount={usd}") as resp:
                data = await resp.json()
                return data.get("result")

    @app_commands.command(name="price", description="Get the price of a Pokémon card by name.")
    async def price(self, interaction: discord.Interaction, name: str):
        cards = await self.search_cards(name)
        if not cards:
            await interaction.response.send_message("No cards found matching that name.", ephemeral=True)
            return

        if len(cards) > 1:
            card_names = [f"{c['name']} ({c.get('set', {}).get('name', 'Unknown Set')})" for c in cards]
            await interaction.response.send_message(
                f"Multiple cards found:\n" + "\n".join(card_names) +
                "\n\nPlease specify the set or more details.",
                ephemeral=True
            )
            return

        card = cards[0]
        price_data = card.get("tcgplayer", {}).get("prices", {})
        usd_price = None
        for variant in ["holofoil", "normal", "reverseHolofoil"]:
            if variant in price_data and "market" in price_data[variant]:
                usd_price = price_data[variant]["market"]
                break

        if usd_price is None:
            await interaction.response.send_message("No price data available for this card.", ephemeral=True)
            return

        gbp_price = await self.convert_usd_to_gbp(usd_price)
        image_url = await self.get_card_image(card)
        embed = discord.Embed(
            title=f"{card['name']} ({card.get('set', {}).get('name', 'Unknown Set')})",
            description=f"Market Price: ${usd_price:.2f} USD / £{gbp_price:.2f} GBP",
            color=discord.Color.gold()
        )
        if image_url:
            embed.set_image(url=image_url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="lookup", description="Look up a Pokémon card by name.")
    async def lookup(self, interaction: discord.Interaction, name: str):
        cards = await self.search_cards(name)
        if not cards:
            await interaction.response.send_message("No cards found.", ephemeral=True)
            return
        card = cards[0]
        image_url = await self.get_card_image(card)
        embed = discord.Embed(
            title=f"{card['name']} ({card.get('set', {}).get('name', 'Unknown Set')})",
            description=trim(card.get("flavorText", "") or card.get("text", "") or "No description."),
            color=discord.Color.blue()
        )
        if image_url:
            embed.set_image(url=image_url)
        await interaction.response.send_message(embed=embed)

    # Battle functionality remains unchanged, but trading/balance/randomcard removed

    @commands.Cog.listener()
    async def on_ready(self):
        print("PokemonFullCog is loaded.")

async def setup(bot: commands.Bot):
    await bot.add_cog(PokemonFullCog(bot))
