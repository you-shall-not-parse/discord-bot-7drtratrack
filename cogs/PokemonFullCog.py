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

    # ---------- lifecycle ----------
    @commands.Cog.listener()
    async def on_ready(self):
        try:
            await self.bot.tree.sync()
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

    # ... (all your command logic remains as previously posted, all DB queries must include guild_id)

# ---------------- Setup ----------------
async def setup(bot: commands.Bot):
    await bot.add_cog(PokemonFullCog(bot))
