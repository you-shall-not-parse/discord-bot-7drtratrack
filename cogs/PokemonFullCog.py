# pokemon_full_cog.py
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
load_dotenv()
POKEMON_API_KEY = os.getenv("POKEMON_TCG_API_KEY") or os.getenv("TCG_API_KEY")
API_HEADERS = {"X-Api-Key": POKEMON_API_KEY} if POKEMON_API_KEY else {}
API_URL = "https://api.pokemontcg.io/v2/cards"
DB_FILE = "pokemon_cards.db"

# Rarity weighting for pack opening
RARITY_WEIGHTS = {"Common": 62, "Uncommon": 24, "Rare": 8, "Rare Holo": 4, "Rare Ultra": 2}

# Regex helpers for parsing attack text
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
    # Weakness: × or x (e.g., ×2) -> multiply
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
    # Resistance: subtract value (often -30)
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
        user_id INTEGER PRIMARY KEY,
        balance INTEGER DEFAULT 500,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS inventory (
        user_id INTEGER,
        card_id TEXT,
        card_name TEXT,
        rarity TEXT,
        supertype TEXT,
        PRIMARY KEY(user_id, card_id)
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_user INTEGER,
        to_user INTEGER,
        card_id TEXT
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS battles (
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
            await cog.handle_forfeit(interaction, self.view.custom_id)  # view.custom_id carries battle_id

class AttackButton(Button):
    def __init__(self, label: str, idx: int):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.idx = idx

    async def callback(self, interaction: discord.Interaction):
        cog: "PokemonFullCog" = interaction.client.get_cog("PokemonFullCog")
        if cog:
            await cog.handle_attack(interaction, self.view.custom_id, self.idx)

class BattleSelect(Select):
    # simple wrapper in case we want per-select logic later
    pass

class BattleView(View):
    def __init__(self, battle_id: int, timeout: int = 900):
        super().__init__(timeout=timeout)
        self.custom_id = battle_id  # store battle_id on view for buttons to access

    async def on_timeout(self):
        # just leave DB state as-is; the cog will restore view on startup
        pass

# ---------------- Cog ----------------
class PokemonFullCog(commands.Cog, name="PokemonFullCog"):
    """Full Pokémon cog: lookup, openpack, inventory, trade, interactive 3v3 battles (persistent)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # connections
        self.conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        init_db(self.conn)
        self._session = aiohttp.ClientSession()
        # restore battles after ready
        self._restore_task = self.bot.loop.create_task(self._restore_battles_on_ready())

    # ---------- lifecycle ----------
    @commands.Cog.listener()
    async def on_ready(self):
        # sync commands
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

    # currency conversion
    async def convert_to_gbp(self, amount: float, from_curr: str) -> float:
        try:
            async with self._session.get("https://api.exchangerate.host/convert", params={"from": from_curr, "to": "GBP", "amount": amount}) as r:
                data = await r.json()
                return float(data.get("result", 0.0))
        except Exception:
            return 0.0

    # ---------- Commands ----------
    @app_commands.command(name="openpack", description="Open a booster pack (5 Pokémon cards).")
    async def openpack(self, interaction: discord.Interaction):
        await interaction.response.defer()
        async with aiohttp.ClientSession() as s:
            pulled = []
            for _ in range(5):
                rarity = random.choices(list(RARITY_WEIGHTS.keys()), weights=RARITY_WEIGHTS.values())[0]
                q = f'supertype:"Pokémon" rarity:"{rarity}"'
                card = await self.random_card_by_query(q)
                if not card:
                    # fallback to any Pokémon
                    card = await self.random_card_by_query('supertype:"Pokémon"')
                if not card:
                    await interaction.followup.send("Could not fetch cards right now. Try again later.")
                    return
                pulled.append(card)
                # insert into inventory (upsert)
                try:
                    self.conn.execute(
                        "INSERT OR REPLACE INTO inventory(user_id, card_id, card_name, rarity, supertype) VALUES(?,?,?,?,?)",
                        (interaction.user.id, card["id"], card["name"], card.get("rarity", "Unknown"), supertype(card))
                    )
                except Exception:
                    pass
            self.conn.commit()

        desc = "\n".join([f"**{c['name']}** — *{c.get('rarity','Unknown')}* (`{c['id']}`)" for c in pulled])
        embed = discord.Embed(title=f"{interaction.user.display_name} opened a pack!", description=trim(desc, 4000), color=0x00aa88)
        try:
            embed.set_thumbnail(url=pulled[0]["images"]["small"])
            embed.set_image(url=pulled[0]["images"]["large"])
        except Exception:
            pass
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="collection", description="View your card collection (first 40 shown).")
    async def collection(self, interaction: discord.Interaction):
        rows = self.conn.execute("SELECT card_name, rarity, card_id FROM inventory WHERE user_id=? ORDER BY card_name ASC", (interaction.user.id,)).fetchall()
        if not rows:
            await interaction.response.send_message("📭 Your binder is empty. Use `/openpack` to start collecting!")
            return
        lines = [f"{i+1}. **{n}** — *{r}* (`{cid}`)" for i, (n, r, cid) in enumerate(rows[:40])]
        more = f"\n…and {len(rows)-40} more." if len(rows) > 40 else ""
        embed = discord.Embed(title=f"{interaction.user.display_name}'s Inventory", description=trim("\n".join(lines) + more, 4000), color=0x3498db)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="lookup", description="Lookup a Pokémon TCG card and show GBP pricing.")
    async def lookup(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()
        card = None
        try:
            if "-" in query:
                card = await self.get_card_by_id(query)
        except Exception:
            card = None
        if not card:
            card = await self.search_card(query)
        if not card:
            await interaction.followup.send("❌ No card found.")
            return

        embed = discord.Embed(title=f"{card.get('name','Unknown')} — {card.get('set',{}).get('name','')}",
                              description=card.get("rarity","Unknown"), color=0x2ecc71)
        if card.get("images", {}).get("large"):
            embed.set_image(url=card["images"]["large"])
        embed.add_field(name="HP / Types", value=f"{card.get('hp','?')} / {', '.join(card.get('types') or []) or '—'}", inline=True)
        if card.get("attacks"):
            atks = []
            for a in card["attacks"][:3]:
                cost = " ".join(a.get("cost") or []) or "—"
                atks.append(f"**{a.get('name','?')}** ({cost}) — {a.get('damage') or '—'}")
            embed.add_field(name="Attacks", value=trim("\n".join(atks), 1024), inline=False)
        if card.get("weaknesses"):
            w = ", ".join([f"{x['type']} {x.get('value','')}" for x in card["weaknesses"]])
            embed.add_field(name="Weaknesses", value=w, inline=True)
        if card.get("resistances"):
            r = ", ".join([f"{x['type']} {x.get('value','')}" for x in card["resistances"]])
            embed.add_field(name="Resistances", value=r, inline=True)

        price_lines = []
        tcg = card.get("tcgplayer", {}).get("prices", {})
        cm = card.get("cardmarket", {}).get("prices", {})
        # TCGplayer price (USD -> GBP)
        try:
            if tcg:
                chosen = None
                for k in ("holofoil", "normal", "reverseHolofoil"):
                    if k in tcg and tcg[k].get("market"):
                        chosen = (k, tcg[k]["market"])
                        break
                if not chosen:
                    for k, v in tcg.items():
                        if v.get("market"):
                            chosen = (k, v["market"])
                            break
                if chosen:
                    label, usd_price = chosen
                    gbp = await self.convert_to_gbp(float(usd_price), "USD")
                    price_lines.append(f"TCGplayer ({label}): £{gbp:.2f} (from ${usd_price:.2f} USD)")
        except Exception:
            pass
        # Cardmarket price (EUR -> GBP)
        try:
            if cm and cm.get("averageSellPrice") is not None:
                eur_price = float(cm["averageSellPrice"])
                gbp = await self.convert_to_gbp(eur_price, "EUR")
                price_lines.append(f"Cardmarket (avg): £{gbp:.2f} (from €{eur_price:.2f} EUR)")
        except Exception:
            pass

        if price_lines:
            embed.add_field(name="Prices (converted to GBP)", value="\n".join(price_lines), inline=False)
        else:
            embed.add_field(name="Prices", value="No pricing available.", inline=False)
        embed.set_footer(text=f"ID: {card.get('id','?')}")
        await interaction.followup.send(embed=embed)

    # ------------- Trading -------------
    @app_commands.command(name="trade", description="Offer a card you own to another user (by card ID).")
    async def trade(self, interaction: discord.Interaction, user: discord.User, card_id: str):
        row = self.conn.execute("SELECT card_name FROM inventory WHERE user_id=? AND card_id=?", (interaction.user.id, card_id)).fetchone()
        if not row:
            await interaction.response.send_message("❌ You don't own that card, or the ID is incorrect. Use `/collection` for IDs.")
            return
        self.conn.execute("INSERT INTO trades(from_user, to_user, card_id) VALUES(?,?,?)", (interaction.user.id, user.id, card_id))
        self.conn.commit()
        await interaction.response.send_message(f"📦 Trade created: {interaction.user.mention} → {user.mention} for **{row[0]}** (`{card_id}`)\n{user.mention} use `/accepttrade` or `/declinetrade`.")

    @app_commands.command(name="accepttrade", description="Accept your latest trade.")
    async def accepttrade(self, interaction: discord.Interaction):
        tr = self.conn.execute("SELECT trade_id, from_user, card_id FROM trades WHERE to_user=? ORDER BY trade_id DESC LIMIT 1", (interaction.user.id,)).fetchone()
        if not tr:
            await interaction.response.send_message("📭 No pending trades.")
            return
        trade_id, from_user, card_id = tr
        card_row = self.conn.execute("SELECT card_name, rarity, supertype FROM inventory WHERE user_id=? AND card_id=?", (from_user, card_id)).fetchone()
        if not card_row:
            await interaction.response.send_message("⚠️ Trade failed: card no longer available.")
            self.conn.execute("DELETE FROM trades WHERE trade_id=?", (trade_id,))
            self.conn.commit()
            return
        name, rarity, st = card_row
        self.conn.execute("DELETE FROM inventory WHERE user_id=? AND card_id=?", (from_user, card_id))
        self.conn.execute("INSERT OR REPLACE INTO inventory(user_id, card_id, card_name, rarity, supertype) VALUES(?,?,?,?,?)", (interaction.user.id, card_id, name, rarity, st))
        self.conn.execute("DELETE FROM trades WHERE trade_id=?", (trade_id,))
        self.conn.commit()
        await interaction.response.send_message(f"✅ Trade complete! {interaction.user.mention} received **{name}** from <@{from_user}>.")

    @app_commands.command(name="declinetrade", description="Decline your latest trade.")
    async def declinetrade(self, interaction: discord.Interaction):
        tr = self.conn.execute("SELECT trade_id FROM trades WHERE to_user=? ORDER BY trade_id DESC LIMIT 1", (interaction.user.id,)).fetchone()
        if not tr:
            await interaction.response.send_message("📭 No pending trades.")
            return
        self.conn.execute("DELETE FROM trades WHERE trade_id=?", (tr[0],))
        self.conn.commit()
        await interaction.response.send_message("❎ Trade declined.")

    # ------------- Battles -------------
    @app_commands.command(name="battle", description="Challenge someone to a 3v3 interactive battle.")
    async def battle(self, interaction: discord.Interaction, opponent: discord.User):
        await interaction.response.defer()
        if opponent.id == interaction.user.id:
            await interaction.followup.send("You can't battle yourself.")
            return
        # fetch both players' pokemon cards from inventory
        p1_ids = [r[0] for r in self.conn.execute("SELECT card_id FROM inventory WHERE user_id=? AND supertype='pokémon'", (interaction.user.id,)).fetchall()]
        p2_ids = [r[0] for r in self.conn.execute("SELECT card_id FROM inventory WHERE user_id=? AND supertype='pokémon'", (opponent.id,)).fetchall()]

        if len(p1_ids) < 3 or len(p2_ids) < 3:
            await interaction.followup.send("Both players need at least 3 Pokémon cards in inventory to play.")
            return

        # Fetch up to 25 options each
        async with aiohttp.ClientSession() as session:
            async def fetch_opts(ids, limit=25):
                opts = []
                for cid in ids[:limit]:
                    try:
                        data = await self.get_card_by_id(cid)
                        if data:
                            opts.append(discord.SelectOption(label=data.get("name","Unknown"), value=data["id"], description=data.get("rarity","")))
                    except Exception:
                        continue
                return opts

            p1_opts = await fetch_opts(p1_ids)
            p2_opts = await fetch_opts(p2_ids)

        if not p1_opts or not p2_opts:
            await interaction.followup.send("Could not build selection lists. Try again later.")
            return

        # Build selection view (both pick concurrently)
        view = View(timeout=60)
        p1_select = Select(placeholder=f"{interaction.user.name}: Choose 3 Pokémon", min_values=3, max_values=3, options=p1_opts)
        p2_select = Select(placeholder=f"{opponent.name}: Choose 3 Pokémon", min_values=3, max_values=3, options=p2_opts)
        picks = {"p1": None, "p2": None}

        async def p1_cb(i: discord.Interaction):
            if i.user.id != interaction.user.id:
                await i.response.send_message("This selection is not for you.", ephemeral=True)
                return
            picks["p1"] = p1_select.values
            await i.response.send_message("You selected your 3 Pokémon.", ephemeral=True)

        async def p2_cb(i: discord.Interaction):
            if i.user.id != opponent.id:
                await i.response.send_message("This selection is not for you.", ephemeral=True)
                return
            picks["p2"] = p2_select.values
            await i.response.send_message("You selected your 3 Pokémon.", ephemeral=True)

        p1_select.callback = p1_cb
        p2_select.callback = p2_cb
        view.add_item(p1_select)
        view.add_item(p2_select)

        pick_msg = await interaction.followup.send(content=f"{interaction.user.mention} and {opponent.mention} — pick your 3 Pokémon (60s).", view=view)

        # wait up to 60s for both picks
        for _ in range(60):
            if picks["p1"] is not None and picks["p2"] is not None:
                break
            await asyncio.sleep(1)

        if picks["p1"] is None or picks["p2"] is None:
            await interaction.followup.send("Pick timed out. Both players must pick 3 Pokémon.")
            return

        # fetch card objects for selected IDs
        async with aiohttp.ClientSession() as session:
            async def fetch_cards(ids):
                cards = []
                for cid in ids:
                    try:
                        c = await self.get_card_by_id(cid)
                        if c:
                            cards.append(c)
                    except Exception:
                        continue
                return cards

            p1_cards = await fetch_cards(picks["p1"])
            p2_cards = await fetch_cards(picks["p2"])

        if len(p1_cards) < 3 or len(p2_cards) < 3:
            await interaction.followup.send("Failed to fetch selected cards. Aborting.")
            return

        # create persistent battle state
        battle_id = random.randint(10_000_000, 99_999_999)
        state = {
            "battle_id": battle_id,
            "channel_id": interaction.channel_id,
            "message_id": None,
            "player1_id": interaction.user.id,
            "player1_name": interaction.user.name,
            "player2_id": opponent.id,
            "player2_name": opponent.name,
            "p1_active": p1_cards[0],
            "p1_active_state": {"hp": hp_of(p1_cards[0]), "burn": False, "poison": False, "paralyzed": False, "confused": False},
            "p1_team": p1_cards[1:],
            "p1_team_states": [{"hp": hp_of(c), "burn": False, "poison": False, "paralyzed": False, "confused": False} for c in p1_cards[1:]],
            "p2_active": p2_cards[0],
            "p2_active_state": {"hp": hp_of(p2_cards[0]), "burn": False, "poison": False, "paralyzed": False, "confused": False},
            "p2_team": p2_cards[1:],
            "p2_team_states": [{"hp": hp_of(c), "burn": False, "poison": False, "paralyzed": False, "confused": False} for c in p2_cards[1:]],
            "current_player": interaction.user.id,
            "log": [f"Battle start: {interaction.user.name} vs {opponent.name}"],
            "finished": False,
            "winner_id": None
        }

        # persist in DB
        self.conn.execute("INSERT INTO battles(battle_id, state_json) VALUES(?,?)", (battle_id, json.dumps(state)))
        self.conn.commit()

        # Create view with starter attack buttons
        bview = BattleView(battle_id)
        bview.add_item(ForfeitButton())
        # add attack buttons for starter active
        starter_attacks = (p1_cards[0].get("attacks") or [])[:4]
        for idx, atk in enumerate(starter_attacks):
            bview.add_item(AttackButton(label=trim(atk.get("name","Attack"), 80), idx=idx))

        embed = discord.Embed(title=f"Battle: {interaction.user.name} vs {opponent.name}", color=0xE67E22)
        embed.add_field(name=f"{interaction.user.name} Active", value=f"**{p1_cards[0]['name']}** — HP {state['p1_active_state']['hp']}", inline=True)
        embed.add_field(name=f"{opponent.name} Active", value=f"**{p2_cards[0]['name']}** — HP {state['p2_active_state']['hp']}", inline=True)
        embed.add_field(name="Instructions", value=f"Current player: <@{state['current_player']}>. Use the buttons to pick an attack. First to KO all 3 opponent Pokémon wins.", inline=False)

        sent = await interaction.followup.send(embed=embed, view=bview)
        state["message_id"] = sent.id
        # update DB with message id
        self.conn.execute("UPDATE battles SET state_json = ? WHERE battle_id = ?", (json.dumps(state), battle_id))
        self.conn.commit()

    # handle forfeit
    async def handle_forfeit(self, interaction: discord.Interaction, battle_id: int):
        state = self._load_battle(battle_id)
        if not state:
            await interaction.response.send_message("Battle not found.", ephemeral=True)
            return
        if interaction.user.id not in (state["player1_id"], state["player2_id"]):
            await interaction.response.send_message("You are not part of this battle.", ephemeral=True)
            return
        loser = interaction.user.id
        winner = state["player1_id"] if loser == state["player2_id"] else state["player2_id"]
        state["finished"] = True
        state["winner_id"] = winner
        state["log"].append(f"<@{loser}> forfeited. <@{winner}> wins.")
        # update DB and edit message
        self._save_battle(state)
        try:
            ch = self.bot.get_channel(state["channel_id"])
            if ch:
                msg = await ch.fetch_message(state["message_id"])
                await msg.edit(content=f"Battle finished — <@{winner}> wins by forfeit.", embed=None, view=None)
        except Exception:
            pass
        await interaction.response.send_message(f"You forfeited. <@{winner}> wins.", ephemeral=False)
        # cleanup: remove battle from DB
        self._delete_battle(state["battle_id"])

    # handle attack
    async def handle_attack(self, interaction: discord.Interaction, battle_id: int, attack_idx: int):
        state = self._load_battle(battle_id)
        if not state:
            await interaction.response.send_message("Battle not found or finished.", ephemeral=True)
            return
        if state.get("finished"):
            await interaction.response.send_message("Battle already finished.", ephemeral=True)
            return
        actor = interaction.user.id
        if actor != state["current_player"]:
            await interaction.response.send_message("It's not your turn.", ephemeral=True)
            return

        is_p1 = actor == state["player1_id"]
        if is_p1:
            attacker_card = state["p1_active"]
            attacker_state = state["p1_active_state"]
            defender_card = state["p2_active"]
            defender_state = state["p2_active_state"]
        else:
            attacker_card = state["p2_active"]
            attacker_state = state["p2_active_state"]
            defender_card = state["p1_active"]
            defender_state = state["p1_active_state"]

        attacks = attacker_card.get("attacks") or []
        if attack_idx >= len(attacks):
            await interaction.response.send_message("Invalid attack selection.", ephemeral=True)
            return
        attack = attacks[attack_idx]

        # confusion check
        if attacker_state.get("confused"):
            if random.random() < 0.5:
                self_dmg = max(1, attack_base_damage(attack)//2)
                attacker_state["hp"] -= self_dmg
                state["log"].append(f"**{attacker_card['name']}** is confused and hurts itself for {self_dmg} damage! (HP now {max(attacker_state['hp'],0)})")
                attacker_state["confused"] = False
                self._save_battle(state)
                await self._update_battle_message(state)
                if attacker_state["hp"] <= 0:
                    state["log"].append(f"💥 {attacker_card['name']} fainted from confusion!")
                    # treat as KO for the attacker; promote or end
                    await self._handle_knockout_and_promote(state, attacker_won=(not is_p1))
                return
            else:
                attacker_state["confused"] = False

        # compute damage
        base = attack_base_damage(attack)
        coin_bonus = attack_coin_bonus(attack)
        bonus = 0
        if coin_bonus > 0:
            flip = random.choice(["heads", "tails"])
            if flip == "heads":
                bonus += coin_bonus
            state["log"].append(f"Coin flip for {attack.get('name','attack')}: {flip}. Bonus {bonus}.")

        atk_type = guess_attack_type(attacker_card, attack)
        damage = apply_weak_resist(atk_type, defender_card, base + bonus)

        defender_state["hp"] -= damage
        state["log"].append(f"**{attacker_card['name']}** used **{attack.get('name','')}** for **{damage}** damage. {defender_card['name']} HP: {max(defender_state['hp'],0)}")

        # status effects
        if attack_text_has_paralysis(attack):
            defender_state["paralyzed"] = True
            state["log"].append(f"{defender_card['name']} is Paralyzed.")
        elif attack_may_paralyze(attack) and random.random() < 0.5:
            defender_state["paralyzed"] = True
            state["log"].append(f"{defender_card['name']} is Paralyzed (coin).")

        if attack_text_has_burn(attack):
            defender_state["burn"] = True
            state["log"].append(f"{defender_card['name']} is Burned.")
        elif attack_may_burn(attack) and random.random() < 0.5:
            defender_state["burn"] = True
            state["log"].append(f"{defender_card['name']} is Burned (coin).")

        if attack_text_has_poison(attack):
            defender_state["poison"] = True
            state["log"].append(f"{defender_card['name']} is Poisoned.")
        elif attack_may_poison(attack) and random.random() < 0.5:
            defender_state["poison"] = True
            state["log"].append(f"{defender_card['name']} is Poisoned (coin).")

        if attack_text_has_confuse(attack):
            defender_state["confused"] = True
            state["log"].append(f"{defender_card['name']} is Confused.")
        elif attack_may_confuse(attack) and random.random() < 0.5:
            defender_state["confused"] = True
            state["log"].append(f"{defender_card['name']} is Confused (coin).")

        # apply immediate poison damage
        if defender_state.get("poison"):
            pdmg = max(5, int(0.1 * hp_of(defender_card)))
            defender_state["hp"] -= pdmg
            state["log"].append(f"{defender_card['name']} takes {pdmg} poison damage (HP now {max(defender_state['hp'],0)})")

        # check knockout
        if defender_state["hp"] <= 0:
            state["log"].append(f"🎯 **{defender_card['name']}** was Knocked Out!")
            await self._handle_knockout_and_promote(state, attacker_won=is_p1)
            return

        # apply burn immediate
        if defender_state.get("burn"):
            bdmg = max(5, int(0.1 * hp_of(defender_card)))
            defender_state["hp"] -= bdmg
            state["log"].append(f"{defender_card['name']} suffers {bdmg} burn damage (HP now {max(defender_state['hp'],0)})")
            if defender_state["hp"] <= 0:
                state["log"].append(f"🎯 {defender_card['name']} fainted from burn!")
                await self._handle_knockout_and_promote(state, attacker_won=is_p1)
                return

        # next player's turn (respect paralyze skipping)
        state["current_player"] = state["player2_id"] if is_p1 else state["player1_id"]
        next_is_p1 = state["current_player"] == state["player1_id"]
        if next_is_p1:
            if state["p1_active_state"].get("paralyzed"):
                state["log"].append(f"{state['player1_name']} is Paralyzed and will miss their next turn.")
                state["p1_active_state"]["paralyzed"] = False
                state["current_player"] = state["player2_id"]
        else:
            if state["p2_active_state"].get("paralyzed"):
                state["log"].append(f"{state['player2_name']} is Paralyzed and will miss their next turn.")
                state["p2_active_state"]["paralyzed"] = False
                state["current_player"] = state["player1_id"]

        # save and update message
        self._save_battle(state)
        await self._update_battle_message(state)

    # handle KO and promote or finalize
    async def _handle_knockout_and_promote(self, state: Dict[str, Any], attacker_won: bool):
        # attacker_won == True means p1 (if p1 attacked) or p2 accordingly; we passed attacker_won relative to p1
        if attacker_won:
            # defender is p2
            if state["p2_team"]:
                new_card = state["p2_team"].pop(0)
                new_state = state["p2_team_states"].pop(0)
                state["p2_active"] = new_card
                state["p2_active_state"] = new_state
                state["log"].append(f"{state['player2_name']} promotes **{new_card['name']}**.")
            else:
                # p1 wins
                state["finished"] = True
                state["winner_id"] = state["player1_id"]
                state["log"].append(f"🏆 {state['player1_name']} wins the match!")
                self._save_battle(state)
                await self._finalize_battle(state)
                return
        else:
            # attacker was p2, so p1 lost active
            if state["p1_team"]:
                new_card = state["p1_team"].pop(0)
                new_state = state["p1_team_states"].pop(0)
                state["p1_active"] = new_card
                state["p1_active_state"] = new_state
                state["log"].append(f"{state['player1_name']} promotes **{new_card['name']}**.")
            else:
                state["finished"] = True
                state["winner_id"] = state["player2_id"]
                state["log"].append(f"🏆 {state['player2_name']} wins the match!")
                self._save_battle(state)
                await self._finalize_battle(state)
                return
        # persist and update message
        self._save_battle(state)
        await self._update_battle_message(state)

    async def _finalize_battle(self, state: Dict[str, Any]):
        # update wins/losses
        winner = state.get("winner_id")
        p1 = state["player1_id"]
        p2 = state["player2_id"]
        cur = self.conn.cursor()
        cur.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (p1,))
        cur.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (p2,))
        if winner == p1:
            cur.execute("UPDATE users SET wins = wins + 1 WHERE user_id=?", (p1,))
            cur.execute("UPDATE users SET losses = losses + 1 WHERE user_id=?", (p2,))
        elif winner == p2:
            cur.execute("UPDATE users SET wins = wins + 1 WHERE user_id=?", (p2,))
            cur.execute("UPDATE users SET losses = losses + 1 WHERE user_id=?", (p1,))
        self.conn.commit()

        # edit message to final state
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
        # remove from DB
        self._delete_battle(state["battle_id"])

    # update/create battle message (tries to edit existing message, else posts new one)
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
                # update saved message id in DB if changed
                state["message_id"] = msg.id
                self._save_battle(state)
                return
            except Exception:
                pass
        # send fresh message
        try:
            sent = await ch.send(embed=emb, view=view)
            state["message_id"] = sent.id
            self._save_battle(state)
        except Exception:
            self._save_battle(state)

    # ---------------- DB wrappers for battles ----------------
    def _save_battle(self, state: Dict[str, Any]):
        self.conn.execute("INSERT OR REPLACE INTO battles(battle_id, state_json) VALUES(?,?)", (state["battle_id"], json.dumps(state)))
        self.conn.commit()

    def _load_battle(self, battle_id: int) -> Optional[Dict[str, Any]]:
        row = self.conn.execute("SELECT state_json FROM battles WHERE battle_id=?", (battle_id,)).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return None

    def _delete_battle(self, battle_id: int):
        self.conn.execute("DELETE FROM battles WHERE battle_id=?", (battle_id,))
        self.conn.commit()

    # ---------------- Restore on startup ----------------
    async def _restore_battles_on_ready(self):
        await self.bot.wait_until_ready()
        rows = self.conn.execute("SELECT battle_id, state_json FROM battles").fetchall()
        for bid, sj in rows:
            try:
                state = json.loads(sj)
            except Exception:
                continue
            # try to edit existing message or post new message with view
            try:
                ch = self.bot.get_channel(state["channel_id"])
                if not ch:
                    continue
                msg = None
                try:
                    msg = await ch.fetch_message(state["message_id"])
                except Exception:
                    msg = None
                # create view & embed similar to _update_battle_message
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

    # ---------------- Utility: get_card_by_id used elsewhere ----------------
    async def get_card_by_id(self, card_id: str) -> Optional[Dict[str, Any]]:
        try:
            data = await self._get_json(f"{API_URL}/{card_id}")
            return data.get("data")
        except Exception:
            return None

# ---------------- Setup ----------------
async def setup(bot: commands.Bot):
    await bot.add_cog(PokemonFullCog(bot))
