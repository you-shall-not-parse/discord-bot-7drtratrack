import discord
from discord.ext import commands, tasks
from discord import app_commands
from discord.ui import View, Select, Modal, TextInput, UserSelect
import aiosqlite
import random
import datetime
import math
from typing import Optional

# ---------------- Config ----------------
GUILD_ID = 1097913605082579024  # replace with your guild ID

# Channel IDs for the armour leaderboard and submissions
ARM_LEADERBOARD_CHANNEL_ID = 1214971219246325851
ARM_SUBMISSIONS_CHANNEL_ID = 1419010992578363564

# Support multiple admin roles (reuse your existing IDs)
ADMIN_ROLE_IDS = {
    1213495462632361994,
    1097915860322091090,
    1097946543065137183,
}

DB_FILE = "armleaderboard.db"

# Minutes allowed to provide a screenshot when one is required
PROOF_TIMEOUT_MINUTES = 5

# Armour crew stats (adjust as needed)
OLD_LIFE_STAT_NAME = "Longest Life HH:MM"
LIFE_STAT_NAME = "Longest Life HH:MM:SS"
STATS_ARM = [
    "Most Infantry Kills",
    "Longest Armour Kill",
    "Most Vehicles Destroyed",
    "Most Killstreak",
    "Most Garrisons Destroyed",
    "Most OPs Destroyed",
    "Most Officer Kills",
    LIFE_STAT_NAME
]

# Text shown under the embed title
LEADERBOARD_DESCRIPTION = (
    f"Submit your armour crew high scores using the selector below (whole match, up to 2h 30m). "
    f"After choosing a stat, you will select 1–3 crew members via a user selector. "
    f"Submissions are community-reported in <#{ARM_SUBMISSIONS_CHANNEL_ID}> and will be reviewed.\n\n"
    "**You must have a screenshot to back up your submissions, it is requested on a random basis and if called upon you must post it "
    f"in <#{ARM_SUBMISSIONS_CHANNEL_ID}> otherwise your scores will be revoked.**\n\n"
    "Leaderboard shows the highest single verified submissions by crew (pending proofs are excluded). "
    "Admins and SNCO can use /hlltankscoreadmin to set a crew's stats."
)
LEADERBOARD_DESCRIPTION_MONTHLY = (
    "Showing highest single verified submissions for the current month (by crew). Use /hllarmtopscores to view all-time leaders."
)

# ---------------- Helpers ----------------
def normalize_crew_ids(ids: list[int]) -> list[int]:
    """Unique + sort crew IDs, max 3."""
    uniq = sorted(set(int(x) for x in ids))
    return uniq[:3]

def crew_key_from_ids(ids: list[int]) -> str:
    """Build a deterministic key from crew IDs."""
    return ",".join(str(x) for x in normalize_crew_ids(ids))

def crew_mentions_from_key(bot: commands.Bot, crew_key: str) -> str:
    """Return a human-friendly crew string from the key."""
    if not crew_key:
        return "(no crew)"
    parts = []
    for s in crew_key.split(","):
        try:
            uid = int(s)
        except ValueError:
            continue
        user = bot.get_user(uid)
        parts.append(user.mention if user else f"<@{uid}>")
    return ", ".join(parts) if parts else "(no crew)"

def is_life_stat(stat: str) -> bool:
    return stat == LIFE_STAT_NAME or stat == OLD_LIFE_STAT_NAME

def parse_time_to_seconds(text: str) -> int:
    """
    Parse 'H:MM' or 'H:MM:SS' into total seconds.
    Raises ValueError on bad format or out-of-range values.
    """
    s = (text or "").strip()
    if ":" not in s:
        raise ValueError("Missing ':'")
    parts = [p.strip() for p in s.split(":")]
    if len(parts) == 2:
        h_str, m_str = parts
        if not (h_str.isdigit() and m_str.isdigit()):
            raise ValueError("Non-numeric")
        h = int(h_str); m = int(m_str); s_val = 0
    elif len(parts) == 3:
        h_str, m_str, sec_str = parts
        if not (h_str.isdigit() and m_str.isdigit() and sec_str.isdigit()):
            raise ValueError("Non-numeric")
        h = int(h_str); m = int(m_str); s_val = int(sec_str)
    else:
        raise ValueError("Invalid time format")
    if h < 0 or m < 0 or m >= 60 or s_val < 0 or s_val >= 60:
        raise ValueError("Out of range")
    return h * 3600 + m * 60 + s_val

def format_seconds_as_hhmmss(total_seconds: int) -> str:
    if total_seconds is None:
        return "0:00:00"
    if total_seconds < 0:
        total_seconds = 0
    h = total_seconds // 3600
    rem = total_seconds % 3600
    m = rem // 60
    s = rem % 60
    return f"{h}:{m:02d}:{s:02d}"

# ---------------- Database (async with aiosqlite) ----------------
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        # Crew-based armour submissions table (unique to this cog)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS submissions_arm (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                submitter_id INTEGER,
                crew_key TEXT,
                stat TEXT,
                value INTEGER,
                submitted_at TEXT,
                needs_proof INTEGER DEFAULT 0,
                proof_verified INTEGER DEFAULT 1,
                proof_deadline TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await db.commit()

async def migrate_life_stat_to_seconds():
    """
    Migrate any old 'HH:MM' life records to seconds and rename stat to 'HH:MM:SS'.
    Safe to run multiple times.
    """
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute(
                "UPDATE submissions_arm SET value = value * 60, stat = ? WHERE stat = ?",
                (LIFE_STAT_NAME, OLD_LIFE_STAT_NAME),
            )
            await db.commit()
    except Exception as e:
        print(f"HLLArmLeaderboard: migration failed: {e}")

# ---------------- Cog ----------------
class HLLArmLeaderboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._synced = False
        self._db_initialized = False
        self._view_registered = False
        self._cleanup_started = False

    async def _get_channel(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                channel = None
        return channel

    # ---------- Metadata for armour leaderboard message ----------
    async def get_leaderboard_message(self):
        async with aiosqlite.connect(DB_FILE) as db:
            cur = await db.execute("SELECT value FROM metadata WHERE key = ?", ("arm_leaderboard_message_id",))
            row = await cur.fetchone()
        if not row:
            return None
        channel = await self._get_channel(ARM_LEADERBOARD_CHANNEL_ID)
        if not channel:
            return None
        try:
            return await channel.fetch_message(int(row[0]))
        except Exception:
            return None

    async def set_leaderboard_message(self, message_id: int):
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute(
                "INSERT INTO metadata(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("arm_leaderboard_message_id", str(message_id)),
            )
            await db.commit()

    # ---------- Build embeds ----------
    async def build_leaderboard_embed(self, monthly: bool = False):
        embed = discord.Embed(
            title="Hell Let Loose Armour Leaderboard" + (" - This Month" if monthly else ""),
            color=discord.Color.blurple(),
        )
        embed.description = LEADERBOARD_DESCRIPTION_MONTHLY if monthly else LEADERBOARD_DESCRIPTION

        async with aiosqlite.connect(DB_FILE) as db:
            for stat in STATS_ARM:
                if monthly:
                    now = datetime.datetime.utcnow()
                    start_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
                    query = """
                    WITH bests AS (
                        SELECT crew_key, MAX(value) AS best
                        FROM submissions_arm
                        WHERE stat = ? AND proof_verified = 1 AND submitted_at >= ?
                        GROUP BY crew_key
                    ),
                    achieved AS (
                        SELECT s.crew_key, b.best,
                               MIN(s.submitted_at) AS first_achieved_at
                        FROM submissions_arm s
                        JOIN bests b
                          ON b.crew_key = s.crew_key
                         AND s.value = b.best
                        WHERE s.stat = ? AND s.proof_verified = 1 AND s.submitted_at >= ?
                        GROUP BY s.crew_key
                    )
                    SELECT crew_key, best, first_achieved_at
                    FROM achieved
                    ORDER BY best DESC, first_achieved_at ASC, crew_key ASC
                    LIMIT 5
                    """
                    params = (stat, start_month, stat, start_month)
                else:
                    query = """
                    WITH bests AS (
                        SELECT crew_key, MAX(value) AS best
                        FROM submissions_arm
                        WHERE stat = ? AND proof_verified = 1
                        GROUP BY crew_key
                    ),
                    achieved AS (
                        SELECT s.crew_key, b.best,
                               MIN(s.submitted_at) AS first_achieved_at
                        FROM submissions_arm s
                        JOIN bests b
                          ON b.crew_key = s.crew_key
                         AND s.value = b.best
                        WHERE s.stat = ? AND s.proof_verified = 1
                        GROUP BY s.crew_key
                    )
                    SELECT crew_key, best, first_achieved_at
                    FROM achieved
                    ORDER BY best DESC, first_achieved_at ASC, crew_key ASC
                    LIMIT 5
                    """
                    params = (stat, stat)

                cur = await db.execute(query, params)
                rows = await cur.fetchall()

                if rows:
                    lines = []
                    for idx, (crew_key, best, first_achieved_at) in enumerate(rows, 1):
                        crew_str = crew_mentions_from_key(self.bot, crew_key)
                        achieved_str = ""
                        if first_achieved_at:
                            try:
                                dt = datetime.datetime.fromisoformat(first_achieved_at)
                                achieved_str = f" ({dt.strftime('%d/%m/%y')})"
                            except Exception:
                                pass
                        display_val = format_seconds_as_hhmmss(best) if is_life_stat(stat) else str(best)
                        lines.append(f"**{idx}.** {crew_str} — {display_val}{achieved_str}")
                    embed.add_field(name=stat, value="\n".join(lines), inline=False)
                else:
                    embed.add_field(name=stat, value="No data yet", inline=False)

        now_str = datetime.datetime.utcnow().strftime("%d/%m/%y %H:%M GMT")
        embed.set_footer(text=f"Last updated: {now_str}")
        return embed

    async def update_leaderboard(self):
        if ARM_LEADERBOARD_CHANNEL_ID == 0:
            print("HLLArmLeaderboard: ARM_LEADERBOARD_CHANNEL_ID not set. Skipping update.")
            return
        channel = await self._get_channel(ARM_LEADERBOARD_CHANNEL_ID)
        if not channel:
            print("HLLArmLeaderboard: ARM_LEADERBOARD_CHANNEL_ID not found. Skipping update.")
            return

        embed = await self.build_leaderboard_embed(monthly=False)
        msg = await self.get_leaderboard_message()

        if msg:
            try:
                await msg.edit(embed=embed, view=ArmLeaderboardView(self))
            except Exception as e:
                print(f"HLLArmLeaderboard: Failed to edit leaderboard message: {e}")
        else:
            try:
                new_msg = await channel.send(embed=embed, view=ArmLeaderboardView(self))
                await self.set_leaderboard_message(new_msg.id)
            except Exception as e:
                print(f"HLLArmLeaderboard: Failed to send leaderboard message: {e}")

    # ---------- Pending proof helper ----------
    async def get_active_pending_proof(self, submitter_id: int):
        now_iso = datetime.datetime.utcnow().isoformat()
        async with aiosqlite.connect(DB_FILE) as db:
            cur = await db.execute(
                """
                SELECT id, proof_deadline FROM submissions_arm
                WHERE submitter_id=? AND needs_proof=1 AND proof_verified=0
                  AND (proof_deadline IS NULL OR proof_deadline >= ?)
                ORDER BY submitted_at ASC
                LIMIT 1
                """,
                (submitter_id, now_iso),
            )
            return await cur.fetchone()

    # ---------- Bot lifecycle ----------
    @commands.Cog.listener()
    async def on_ready(self):
        if not self._db_initialized:
            try:
                await init_db()
                self._db_initialized = True
            except Exception as e:
                print(f"HLLArmLeaderboard: DB init failed: {e}")

        # Run migration to seconds format for Longest Life
        try:
            await migrate_life_stat_to_seconds()
        except Exception as e:
            print(f"HLLArmLeaderboard: life stat migration error: {e}")

        if not self._view_registered:
            try:
                self.bot.add_view(ArmLeaderboardView(self))  # persistent
                self._view_registered = True
            except Exception as e:
                print(f"HLLArmLeaderboard: Failed to register persistent view: {e}")

        if not self._synced:
            try:
                await self.bot.tree.sync(guild=discord.Object(id=GUILD_ID))
                self._synced = True
            except Exception as e:
                print(f"HLLArmLeaderboard: Command sync failed: {e}")

        if not self._cleanup_started:
            try:
                self.proof_cleanup.start()
                self._cleanup_started = True
            except Exception as e:
                print(f"HLLArmLeaderboard: Failed to start cleanup loop: {e}")

        await self.update_leaderboard()

    # ---------- Slash commands (unique names for this cog) ----------
    @app_commands.command(name="hllarmtopscores", description="Show all-time armour crew top scores")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def hllarmtopscores(self, interaction: discord.Interaction):
        embed = await self.build_leaderboard_embed(monthly=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="hllarmmonthtopscores", description="Show this month's armour crew top scores")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def hllarmmonthtopscores(self, interaction: discord.Interaction):
        embed = await self.build_leaderboard_embed(monthly=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # Admin: overwrite a crew's record for a stat (unique command name)
    @app_commands.command(
        name="hlltankscoreadmin",
        description="Admin: set a crew's high score for a stat. Set value to 0 to remove this crew from leaderboard."
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def hlltankscoreadmin(
        self,
        interaction: discord.Interaction,
        user1: discord.Member,
        stat: str,
        value: str,
        user2: Optional[discord.Member] = None,
        user3: Optional[discord.Member] = None,
    ):
        invoker = interaction.user
        has_admin_role = any(r.id in ADMIN_ROLE_IDS for r in getattr(invoker, "roles", []))
        if not has_admin_role:
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        if stat not in STATS_ARM:
            await interaction.response.send_message("Invalid stat.", ephemeral=True)
            return

        # Parse value based on stat
        parsed_value: Optional[int] = None
        value_str = (value or "").strip()
        try:
            if is_life_stat(stat):
                # Allow "0" as removal shorthand
                if value_str == "0":
                    parsed_value = 0
                else:
                    parsed_value = parse_time_to_seconds(value_str)  # store as seconds
            else:
                parsed_value = int(value_str)
                if parsed_value < 0:
                    raise ValueError("negative")
        except Exception:
            if is_life_stat(stat):
                await interaction.response.send_message(
                    "Invalid value for Longest Life. Enter H:MM or H:MM:SS (e.g. 1:23 or 1:23:45), or 0 to remove.",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "Invalid score. Enter a non-negative integer (e.g. 10) or 0 to remove.",
                    ephemeral=True
                )
            return

        crew_ids = [user1.id]
        if user2: crew_ids.append(user2.id)
        if user3: crew_ids.append(user3.id)
        crew_ids = normalize_crew_ids(crew_ids)
        if not (1 <= len(crew_ids) <= 3):
            await interaction.response.send_message("Crew must contain 1 to 3 unique members.", ephemeral=True)
            return
        crew_key = crew_key_from_ids(crew_ids)

        try:
            async with aiosqlite.connect(DB_FILE) as db:
                # Capture previous verified best for this crew+stat
                cur = await db.execute(
                    "SELECT MAX(value) FROM submissions_arm WHERE crew_key=? AND stat=? AND proof_verified=1",
                    (crew_key, stat),
                )
                row = await cur.fetchone()
                prev_best = row[0] if row and row[0] is not None else 0
                prev_best_display = format_seconds_as_hhmmss(prev_best) if is_life_stat(stat) else str(prev_best)

                # If parsed_value is 0, remove crew from this stat's leaderboard (delete all, do not insert)
                if parsed_value == 0:
                    await db.execute("DELETE FROM submissions_arm WHERE crew_key=? AND stat=?", (crew_key, stat))
                    await db.commit()
                    await self.update_leaderboard()
                    crew_str = ", ".join(m.mention for m in [user1, user2, user3] if m)
                    await interaction.response.send_message(
                        f"Removed {crew_str} from the {stat} leaderboard. Previous verified best was {prev_best_display}.",
                        ephemeral=True,
                    )
                    return

                # Overwrite existing rows for this crew+stat
                await db.execute("DELETE FROM submissions_arm WHERE crew_key=? AND stat=?", (crew_key, stat))
                now_iso = datetime.datetime.utcnow().isoformat()
                # Use invoker as submitter_id for admin action
                await db.execute(
                    "INSERT INTO submissions_arm(submitter_id, crew_key, stat, value, submitted_at, needs_proof, proof_verified) VALUES(?, ?, ?, ?, ?, 0, 1)",
                    (invoker.id, crew_key, stat, int(parsed_value), now_iso),
                )
                await db.commit()
        except Exception as e:
            await interaction.response.send_message(f"Failed to set high score: {e}", ephemeral=True)
            return

        await self.update_leaderboard()
        crew_str = ", ".join(m.mention for m in [user1, user2, user3] if m)
        new_val_display = format_seconds_as_hhmmss(parsed_value) if is_life_stat(stat) else str(parsed_value)
        await interaction.response.send_message(
            f"Set {crew_str}'s {stat} high score to {new_val_display}. Previous verified best was {prev_best_display}.",
            ephemeral=True,
        )

    # --- Autocomplete for "stat" argument ---
    @hlltankscoreadmin.autocomplete("stat")
    async def stat_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str
    ):
        return [
            app_commands.Choice(name=s, value=s)
            for s in STATS_ARM if current.lower() in s.lower()
        ][:25]  # max 25 choices allowed


    # ---------- Listener: proof uploads for armour submissions ----------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if ARM_SUBMISSIONS_CHANNEL_ID == 0 or message.channel.id != ARM_SUBMISSIONS_CHANNEL_ID:
            return
        if not message.attachments:
            return

        def is_image(att: discord.Attachment) -> bool:
            ct = (att.content_type or "").lower()
            if ct.startswith("image/"):
                return True
            name = att.filename.lower()
            return name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"))

        if not any(is_image(a) for a in message.attachments):
            return

        try:
            now_iso = datetime.datetime.utcnow().isoformat()
            async with aiosqlite.connect(DB_FILE) as db:
                cur = await db.execute(
                    """
                    SELECT id FROM submissions_arm
                    WHERE submitter_id=? AND needs_proof=1 AND proof_verified=0
                      AND (proof_deadline IS NULL OR proof_deadline >= ?)
                    ORDER BY submitted_at ASC
                    LIMIT 1
                    """,
                    (message.author.id, now_iso),
                )
                row = await cur.fetchone()
                if not row:
                    return

                submission_id = row[0]
                await db.execute(
                    "UPDATE submissions_arm SET needs_proof=0, proof_verified=1 WHERE id=?",
                    (submission_id,),
                )
                await db.commit()

            try:
                await message.add_reaction("✅")
                await message.channel.send(
                    f"Thanks {message.author.mention}, your screenshot has been verified for armour crew submission #{submission_id}."
                )
            except Exception:
                pass

            await self.update_leaderboard()

        except Exception as e:
            print(f"HLLArmLeaderboard: on_message proof handling failed: {e}")

    # ---------- Background: Cleanup expired pending proofs ----------
    @tasks.loop(minutes=1)
    async def proof_cleanup(self):
        try:
            async with aiosqlite.connect(DB_FILE) as db:
                now_iso = datetime.datetime.utcnow().isoformat()
                cur = await db.execute(
                    """
                    SELECT id, submitter_id, stat, value
                    FROM submissions_arm
                    WHERE needs_proof=1 AND proof_verified=0 AND proof_deadline IS NOT NULL AND proof_deadline < ?
                    """,
                    (now_iso,),
                )
                rows = await cur.fetchall()

                if rows:
                    await db.executemany("DELETE FROM submissions_arm WHERE id=?", [(r[0],) for r in rows])
                    await db.commit()

                    channel = await self._get_channel(ARM_SUBMISSIONS_CHANNEL_ID)
                    if channel:
                        for sid, uid, stat, val in rows:
                            try:
                                val_display = format_seconds_as_hhmmss(val) if is_life_stat(stat) else str(val)
                                await channel.send(
                                    f"<@{uid}> your armour crew submission #{sid} ({val_display} {stat}) was removed "
                                    f"due to missing screenshot within {PROOF_TIMEOUT_MINUTES} minutes."
                                )
                            except Exception:
                                pass

                    await self.update_leaderboard()
        except Exception as e:
            print(f"HLLArmLeaderboard: proof cleanup failed: {e}")

    @proof_cleanup.before_loop
    async def before_proof_cleanup(self):
        await self.bot.wait_until_ready()

# ---------------- Submission Modal ----------------
class ArmSubmissionModal(Modal):
    def __init__(self, cog: HLLArmLeaderboard, stat: str, submitter: discord.abc.User, crew_key: str):
        super().__init__(title=f"Submit {stat} (Armour Crew)")
        self.cog = cog
        self.stat = stat
        self.submitter = submitter
        self.crew_key = crew_key

        if is_life_stat(stat):
            self.value_input = TextInput(label="Enter your time (H:MM or H:MM:SS)", placeholder="e.g. 1:23 or 1:23:45", required=True)
        else:
            self.value_input = TextInput(label="Enter your score", placeholder="e.g. 10", required=True)
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        # Safety: block if submitter has a pending proof
        pending = await self.cog.get_active_pending_proof(self.submitter.id)
        if pending:
            sub_id, deadline_iso = pending
            remaining_txt = ""
            try:
                if deadline_iso:
                    deadline = datetime.datetime.fromisoformat(deadline_iso)
                    secs = (deadline - datetime.datetime.utcnow()).total_seconds()
                    if secs > 0:
                        mins = math.ceil(secs / 60)
                        remaining_txt = f" (~{mins} minute(s) remaining)"
            except Exception:
                pass
            await interaction.response.send_message(
                f"You already have an armour crew submission pending screenshot verification (#{sub_id}). "
                f"Please upload an image in <#{ARM_SUBMISSIONS_CHANNEL_ID}> or wait for the timeout{remaining_txt} before submitting again.",
                ephemeral=True,
            )
            return

        # Validate input
        try:
            raw_value = str(self.value_input.value).strip()
            if is_life_stat(self.stat):
                value = parse_time_to_seconds(raw_value)  # seconds
            else:
                value = int(raw_value)
                if value < 0:
                    raise ValueError("negative")
        except (TypeError, ValueError):
            if is_life_stat(self.stat):
                await interaction.response.send_message("Please enter time as H:MM or H:MM:SS (e.g. 1:23 or 1:23:45).", ephemeral=True)
            else:
                await interaction.response.send_message("Please enter a valid non-negative integer.", ephemeral=True)
            return

        # Insert submission as verified; may be flipped to pending if proof required
        try:
            async with aiosqlite.connect(DB_FILE) as db:
                cur = await db.execute(
                    """
                    INSERT INTO submissions_arm(submitter_id, crew_key, stat, value, submitted_at, needs_proof, proof_verified)
                    VALUES(?, ?, ?, ?, ?, 0, 1)
                    """,
                    (self.submitter.id, self.crew_key, self.stat, value, datetime.datetime.utcnow().isoformat()),
                )
                submission_id = cur.lastrowid
                await db.commit()
        except Exception as e:
            await interaction.response.send_message(f"Failed to record submission: {e}", ephemeral=True)
            return

        await self.cog.update_leaderboard()

        # Decide if screenshot is required
        submissions_channel = await self.cog._get_channel(ARM_SUBMISSIONS_CHANNEL_ID)
        require_ss = random.random() < 0.7
        display_value = format_seconds_as_hhmmss(value) if is_life_stat(self.stat) else str(value)

        if submissions_channel:
            try:
                if require_ss:
                    deadline = datetime.datetime.utcnow() + datetime.timedelta(minutes=PROOF_TIMEOUT_MINUTES)
                    try:
                        async with aiosqlite.connect(DB_FILE) as db:
                            await db.execute(
                                "UPDATE submissions_arm SET needs_proof=1, proof_verified=0, proof_deadline=? WHERE id=?",
                                (deadline.isoformat(), submission_id),
                            )
                            await db.commit()
                    except Exception:
                        pass

                    await submissions_channel.send(
                        f"{self.submitter.mention} submitted {display_value} {self.stat} for crew: {crew_mentions_from_key(self.cog.bot, self.crew_key)}. "
                        f"Screenshot required.\nPlease upload an image in this channel within {PROOF_TIMEOUT_MINUTES} minutes.\n"
                        f"Submission ID: #{submission_id}"
                    )
                    await self.cog.update_leaderboard()
                else:
                    await submissions_channel.send(
                        f"{self.submitter.mention} submitted {display_value} {self.stat} for crew: {crew_mentions_from_key(self.cog.bot, self.crew_key)}! "
                        f"No screenshot required this time."
                    )
            except Exception:
                pass

        await interaction.response.send_message("Armour crew submission recorded!", ephemeral=True)

# ---------------- Two-step crew selection view ----------------
class CrewSelectView(View):
    def __init__(self, cog: HLLArmLeaderboard, stat: str):
        super().__init__(timeout=180)  # 3 minutes
        self.cog = cog
        self.stat = stat
        self.add_item(CrewUserSelect(cog, stat))

class CrewUserSelect(UserSelect):
    def __init__(self, cog: HLLArmLeaderboard, stat: str):
        super().__init__(placeholder="Select 1–3 crew members", min_values=1, max_values=3)
        self.cog = cog
        self.stat = stat

    async def callback(self, interaction: discord.Interaction):
        # Validate selection (no bots)
        selected_users = [u for u in self.values if not getattr(u, "bot", False)]
        if len(selected_users) != len(self.values):
            await interaction.response.send_message("Bots cannot be part of a crew. Please select human members only.", ephemeral=True)
            return

        crew_ids = [u.id for u in selected_users]
        crew_ids = normalize_crew_ids(crew_ids)
        crew_key = crew_key_from_ids(crew_ids)

        # Optional safety: block if pending
        pending = await self.cog.get_active_pending_proof(interaction.user.id)
        if pending:
            sub_id, deadline_iso = pending
            remaining_txt = ""
            try:
                if deadline_iso:
                    deadline = datetime.datetime.fromisoformat(deadline_iso)
                    secs = (deadline - datetime.datetime.utcnow()).total_seconds()
                    if secs > 0:
                        mins = math.ceil(secs / 60)
                        remaining_txt = f" (~{mins} minute(s) remaining)"
            except Exception:
                pass
            await interaction.response.send_message(
                f"You already have an armour crew submission pending screenshot verification (#{sub_id}). "
                f"Please upload an image in <#{ARM_SUBMISSIONS_CHANNEL_ID}> or wait for the timeout{remaining_txt} before submitting again.",
                ephemeral=True,
            )
            return

        # Open score modal with chosen crew
        await interaction.response.send_modal(ArmSubmissionModal(self.cog, self.stat, interaction.user, crew_key))

# ---------------- Persistent view (stat select) ----------------
class ArmLeaderboardView(View):
    def __init__(self, cog: HLLArmLeaderboard):
        super().__init__(timeout=None)
        self.cog = cog
        self.add_item(ArmStatSelect(cog))

class ArmStatSelect(Select):
    def __init__(self, cog: HLLArmLeaderboard):
        self.cog = cog
        options = [discord.SelectOption(label=stat, value=stat) for stat in STATS_ARM]
        # custom_id for persistence (unique to this cog)
        super().__init__(placeholder="Select armour stat to submit", options=options, custom_id="hll_arm_stat_select")

    async def callback(self, interaction: discord.Interaction):
        # Early block: prevent starting if submitter has a pending proof
        pending = await self.cog.get_active_pending_proof(interaction.user.id)
        if pending:
            sub_id, deadline_iso = pending
            remaining_txt = ""
            try:
                if deadline_iso:
                    deadline = datetime.datetime.fromisoformat(deadline_iso)
                    secs = (deadline - datetime.datetime.utcnow()).total_seconds()
                    if secs > 0:
                        mins = math.ceil(secs / 60)
                        remaining_txt = f" (~{mins} minute(s) remaining)"
            except Exception:
                pass
            await interaction.response.send_message(
                f"You already have an armour crew submission pending screenshot verification (#{sub_id}). "
                f"Please upload an image in <#{ARM_SUBMISSIONS_CHANNEL_ID}> or wait for the timeout{remaining_txt} before submitting again.",
                ephemeral=True,
            )
            return

        stat = self.values[0]
        # Start two-step: send ephemeral crew selection view
        await interaction.response.send_message(
            f"Selected: {stat}\nNow select your crew members (1–3).",
            view=CrewSelectView(self.cog, stat),
            ephemeral=True,
        )

# ---------------- Setup ----------------
async def setup(bot: commands.Bot):
    await bot.add_cog(HLLArmLeaderboard(bot))
