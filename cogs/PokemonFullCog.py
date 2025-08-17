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
        guild_id INTEGER,
        user_id INTEGER,
        balance INTEGER DEFAULT 500,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        PRIMARY KEY (guild_id, user_id)
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS inventory (
        guild_id INTEGER,
        user_id INTEGER,
        card_id TEXT,
        card_name TEXT,
        rarity TEXT,
        supertype TEXT,
        PRIMARY KEY (guild_id, user_id, card_id)
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        guild_id INTEGER,
        trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_user INTEGER,
        to_user INTEGER,
        card_id TEXT
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS battles (
        guild_id INTEGER,
        battle_id INTEGER PRIMARY KEY,
        state_json TEXT
    )""")
    conn.commit()

# ---------------- Views & Buttons ----------------
class ForfeitButton(Button):
    def __init__(self):
        super().__init__(label="Forfeit", style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction):
        cog: "PokemonFullCog" = interaction.client.get_cog("PokemonFullCog")
        if cog:
            await cog.handle_forfeit(interaction, self.view.custom_id)

class AttackButton(Button):
    def __init__(self, label: str, idx: int):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.idx = idx

    async def callback(self, interaction: discord.Interaction):
        cog: "PokemonFullCog" = interaction.client.get_cog("PokemonFullCog")
        if cog:
            await cog.handle_attack(interaction, self.view.custom_id, self.idx)

class BattleSelect(Select):
    pass

class BattleView(View):
    def __init__(self, battle_id: int, timeout: int = 900):
        super().__init__(timeout=timeout)
        self.custom_id = battle_id

    async def on_timeout(self):
        pass

# ---------------- Cog ----------------
class PokemonFullCog(commands.Cog, name="PokemonFullCog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        init_db(self.conn)
        self._session = aiohttp.ClientSession()
        self._restore_task = self.bot.loop.create_task(self._restore_battles_on_ready())

    def get_guild(self):
        return self.bot.get_guild(GUILD_ID)

    def get_member(self, user_id):
        guild = self.get_guild()
        return guild.get_member(user_id) if guild else None

    def guild_check(self, interaction):
        return interaction.guild is not None and interaction.guild.id == GUILD_ID

    # ---------- lifecycle ----------
    @commands.Cog.listener()
    async def on_ready(self):
        try:
            await self.bot.tree.sync(guild=discord.Object(id=GUILD_ID))
            print("[PokemonFullCog] Commands synced.")
        except Exception as e:
            print(f"[PokemonFullCog] Failed to sync commands: {e}")

    async def cog_unload(self):
        try:
            await self._session.close()
        except Exception:
            pass

    # ---------- HTTP helpers ----------
    async def _get_json(self, url: str, params: dict = None):
        async with self._session.get(url, params=params, headers=API_HEADERS, timeout=aiohttp.ClientTimeout(total=25)) as r:
            r.raise_for_status()
            return await r.json()

    async def get_card_by_id(self, cid: str) -> Optional[Dict[str, Any]]:
        data = await self._get_json(f"{API_URL}/{cid}")
        return data.get("data")

    async def search_card(self, query: str) -> Optional[Dict[str, Any]]:
        data = await self._get_json(API_URL, {"q": f'name:"{query}"', "pageSize": 5})
        arr = data.get("data") or []
        return arr[0] if arr else None

    async def random_card_by_query(self, q: str) -> Optional[Dict[str, Any]]:
        meta = await self._get_json(API_URL, {"q": q, "pageSize": 1, "page": 1})
        total = meta.get("totalCount", 0)
        if total == 0:
            return None
        idx = random.randint(1, total)
        data = await self._get_json(API_URL, {"q": q, "pageSize": 1, "page": idx})
        arr = data.get("data") or []
        return arr[0] if arr else None

    async def convert_to_gbp(self, amount: float, from_curr: str) -> float:
        try:
            async with self._session.get("https://api.exchangerate.host/convert", params={"from": from_curr, "to": "GBP", "amount": amount}) as r:
                data = await r.json()
                return float(data.get("result", 0.0))
        except Exception:
            return 0.0

    # ---------- Utility functions for battle DB ----------
    def _save_battle(self, state: Dict[str, Any]):
        self.conn.execute(
            "INSERT OR REPLACE INTO battles(guild_id, battle_id, state_json) VALUES(?,?,?)",
            (GUILD_ID, state["battle_id"], json.dumps(state))
        )
        self.conn.commit()

    def _load_battle(self, battle_id: int) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT state_json FROM battles WHERE guild_id=? AND battle_id=?",
            (GUILD_ID, battle_id)
        ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return None

    def _delete_battle(self, battle_id: int):
        self.conn.execute(
            "DELETE FROM battles WHERE guild_id=? AND battle_id=?",
            (GUILD_ID, battle_id)
        )
        self.conn.commit()

    async def _restore_battles_on_ready(self):
        await self.bot.wait_until_ready()
        rows = self.conn.execute("SELECT battle_id, state_json FROM battles WHERE guild_id=?", (GUILD_ID,)).fetchall()
        for bid, sj in rows:
            try:
                state = json.loads(sj)
            except Exception:
                continue
            try:
                ch = self.bot.get_channel(state["channel_id"])
                if not ch:
                    continue
                msg = None
                try:
                    msg = await ch.fetch_message(state["message_id"])
                except Exception:
                    msg = None
                emb = discord.Embed(title=f"Battle: {state['player1_name']} vs {state['player2_name']}", color=0xE67E22)
                emb.add_field(name=f"{state['player1_name']} (Active)", value=f"**{state['p1_active'].get('name','?')}** — HP {state['p1_active_state'].get('hp',0)}", inline=True)
                emb.add_field(name=f"{state['player2_name']} (Active)", value=f"**{state['p2_active'].get('name','?')}** — HP {state['p2_active_state'].get('hp',0)}", inline=True)
                emb.add_field(name="Recent log", value=trim("\n".join(state['log'][-6:]), 1024), inline=False)

                view = BattleView(state["battle_id"])
                view.add_item(ForfeitButton())
                current = state["current_player"]
                active_card = state["p1_active"] if current == state["player1_id"] else state["p2_active"]
                attacks = (active_card.get("attacks") or [])[:4]
                for idx, atk in enumerate(attacks):
                    view.add_item(AttackButton(label=trim(atk.get("name","Attack"), 80), idx=idx))

                if msg:
                    try:
                        await msg.edit(embed=emb, view=view)
                    except Exception:
                        sent = await ch.send(embed=emb, view=view)
                        state["message_id"] = sent.id
                        self._save_battle(state)
                else:
                    sent = await ch.send(embed=emb, view=view)
                    state["message_id"] = sent.id
                    self._save_battle(state)
            except Exception:
                continue

    async def _finalize_battle(self, state: Dict[str, Any]):
        winner = state.get("winner_id")
        p1 = state["player1_id"]
        p2 = state["player2_id"]
        cur = self.conn.cursor()
        cur.execute("INSERT OR IGNORE INTO users(guild_id, user_id) VALUES(?,?)", (GUILD_ID, p1))
        cur.execute("INSERT OR IGNORE INTO users(guild_id, user_id) VALUES(?,?)", (GUILD_ID, p2))
        if winner == p1:
            cur.execute("UPDATE users SET wins = wins + 1 WHERE guild_id=? AND user_id=?", (GUILD_ID, p1))
            cur.execute("UPDATE users SET losses = losses + 1 WHERE guild_id=? AND user_id=?", (GUILD_ID, p2))
        elif winner == p2:
            cur.execute("UPDATE users SET wins = wins + 1 WHERE guild_id=? AND user_id=?", (GUILD_ID, p2))
            cur.execute("UPDATE users SET losses = losses + 1 WHERE guild_id=? AND user_id=?", (GUILD_ID, p1))
        self.conn.commit()

        try:
            ch = self.bot.get_channel(state["channel_id"])
            if ch:
                msg = await ch.fetch_message(state["message_id"])
                emb = discord.Embed(title=f"Battle finished: {state['player1_name']} vs {state['player2_name']}", description=f"Winner: <@{winner}>", color=0x00FF00)
                emb.add_field(name="Final Active", value=f"{state['player1_name']}: {state['p1_active'].get('name','?')} — HP {state['p1_active_state'].get('hp',0)}", inline=True)
                emb.add_field(name="Final Active", value=f"{state['player2_name']}: {state['p2_active'].get('name','?')} — HP {state['p2_active_state'].get('hp',0)}", inline=True)
                emb.add_field(name="Battle Log (last lines)", value=trim("\n".join(state['log'][-10:]), 1024), inline=False)
                await msg.edit(embed=emb, view=None)
        except Exception:
            pass
        self._delete_battle(state["battle_id"])

    async def _update_battle_message(self, state: Dict[str, Any]):
        battle_id = state["battle_id"]
        ch = self.bot.get_channel(state["channel_id"])
        if not ch:
            return
        msg = None
        try:
            msg = await ch.fetch_message(state["message_id"])
        except Exception:
            msg = None
        emb = discord.Embed(title=f"Battle: {state['player1_name']} vs {state['player2_name']}", color=0xE67E22)
        def fmt_card(card, st):
            name = card.get("name","Unknown")
            hp = max(0, st.get("hp",0))
            s = []
            if st.get("burn"): s.append("Burn")
            if st.get("poison"): s.append("Poison")
            if st.get("paralyzed"): s.append("Paralyzed")
            if st.get("confused"): s.append("Confused")
            stat_txt = f" | {', '.join(s)}" if s else ""
            return f"**{name}** — HP {hp}{stat_txt}"

        emb.add_field(name=f"{state['player1_name']} (Active)", value=fmt_card(state['p1_active'], state['p1_active_state']), inline=True)
        emb.add_field(name=f"{state['player2_name']} (Active)", value=fmt_card(state['p2_active'], state['p2_active_state']), inline=True)
        emb.add_field(name=f"{state['player1_name']} Bench", value=str(len(state['p1_team_states'])), inline=True)
        emb.add_field(name=f"{state['player2_name']} Bench", value=str(len(state['p2_team_states'])), inline=True)
        emb.add_field(name="Recent log", value=trim("\n".join(state['log'][-8:]), 1024), inline=False)

        view = BattleView(state["battle_id"])
        view.add_item(ForfeitButton())
        current = state["current_player"]
        is_p1_turn = current == state["player1_id"]
        active_card = state["p1_active"] if is_p1_turn else state["p2_active"]
        attacks = (active_card.get("attacks") or [])[:4]
        for idx, atk in enumerate(attacks):
            view.add_item(AttackButton(label=trim(atk.get("name","Attack"), 80), idx=idx))

        if msg:
            try:
                await msg.edit(embed=emb, view=view)
                state["message_id"] = msg.id
                self._save_battle(state)
                return
            except Exception:
                pass
        try:
            sent = await ch.send(embed=emb, view=view)
            state["message_id"] = sent.id
            self._save_battle(state)
        except Exception:
            self._save_battle(state)

    # -- ALL COMMANDS below: add guild checks and refactor DB queries for guild_id --
        # ---------- Commands ----------
    @app_commands.command(name="openpack", description="Open a booster pack (5 Pokémon cards).")
    async def openpack(self, interaction: discord.Interaction):
        if not self.guild_check(interaction):
            await interaction.response.send_message("This command is only available in the official server.", ephemeral=True)
            return
        user_id = interaction.user.id
        cur = self.conn.cursor()
        cur.execute("INSERT OR IGNORE INTO users(guild_id, user_id) VALUES (?, ?)", (GUILD_ID, user_id))
        self.conn.commit()
        # Draw 5 cards (simulate pack)
        drawn = []
        for _ in range(5):
            rarity = random.choices(list(RARITY_WEIGHTS.keys()), weights=RARITY_WEIGHTS.values())[0]
            card = await self.random_card_by_query(f"rarity:{rarity}")
            if card:
                card_id = card["id"]
                card_name = card.get("name", "Unknown")
                supertype_ = card.get("supertype", "")
                cur.execute(
                    "INSERT OR IGNORE INTO inventory(guild_id, user_id, card_id, card_name, rarity, supertype) VALUES (?, ?, ?, ?, ?, ?)",
                    (GUILD_ID, user_id, card_id, card_name, rarity, supertype_)
                )
                drawn.append(card_name)
        self.conn.commit()
        await interaction.response.send_message(f"You opened a pack and drew: {', '.join(drawn)}")

    @app_commands.command(name="inventory", description="Show your Pokémon card inventory.")
    async def inventory(self, interaction: discord.Interaction):
        if not self.guild_check(interaction):
            await interaction.response.send_message("This command is only available in the official server.", ephemeral=True)
            return
        user_id = interaction.user.id
        cur = self.conn.cursor()
        rows = cur.execute(
            "SELECT card_name, rarity, supertype FROM inventory WHERE guild_id=? AND user_id=?",
            (GUILD_ID, user_id)
        ).fetchall()
        if not rows:
            await interaction.response.send_message("Your inventory is empty.", ephemeral=True)
            return
        msg = "\n".join(f"{name} ({rarity}, {supertype})" for name, rarity, supertype in rows)
        await interaction.response.send_message(f"Your Inventory:\n{msg}", ephemeral=True)

    @app_commands.command(name="trade", description="Offer a card to another user for trade.")
    @app_commands.describe(card_name="Name of the card to trade", to_user="User to trade with")
    async def trade(self, interaction: discord.Interaction, card_name: str, to_user: discord.Member):
        if not self.guild_check(interaction):
            await interaction.response.send_message("This command is only available in the official server.", ephemeral=True)
            return
        user_id = interaction.user.id
        cur = self.conn.cursor()
        # Find card in inventory
        card_row = cur.execute(
            "SELECT card_id FROM inventory WHERE guild_id=? AND user_id=? AND card_name=?",
            (GUILD_ID, user_id, card_name)
        ).fetchone()
        if not card_row:
            await interaction.response.send_message("You don't have that card.", ephemeral=True)
            return
        card_id = card_row[0]
        # Create trade offer
        cur.execute(
            "INSERT INTO trades(guild_id, from_user, to_user, card_id) VALUES (?, ?, ?, ?)",
            (GUILD_ID, user_id, to_user.id, card_id)
        )
        self.conn.commit()
        await interaction.response.send_message(f"Trade offer created for {card_name} to {to_user.display_name}.")

    @app_commands.command(name="accepttrade", description="Accept a trade offer.")
    @app_commands.describe(trade_id="ID of the trade to accept")
    async def accepttrade(self, interaction: discord.Interaction, trade_id: int):
        if not self.guild_check(interaction):
            await interaction.response.send_message("This command is only available in the official server.", ephemeral=True)
            return
        user_id = interaction.user.id
        cur = self.conn.cursor()
        trade_row = cur.execute(
            "SELECT from_user, to_user, card_id FROM trades WHERE guild_id=? AND trade_id=?",
            (GUILD_ID, trade_id)
        ).fetchone()
        if not trade_row:
            await interaction.response.send_message("Trade not found.", ephemeral=True)
            return
        from_user, to_user, card_id = trade_row
        if to_user != user_id:
            await interaction.response.send_message("You are not the recipient of this trade.", ephemeral=True)
            return
        # Remove card from from_user, add to to_user
        cur.execute(
            "DELETE FROM inventory WHERE guild_id=? AND user_id=? AND card_id=?",
            (GUILD_ID, from_user, card_id)
        )
        card_data = cur.execute(
            "SELECT card_name, rarity, supertype FROM inventory WHERE guild_id=? AND user_id=? AND card_id=?",
            (GUILD_ID, from_user, card_id)
        ).fetchone()
        if card_data:
            card_name, rarity, supertype_ = card_data
            cur.execute(
                "INSERT OR IGNORE INTO inventory(guild_id, user_id, card_id, card_name, rarity, supertype) VALUES (?, ?, ?, ?, ?, ?)",
                (GUILD_ID, to_user, card_id, card_name, rarity, supertype_)
            )
        cur.execute("DELETE FROM trades WHERE guild_id=? AND trade_id=?", (GUILD_ID, trade_id))
        self.conn.commit()
        await interaction.response.send_message("Trade accepted and card transferred.")

    @app_commands.command(name="battle", description="Start a Pokémon battle with another user.")
    @app_commands.describe(opponent="User to battle")
    async def battle(self, interaction: discord.Interaction, opponent: discord.Member):
        if not self.guild_check(interaction):
            await interaction.response.send_message("This command is only available in the official server.", ephemeral=True)
            return
        user_id = interaction.user.id
        opp_id = opponent.id
        # Get both players' teams (first 3 cards in inventory)
        cur = self.conn.cursor()
        team1 = cur.execute(
            "SELECT card_id, card_name FROM inventory WHERE guild_id=? AND user_id=? LIMIT 3",
            (GUILD_ID, user_id)
        ).fetchall()
        team2 = cur.execute(
            "SELECT card_id, card_name FROM inventory WHERE guild_id=? AND user_id=? LIMIT 3",
            (GUILD_ID, opp_id)
        ).fetchall()
        if not team1 or not team2:
            await interaction.response.send_message("Both players must have at least 1 card in inventory.", ephemeral=True)
            return
        # Fetch card details
        p1_team = [await self.get_card_by_id(cid) for cid, _ in team1]
        p2_team = [await self.get_card_by_id(cid) for cid, _ in team2]
        # Initialize states
        state = {
            "battle_id": random.randint(100000, 999999),
            "player1_id": user_id,
            "player2_id": opp_id,
            "player1_name": interaction.user.display_name,
            "player2_name": opponent.display_name,
            "channel_id": interaction.channel.id,
            "current_player": user_id,
            "p1_team": p1_team,
            "p2_team": p2_team,
            "p1_active": p1_team[0],
            "p2_active": p2_team[0],
            "p1_active_state": {"hp": hp_of(p1_team[0])},
            "p2_active_state": {"hp": hp_of(p2_team[0])},
            "p1_team_states": [{"hp": hp_of(card)} for card in p1_team[1:]],
            "p2_team_states": [{"hp": hp_of(card)} for card in p2_team[1:]],
            "log": [f"Battle started! {interaction.user.display_name} vs {opponent.display_name}"],
            "winner_id": None,
            "message_id": None
        }
        self._save_battle(state)
        await self._update_battle_message(state)
        await interaction.response.send_message(f"Battle started! {interaction.user.mention} vs {opponent.mention}")

    async def handle_attack(self, interaction: discord.Interaction, battle_id: int, atk_idx: int):
        if not self.guild_check(interaction):
            await interaction.response.send_message("This command is only available in the official server.", ephemeral=True)
            return
        user_id = interaction.user.id
        state = self._load_battle(battle_id)
        if not state:
            await interaction.response.send_message("Battle not found.", ephemeral=True)
            return
        if state["current_player"] != user_id or state.get("winner_id"):
            await interaction.response.send_message("Not your turn or battle finished.", ephemeral=True)
            return
        # Get active and defender
        is_p1 = user_id == state["player1_id"]
        attacker = state["p1_active"] if is_p1 else state["p2_active"]
        defender = state["p2_active"] if is_p1 else state["p1_active"]
        atk_state = state["p1_active_state"] if is_p1 else state["p2_active_state"]
        def_state = state["p2_active_state"] if is_p1 else state["p1_active_state"]
        # Get attack
        attacks = attacker.get("attacks") or []
        if atk_idx >= len(attacks):
            await interaction.response.send_message("Invalid attack.", ephemeral=True)
            return
        atk = attacks[atk_idx]
        dmg = attack_base_damage(atk)
        # Coin bonuses
        if attack_coin_bonus(atk):
            if random.random() < 0.5:
                dmg += attack_coin_bonus(atk)
        # Status
        if attack_text_has_paralysis(atk):
            def_state["paralyzed"] = True
        elif attack_may_paralyze(atk) and random.random() < 0.5:
            def_state["paralyzed"] = True
        if attack_text_has_burn(atk):
            def_state["burn"] = True
        elif attack_may_burn(atk) and random.random() < 0.5:
            def_state["burn"] = True
        if attack_text_has_poison(atk):
            def_state["poison"] = True
        elif attack_may_poison(atk) and random.random() < 0.5:
            def_state["poison"] = True
        if attack_text_has_confuse(atk):
            def_state["confused"] = True
        elif attack_may_confuse(atk) and random.random() < 0.5:
            def_state["confused"] = True
        # Weakness/resistance
        atk_type = guess_attack_type(attacker, atk)
        dmg = apply_weak_resist(atk_type, defender, dmg)
        def_state["hp"] = max(0, def_state["hp"] - dmg)
        state["log"].append(f"{interaction.user.display_name} used {atk.get('name','Attack')} for {dmg} damage.")
        # Status effects
        for sname in ["burn", "poison"]:
            if def_state.get(sname):
                state["log"].append(f"{defender.get('name','?')} is affected by {sname}!")
                def_state["hp"] = max(0, def_state["hp"] - 10)
        # KO check
        if def_state["hp"] <= 0:
            state["log"].append(f"{defender.get('name','?')} is knocked out!")
            # Swap in next from bench or end battle
            team_states = state["p2_team_states"] if is_p1 else state["p1_team_states"]
            team = state["p2_team"] if is_p1 else state["p1_team"]
            if team_states:
                next_card = team[len(team) - len(team_states)]
                next_state = team_states.pop(0)
                if is_p1:
                    state["p2_active"] = next_card
                    state["p2_active_state"] = next_state
                else:
                    state["p1_active"] = next_card
                    state["p1_active_state"] = next_state
                state["log"].append(f"{defender.get('name','?')} replaced with {next_card.get('name','?')}.")
            else:
                state["winner_id"] = user_id
                await self._finalize_battle(state)
                await interaction.response.send_message("Battle finished!", ephemeral=True)
                return
        # Next turn
        state["current_player"] = state["player2_id"] if is_p1 else state["player1_id"]
        self._save_battle(state)
        await self._update_battle_message(state)
        await interaction.response.send_message("Attack processed.", ephemeral=True)

    async def handle_forfeit(self, interaction: discord.Interaction, battle_id: int):
        if not self.guild_check(interaction):
            await interaction.response.send_message("This command is only available in the official server.", ephemeral=True)
            return
        user_id = interaction.user.id
        state = self._load_battle(battle_id)
        if not state:
            await interaction.response.send_message("Battle not found.", ephemeral=True)
            return
        if state.get("winner_id"):
            await interaction.response.send_message("Battle already finished.", ephemeral=True)
            return
        opp_id = state["player2_id"] if user_id == state["player1_id"] else state["player1_id"]
        state["winner_id"] = opp_id
        state["log"].append(f"{interaction.user.display_name} forfeited!")
        await self._finalize_battle(state)
        await interaction.response.send_message("You forfeited the battle.", ephemeral=True)

    @app_commands.command(name="balance", description="Show your Pokécoin balance.")
    async def balance(self, interaction: discord.Interaction):
        if not self.guild_check(interaction):
            await interaction.response.send_message("This command is only available in the official server.", ephemeral=True)
            return
        user_id = interaction.user.id
        cur = self.conn.cursor()
        row = cur.execute(
            "SELECT balance FROM users WHERE guild_id=? AND user_id=?",
            (GUILD_ID, user_id)
        ).fetchone()
        bal = row[0] if row else 500
        await interaction.response.send_message(f"Your balance: {bal} Pokécoins", ephemeral=True)

    @app_commands.command(name="leaderboard", description="Show win/loss leaderboard.")
    async def leaderboard(self, interaction: discord.Interaction):
        if not self.guild_check(interaction):
            await interaction.response.send_message("This command is only available in the official server.", ephemeral=True)
            return
        cur = self.conn.cursor()
        rows = cur.execute(
            "SELECT user_id, wins, losses FROM users WHERE guild_id=? ORDER BY wins DESC, losses ASC LIMIT 10",
            (GUILD_ID,)
        ).fetchall()
        if not rows:
            await interaction.response.send_message("Leaderboard is empty.", ephemeral=True)
            return
        guild = self.get_guild()
        msg = "\n".join(
            f"{(guild.get_member(uid).display_name if guild and guild.get_member(uid) else uid)}: {w}W/{l}L"
            for uid, w, l in rows
        )
        await interaction.response.send_message(f"Leaderboard:\n{msg}", ephemeral=True)

    # ---------- End of commands ----------
    # ---------- Card lookup commands ----------
    @app_commands.command(name="card", description="Look up a Pokémon card by name.")
    @app_commands.describe(name="Name of the Pokémon card")
    async def card(self, interaction: discord.Interaction, name: str):
        if not self.guild_check(interaction):
            await interaction.response.send_message("This command is only available in the official server.", ephemeral=True)
            return
        card = await self.search_card(name)
        if not card:
            await interaction.response.send_message("Card not found.", ephemeral=True)
            return
        emb = discord.Embed(title=card.get("name", "Unknown"), color=0x3498DB)
        emb.add_field(name="HP", value=str(card.get("hp", "?")), inline=True)
        emb.add_field(name="Rarity", value=card.get("rarity", "?"), inline=True)
        emb.add_field(name="Supertype", value=card.get("supertype", "?"), inline=True)
        emb.add_field(name="Set", value=card.get("set", {}).get("name", "?"), inline=True)
        attacks = card.get("attacks") or []
        for atk in attacks:
            emb.add_field(name=f"Attack: {atk.get('name','Attack')}", value=trim(atk.get("text",""), 400), inline=False)
        emb.set_image(url=card.get("images", {}).get("large", ""))
        await interaction.response.send_message(embed=emb, ephemeral=True)

    @app_commands.command(name="price", description="Show price info for a Pokémon card.")
    @app_commands.describe(name="Name of the Pokémon card")
    async def price(self, interaction: discord.Interaction, name: str):
        if not self.guild_check(interaction):
            await interaction.response.send_message("This command is only available in the official server.", ephemeral=True)
            return
        card = await self.search_card(name)
        if not card:
            await interaction.response.send_message("Card not found.", ephemeral=True)
            return
        prices = card.get("tcgplayer", {}).get("prices", {})
        if not prices:
            await interaction.response.send_message("No price data.", ephemeral=True)
            return
        msg = ""
        for k, v in prices.items():
            market = v.get("market", 0.0)
            msg += f"{k}: ${market:.2f}\n"
            gbp = await self.convert_to_gbp(market, "USD")
            msg += f"    ≈ £{gbp:.2f}\n"
        await interaction.response.send_message(f"Price info for {card.get('name','?')}:\n{msg}", ephemeral=True)

    @app_commands.command(name="randomcard", description="Draw a random Pokémon card.")
    async def randomcard(self, interaction: discord.Interaction):
        if not self.guild_check(interaction):
            await interaction.response.send_message("This command is only available in the official server.", ephemeral=True)
            return
        card = await self.random_card_by_query("supertype:Pokémon")
        if not card:
            await interaction.response.send_message("No card found.", ephemeral=True)
            return
        emb = discord.Embed(title=card.get("name", "Unknown"), color=0xE91E63)
        emb.add_field(name="HP", value=str(card.get("hp", "?")), inline=True)
        emb.add_field(name="Rarity", value=card.get("rarity", "?"), inline=True)
        emb.add_field(name="Supertype", value=card.get("supertype", "?"), inline=True)
        emb.add_field(name="Set", value=card.get("set", {}).get("name", "?"), inline=True)
        attacks = card.get("attacks") or []
        for atk in attacks:
            emb.add_field(name=f"Attack: {atk.get('name','Attack')}", value=trim(atk.get("text",""), 400), inline=False)
        emb.set_image(url=card.get("images", {}).get("large", ""))
        await interaction.response.send_message(embed=emb, ephemeral=True)

    # ---------- Misc ----------
    @app_commands.command(name="help_pokemon", description="Show help for Pokémon commands.")
    async def help_pokemon(self, interaction: discord.Interaction):
        if not self.guild_check(interaction):
            await interaction.response.send_message("This command is only available in the official server.", ephemeral=True)
            return
        msg = (
            "**Pokémon Card Bot Commands:**\n"
            "`/openpack` — Open a booster pack (5 Pokémon cards)\n"
            "`/inventory` — Show your Pokémon card inventory\n"
            "`/trade <card> <user>` — Offer a card to another user\n"
            "`/accepttrade <trade_id>` — Accept a trade offer\n"
            "`/battle <user>` — Start a Pokémon battle with another user\n"
            "`/balance` — Show your Pokécoin balance\n"
            "`/leaderboard` — Show win/loss leaderboard\n"
            "`/card <name>` — Look up a Pokémon card by name\n"
            "`/price <name>` — Show price info for a card\n"
            "`/randomcard` — Draw a random Pokémon card\n"
            "`/help_pokemon` — Show this help message\n"
        )
        await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PokemonFullCog(bot))
