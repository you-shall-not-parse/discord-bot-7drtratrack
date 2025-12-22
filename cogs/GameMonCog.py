# game_mon_cog.py

import discord
from discord.ext import commands, tasks
import asyncio
import json
import os
import datetime
import logging

# ---------------- LOGGING ----------------
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger("GameMonCog")

# ---------------- CONFIG ----------------
GUILD_ID = 1097913605082579024
THREAD_ID = 1412934277133369494

IGNORED_GAMES = ["Spotify", "Discord", "Netflix", "YouTube", "Disney+", "Sky TV"]
PREFS_FILE = "game_prefs.json"
STATE_FILE = "game_state.json"

INACTIVE_CHECK_MINUTES = 60
MAX_INACTIVE_HOURS = 12
DEFAULT_PREFERENCE = "opt_in"

EMBED_UPDATE_MIN_INTERVAL = 5
ADMIN_USER_IDS = [1109147750932676649]
# ----------------------------------------


# ================= BUTTON VIEW =================
class PreferenceView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Opt-In", style=discord.ButtonStyle.green, custom_id="gm:optin")
    async def opt_in(self, interaction: discord.Interaction, _):
        await self.cog.set_preference(interaction, "opt_in")

    @discord.ui.button(label="Opt-Out", style=discord.ButtonStyle.red, custom_id="gm:optout")
    async def opt_out(self, interaction: discord.Interaction, _):
        await self.cog.set_preference(interaction, "opt_out")


# ================= COG =================
class GameMonCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        self.prefs = self._load(PREFS_FILE)
        self.state = self._load(STATE_FILE)

        self.state.setdefault("games", {})
        self.state.setdefault("last_seen", {})
        self.state.setdefault("message_id", None)

        self.file_lock = asyncio.Lock()
        self.view = PreferenceView(self)

        self._last_update = 0.0
        self._update_task = None
        self._force_pending = False

        # Start tasks safely
        self.bot.loop.create_task(self.startup())
        self.cleanup_inactive_users.start()
        self.ensure_message_exists.start()

    # ================= STARTUP =================
    async def startup(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(2)

        thread = self.bot.get_channel(THREAD_ID)
        if not thread:
            logger.error("Thread not found on startup")
            return

        self.bot.add_view(self.view)
        await self.delete_previous_message()
        await self.force_update()

    # ================= JSON =================
    def _load(self, file):
        if not os.path.exists(file):
            return {}
        try:
            with open(file, "r") as f:
                return json.load(f)
        except Exception:
            return {}

    async def _save(self, file, data):
        async with self.file_lock:
            with open(file, "w") as f:
                json.dump(data, f, indent=2)

    # ================= MESSAGE CONTROL =================
    async def delete_previous_message(self):
        if not self.state.get("message_id"):
            return

        thread = self.bot.get_channel(THREAD_ID)
        if not thread:
            self.state["message_id"] = None
            return

        try:
            msg = await thread.fetch_message(int(self.state["message_id"]))
            await msg.delete()
        except discord.NotFound:
            pass
        except discord.Forbidden:
            logger.error("Missing permissions to delete message")

        self.state["message_id"] = None
        await self._save(STATE_FILE, self.state)

    async def force_update(self):
        self._force_pending = True
        await self.schedule_update(force_new=True)

    async def schedule_update(self, force_new=False):
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_update

        self._force_pending |= force_new

        if self._update_task and not self._update_task.done():
            return

        delay = 0 if elapsed >= EMBED_UPDATE_MIN_INTERVAL else EMBED_UPDATE_MIN_INTERVAL - elapsed

        async def runner():
            await asyncio.sleep(delay)
            await self.update_embed(force_new=self._force_pending)
            self._force_pending = False
            self._last_update = asyncio.get_event_loop().time()

        self._update_task = asyncio.create_task(runner())

    # ================= EMBED =================
    async def update_embed(self, force_new=False):
        thread = self.bot.get_channel(THREAD_ID)
        if not thread:
            return

        # HARD GUARANTEE: if message_id missing → force create
        if not self.state.get("message_id"):
            force_new = True

        embed = discord.Embed(
            title="🎮 Now Playing",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )

        guild = self.bot.get_guild(GUILD_ID)

        if self.state["games"]:
            for game, users in sorted(self.state["games"].items(), key=lambda x: len(x[1]), reverse=True):
                names = []
                for uid in users:
                    member = guild.get_member(int(uid)) if guild else None
                    names.append(member.display_name if member else f"User {uid}")

                embed.add_field(
                    name=game,
                    value="• " + "\n• ".join(names),
                    inline=False
                )
        else:
            embed.description = "Nobody is playing tracked games right now."

        embed.set_footer(text="Use the buttons below to opt in or out")

        try:
            if not force_new and self.state.get("message_id"):
                msg = await thread.fetch_message(int(self.state["message_id"]))
                await msg.edit(embed=embed, view=self.view)
            else:
                msg = await thread.send(embed=embed, view=self.view)
                self.state["message_id"] = msg.id
                await self._save(STATE_FILE, self.state)

        except discord.NotFound:
            self.state["message_id"] = None
            await self.force_update()
        except discord.Forbidden:
            logger.error("Missing permissions to post embed")

    # ================= PREFERENCES =================
    async def set_preference(self, interaction, pref):
        uid = str(interaction.user.id)
        self.prefs[uid] = pref
        await self._save(PREFS_FILE, self.prefs)
        await interaction.response.send_message(f"Preference set to **{pref}**", ephemeral=True)
        await self.force_update()

    # ================= CLEANUP =================
    @tasks.loop(minutes=INACTIVE_CHECK_MINUTES)
    async def cleanup_inactive_users(self):
        now = datetime.datetime.utcnow()
        cutoff = datetime.timedelta(hours=MAX_INACTIVE_HOURS)

        changed = False
        for uid, ts in list(self.state["last_seen"].items()):
            try:
                if now - datetime.datetime.fromisoformat(ts) > cutoff:
                    for g in list(self.state["games"].keys()):
                        if uid in self.state["games"][g]:
                            self.state["games"][g].remove(uid)
                            changed = True
                            if not self.state["games"][g]:
                                self.state["games"].pop(g)
            except Exception:
                self.state["last_seen"][uid] = now.isoformat()

        if changed:
            await self._save(STATE_FILE, self.state)
            await self.schedule_update()

    @cleanup_inactive_users.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

    # ================= WATCHDOG =================
    @tasks.loop(minutes=5)
    async def ensure_message_exists(self):
        if not self.state.get("message_id"):
            await self.force_update()

    @ensure_message_exists.before_loop
    async def before_ensure(self):
        await self.bot.wait_until_ready()


# ================= SETUP =================
async def setup(bot):
    await bot.add_cog(GameMonCog(bot))
