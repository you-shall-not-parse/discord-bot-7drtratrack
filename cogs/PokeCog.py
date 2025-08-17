import os
import re
import json
import random
import sqlite3
import asyncio
import logging
import time
from typing import Dict, Any, Optional

import aiohttp
import discord
from discord.ext import commands
from discord.ui import View, Button

# Set up logging
logger = logging.getLogger("PokeCog")
logging.basicConfig(level=logging.INFO)

# Config
POKEMON_API_KEY = os.getenv("POKEMON_TCG_API_KEY") or os.getenv("TCG_API_KEY")
API_HEADERS = {"X-Api-Key": POKEMON_API_KEY} if POKEMON_API_KEY else {}
API_URL = "https://api.pokemontcg.io/v2/cards"
RARITY_WEIGHTS = {"Common": 62, "Uncommon": 24, "Rare": 8, "Rare Holo": 4, "Rare Ultra": 2}

# Regex helpers
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

def attack_cost_count(atk: Dict[str, Any]) -> int:
    return len(atk.get("cost") or [])

def attack_coin_bonus(atk: Dict[str, Any]) -> int:
    text = atk.get("text") or ""
    m = _more_damage_re.search(text)
    if m and _flip_re.search(text):
        return int(m.group(1))
    return 0

# Add more helper functions as necessary...

# Database helpers
DB_PATH = "pokemon_tcg_persistent.db"

def db_execute_with_retry(conn, query, params=(), retries=5, delay=0.2):
    for attempt in range(retries):
        try:
            c = conn.cursor()
            c.execute(query, params)
            conn.commit()
            return c
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                logger.warning("Database is locked, retrying...")
                time.sleep(delay)
            else:
                logger.exception("Database operational error")
                raise
    logger.error("Database is locked after multiple retries")
    raise sqlite3.OperationalError("Database is locked after multiple retries")

def init_db(conn: sqlite3.Connection):
    db_execute_with_retry(conn, """
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        balance INTEGER DEFAULT 500,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0
    )""")
    db_execute_with_retry(conn, """
    CREATE TABLE IF NOT EXISTS inventory (
        user_id INTEGER,
        card_id TEXT,
        card_name TEXT,
        rarity TEXT,
        supertype TEXT,
        PRIMARY KEY(user_id, card_id)
    )""")
    db_execute_with_retry(conn, """
    CREATE TABLE IF NOT EXISTS trades (
        trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_user INTEGER,
        to_user INTEGER,
        card_id TEXT
    )""")
    db_execute_with_retry(conn, """
    CREATE TABLE IF NOT EXISTS battles (
        battle_id INTEGER PRIMARY KEY,
        state_json TEXT
    )""")

# Button classes for your Views
class ForfeitButton(Button):
    def __init__(self):
        super().__init__(label="Forfeit", style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction):
        view: PersistentBattleView = self.view  # type: ignore
        try:
            await view.on_forfeit(interaction)
        except Exception as e:
            logger.exception("Error in ForfeitButton callback")
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)

class AttackButton(Button):
    def __init__(self, label: str, idx: int):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.idx = idx

    async def callback(self, interaction: discord.Interaction):
        view: PersistentBattleView = self.view  # type: ignore
        try:
            await view.on_attack(interaction, self.idx)
        except Exception as e:
            logger.exception("Error in AttackButton callback")
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)

class PersistentBattleView(View):
    def __init__(self, bot: commands.Bot, battle_id: int, timeout: int = 900):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.battle_id = battle_id
        self.message: Optional[discord.Message] = None

    async def on_attack(self, interaction: discord.Interaction, attack_index: int):
        cog: "PokeCog" = self.bot.get_cog("PokeCog")
        if not cog:
            await interaction.response.send_message("Cog unavailable.", ephemeral=True)
            return
        try:
            await cog.resolve_attack_for_battle(interaction, self.battle_id, interaction.user.id, attack_index)
        except Exception as e:
            logger.exception("Error in on_attack")
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)

    async def on_forfeit(self, interaction: discord.Interaction):
        cog: "PokeCog" = self.bot.get_cog("PokeCog")
        if not cog:
            await interaction.response.send_message("Cog unavailable.", ephemeral=True)
            return
        try:
            await cog.forfeit_battle(interaction, self.battle_id, interaction.user.id)
        except Exception as e:
            logger.exception("Error in on_forfeit")
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)

    async def on_timeout(self):
        try:
            if self.message:
                await self.message.edit(content="Battle view timed out (buttons disabled). The battle state is saved — use /resume_battles to restore.", view=None)
        except Exception as e:
            logger.exception("Error in on_timeout")

# The Cog itself
class PokeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        init_db(self.conn)
        self.battle_views: Dict[int, PersistentBattleView] = {}
        self._restore_task = self.bot.loop.create_task(self._restore_battles_on_ready())

    async def _restore_battles_on_ready(self):
        # This is a stub; implement your restoration logic here if needed
        logger.info("Restoring battles on bot ready (stub)")
        pass

    # Example command using db_execute_with_retry and logging
    @commands.command(name="buy_pack")
    async def buy_pack(self, ctx, pack_type="basic"):
        user_id = ctx.author.id
        try:
            c = db_execute_with_retry(self.conn, "SELECT balance FROM users WHERE user_id = ?", (user_id,))
            row = c.fetchone()
            balance = row[0] if row else 0
            # ... rest of your command logic ...
            await ctx.send(f"Buying a {pack_type} pack... (stub, balance: {balance})")
        except Exception as e:
            logger.exception("Error in buy_pack command")
            await ctx.send(f"An error occurred: {e}")

    # Add other commands and event handlers as needed...

# Setup function for discord.py v2.x
async def setup(bot: commands.Bot):
    await bot.add_cog(PokeCog(bot))
