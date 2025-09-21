import discord
from discord.ext import commands, tasks
from discord import app_commands
from discord.ui import View, Select, Modal, TextInput
import aiosqlite
import random
import datetime
import math

# ---------------- Config ----------------
GUILD_ID = 1097913605082579024  # replace with your guild ID
LEADERBOARD_CHANNEL_ID = 1419010804832800859  # replace with your leaderboard channel
SUBMISSIONS_CHANNEL_ID = 1419010992578363564  # replace with your submissions channel

# Support multiple admin roles
ADMIN_ROLE_IDS = {
    1213495462632361994,
    1097915860322091090,
    1097946543065137183,
}

DB_FILE = "leaderboard.db"

# Minutes allowed to provide a screenshot when one is required
PROOF_TIMEOUT_MINUTES = 5

STATS = ["Most Kills", "Most Artillery Kills", "Most Vehicles Destroyed", "Highest Killstreak", "Most Satchel Kills"]

# Text shown under the embed title
LEADERBOARD_DESCRIPTION = (
    f"Submit your scores using the selector below, we're looking for your high scores across one game of 2hr 30mins or less of Hell Let Loose. Submissions are community-reported in <#{1419010992578363564}> and will be reviewed.\n\n"
    "**You must have a screenshot to back up your submissions, it is requested on a random basis and if called upon you must post it "
    f"in <#{1419010992578363564}> otherwise your scores will be revoked.**\n\n"
    "Leaderboard shows the highest single verified submissions (pending proofs are excluded). "
    "Admins and SNCO can use /hllstatsadmin to change or revoke your stats anytime as required."
)
LEADERBOARD_DESCRIPTION_MONTHLY = (
    "Showing highest single verified submissions for the current month. Use /hlltopscores to view all-time leaders."
)

# ---------------- Database (async with aiosqlite) ----------------
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                stat TEXT,
                value INTEGER,
                submitted_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        # Add columns for screenshot proof flow if missing (safe to run on every start)
        try:
            await db.execute("ALTER TABLE submissions ADD COLUMN needs_proof INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE submissions ADD COLUMN proof_verified INTEGER DEFAULT 1")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE submissions ADD COLUMN proof_deadline TEXT")
        except Exception:
            pass
        await db.commit()

# ---------------- Cog ----------------
class HLLLeaderboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._synced = False       # ensure we sync app commands once
        self._db_initialized = False
        self._view_registered = False  # persistent view registered once
        self._cleanup_started = False  # start proof cleanup loop once

    async def _get_channel(self, channel_id: int):
        """Try cache first, then API as a fallback."""
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                channel = None
        return channel

    async def get_leaderboard_message(self):
        async with aiosqlite.connect(DB_FILE) as db:
            cursor = await db.execute("SELECT value FROM metadata WHERE key = ?", ("leaderboard_message_id",))
            row = await cursor.fetchone()

        if not row:
            return None

        channel = await self._get_channel(LEADERBOARD_CHANNEL_ID)
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
                ("leaderboard_message_id", str(message_id)),
            )
            await db.commit()

    async def build_leaderboard_embed(self, monthly: bool = False):
        embed = discord.Embed(
            title="Hell Let Loose Leaderboard" + (" - This Month" if monthly else ""),
            color=discord.Color.dark_gold(),
        )
        # Add descriptive text under the title
        embed.description = LEADERBOARD_DESCRIPTION_MONTHLY if monthly else LEADERBOARD_DESCRIPTION

        async with aiosqlite.connect(DB_FILE) as db:
            for stat in STATS:
                if monthly:
                    now = datetime.datetime.utcnow()
                    start_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
                    # Count only verified submissions; tie-break by earliest achievement, then user_id
                    query = """
                    WITH bests AS (
                        SELECT user_id, MAX(value) AS best
                        FROM submissions
                        WHERE stat = ? AND proof_verified = 1 AND submitted_at >= ?
                        GROUP BY user_id
                    ),
                    achieved AS (
                        SELECT s.user_id, b.best,
                               MIN(s.submitted_at) AS first_achieved_at
                        FROM submissions s
                        JOIN bests b
                          ON b.user_id = s.user_id
                         AND s.value = b.best
                        WHERE s.stat = ? AND s.proof_verified = 1 AND s.submitted_at >= ?
                        GROUP BY s.user_id
                    )
                    SELECT user_id, best, first_achieved_at
                    FROM achieved
                    ORDER BY best DESC, first_achieved_at ASC, user_id ASC
                    LIMIT 5
                    """
                    params = (stat, start_month, stat, start_month)
                else:
                    # All-time: only verified; tie-break by earliest achievement, then user_id
                    query = """
                    WITH bests AS (
                        SELECT user_id, MAX(value) AS best
                        FROM submissions
                        WHERE stat = ? AND proof_verified = 1
                        GROUP BY user_id
                    ),
                    achieved AS (
                        SELECT s.user_id, b.best,
                               MIN(s.submitted_at) AS first_achieved_at
                        FROM submissions s
                        JOIN bests b
                          ON b.user_id = s.user_id
                         AND s.value = b.best
                        WHERE s.stat = ? AND s.proof_verified = 1
                        GROUP BY s.user_id
                    )
                    SELECT user_id, best, first_achieved_at
                    FROM achieved
                    ORDER BY best DESC, first_achieved_at ASC, user_id ASC
                    LIMIT 5
                    """
                    params = (stat, stat)

                cursor = await db.execute(query, params)
                rows = await cursor.fetchall()

                if rows:
                    lines = []
                    for idx, (user_id, best, first_achieved_at) in enumerate(rows, 1):
                        user = self.bot.get_user(user_id)
                        name = user.mention if user else f"<@{user_id}>"
                        # Optionally show the date achieved; comment out if you don't want it visible
                        # achieved_str = ""
                        # if first_achieved_at:
                        #     try:
                        #         dt = datetime.datetime.fromisoformat(first_achieved_at)
                        #         achieved_str = f" (on {dt.strftime('%Y-%m-%d')})"
                        #     except Exception:
                        #         pass
                        lines.append(f"**{idx}.** {name} — {best}")
                    embed.add_field(name=stat, value="\n".join(lines), inline=False)
                else:
                    embed.add_field(name=stat, value="No data yet", inline=False)

        now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M GMT")
        embed.set_footer(text=f"Last updated: {now_str}")
        return embed

    async def update_leaderboard(self):
        channel = await self._get_channel(LEADERBOARD_CHANNEL_ID)
        if not channel:
            print("HLLLeaderboard: LEADERBOARD_CHANNEL_ID not found. Skipping update.")
            return

        embed = await self.build_leaderboard_embed(monthly=False)
        msg = await self.get_leaderboard_message()

        if msg:
            try:
                await msg.edit(embed=embed, view=LeaderboardView(self))
            except Exception as e:
                print(f"HLLLeaderboard: Failed to edit leaderboard message: {e}")
        else:
            try:
                new_msg = await channel.send(embed=embed, view=LeaderboardView(self))
                await self.set_leaderboard_message(new_msg.id)
            except Exception as e:
                print(f"HLLLeaderboard: Failed to send leaderboard message: {e}")

    # Helper: Check for an active (non-expired) pending proof for a user
    async def get_active_pending_proof(self, user_id: int):
        now_iso = datetime.datetime.utcnow().isoformat()
        async with aiosqlite.connect(DB_FILE) as db:
            cursor = await db.execute(
                """
                SELECT id, proof_deadline FROM submissions
                WHERE user_id=? AND needs_proof=1 AND proof_verified=0
                  AND (proof_deadline IS NULL OR proof_deadline >= ?)
                ORDER BY submitted_at ASC
                LIMIT 1
                """,
                (user_id, now_iso),
            )
            return await cursor.fetchone()

    @commands.Cog.listener()
    async def on_ready(self):
        # Initialize DB once (async)
        if not self._db_initialized:
            try:
                await init_db()
                self._db_initialized = True
            except Exception as e:
                print(f"HLLLeaderboard: DB init failed: {e}")

        # Register persistent view once so interactions work after restart
        if not self._view_registered:
            try:
                self.bot.add_view(LeaderboardView(self))  # persistent (timeout=None + custom_id)
                self._view_registered = True
            except Exception as e:
                print(f"HLLLeaderboard: Failed to register persistent view: {e}")

        # Sync slash commands for this guild (do once)
        if not self._synced:
            try:
                await self.bot.tree.sync(guild=discord.Object(id=GUILD_ID))
                self._synced = True
            except Exception as e:
                print(f"HLLLeaderboard: Command sync failed: {e}")

        # Start cleanup loop
        if not self._cleanup_started:
            try:
                self.proof_cleanup.start()
                self._cleanup_started = True
            except Exception as e:
                print(f"HLLLeaderboard: Failed to start cleanup loop: {e}")

        await self.update_leaderboard()

    @app_commands.command(name="hlltopscores", description="Show all-time top scores")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def hlltopscores(self, interaction: discord.Interaction):
        embed = await self.build_leaderboard_embed(monthly=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="hllmonthtopscores", description="Show top scores for this month")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def hllmonthtopscores(self, interaction: discord.Interaction):
        embed = await self.build_leaderboard_embed(monthly=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---------------- Admin: Adjust Scores (best single score; keep history) ----------------
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.choices(
        stat=[app_commands.Choice(name=s, value=s) for s in STATS],
        mode=[
            app_commands.Choice(name="Submit new score", value="submit"),
            app_commands.Choice(name="Set high score (keep history)", value="set"),
        ],
    )
    @app_commands.command(
        name="hllstatsadmin",
        description="Admin: submit a score or set a user's high score (keeps history; leaderboard uses best single verified score)."
    )
    async def hllstatsadmin(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        stat: app_commands.Choice[str],
        mode: app_commands.Choice[str],
        value: int,
    ):
        # Permission check: must have one of the admin roles
        invoker = interaction.user
        has_admin_role = any(r.id in ADMIN_ROLE_IDS for r in getattr(invoker, "roles", []))
        if not has_admin_role:
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        # Basic validation
        if value < 0:
            await interaction.response.send_message("Score value cannot be negative.", ephemeral=True)
            return

        try:
            async with aiosqlite.connect(DB_FILE) as db:
                # Current best (verified only, for info)
                cursor = await db.execute(
                    "SELECT MAX(value) FROM submissions WHERE user_id=? AND stat=? AND proof_verified=1",
                    (user.id, stat.value),
                )
                row = await cursor.fetchone()
                current_best = row[0] if row and row[0] is not None else 0

                now_iso = datetime.datetime.utcnow().isoformat()

                # Both 'submit' and 'set' insert a verified record; 'set' no longer deletes history
                await db.execute(
                    "INSERT INTO submissions(user_id, stat, value, submitted_at, needs_proof, proof_verified) VALUES(?, ?, ?, ?, 0, 1)",
                    (user.id, stat.value, int(value), now_iso),
                )

                await db.commit()

                # New best after change (verified only)
                cursor = await db.execute(
                    "SELECT MAX(value) FROM submissions WHERE user_id=? AND stat=? AND proof_verified=1",
                    (user.id, stat.value),
                )
                row = await cursor.fetchone()
                new_best = row[0] if row and row[0] is not None else 0

        except Exception as e:
            await interaction.response.send_message(f"Failed to record admin action: {e}", ephemeral=True)
            return

        # Update leaderboard message
        await self.update_leaderboard()

        # Respond
        if mode.value == "submit":
            details = (
                f"Submitted score {value} for {user.mention} — {stat.value}.\n"
                f"Previous verified best: {current_best}\n"
                f"New verified best (all-time): {new_best}"
            )
        else:
            details = (
                f"Set high score for {user.mention} — {stat.value} to {value} (history kept).\n"
                f"Previous verified best: {current_best}\n"
                f"New verified best (all-time): {new_best}"
            )
        # Note: This also affects the current month's leaderboard.
        await interaction.response.send_message(details, ephemeral=True)

    # ---------------- Listener: Capture proof uploads (no reply needed) ----------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Only watch the submissions channel for messages with image attachments
        if message.author.bot:
            return
        if message.channel.id != SUBMISSIONS_CHANNEL_ID:
            return
        if not message.attachments:
            return

        # Check if any attachment looks like an image
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
                # Oldest pending submission still within deadline
                cursor = await db.execute(
                    """
                    SELECT id FROM submissions
                    WHERE user_id=? AND needs_proof=1 AND proof_verified=0
                      AND (proof_deadline IS NULL OR proof_deadline >= ?)
                    ORDER BY submitted_at ASC
                    LIMIT 1
                    """,
                    (message.author.id, now_iso),
                )
                row = await cursor.fetchone()
                if not row:
                    return

                submission_id = row[0]
                await db.execute(
                    "UPDATE submissions SET needs_proof=0, proof_verified=1 WHERE id=?",
                    (submission_id,),
                )
                await db.commit()

            try:
                await message.add_reaction("✅")
                await message.channel.send(
                    f"Thanks {message.author.mention}, your screenshot has been verified for submission #{submission_id}."
                )
            except Exception:
                pass

            # Refresh leaderboard (only verified count now, so this matters)
            await self.update_leaderboard()

        except Exception as e:
            print(f"HLLLeaderboard: on_message proof handling failed: {e}")

    # ---------------- Background: Cleanup expired pending proofs ----------------
    @tasks.loop(minutes=1)
    async def proof_cleanup(self):
        try:
            async with aiosqlite.connect(DB_FILE) as db:
                now_iso = datetime.datetime.utcnow().isoformat()
                cursor = await db.execute(
                    """
                    SELECT id, user_id, stat, value
                    FROM submissions
                    WHERE needs_proof=1 AND proof_verified=0 AND proof_deadline IS NOT NULL AND proof_deadline < ?
                    """,
                    (now_iso,),
                )
                rows = await cursor.fetchall()

                if rows:
                    # Delete expired pending submissions
                    await db.executemany("DELETE FROM submissions WHERE id=?", [(r[0],) for r in rows])
                    await db.commit()

                    # Notify channel and refresh leaderboard
                    channel = await self._get_channel(SUBMISSIONS_CHANNEL_ID)
                    if channel:
                        for sid, uid, stat, val in rows:
                            try:
                                await channel.send(
                                    f"<@{uid}> your submission #{sid} ({val} {stat}) was removed "
                                    f"due to missing screenshot within {PROOF_TIMEOUT_MINUTES} minutes."
                                )
                            except Exception:
                                pass

                    await self.update_leaderboard()
        except Exception as e:
            print(f"HLLLeaderboard: proof cleanup failed: {e}")

    @proof_cleanup.before_loop
    async def before_proof_cleanup(self):
        await self.bot.wait_until_ready()

# ---------------- Submission Modal ----------------
class SubmissionModal(Modal):
    def __init__(self, cog: HLLLeaderboard, stat: str, user: discord.abc.User):
        super().__init__(title=f"Submit {stat}")
        self.cog = cog
        self.stat = stat
        self.user = user

        self.value_input = TextInput(label="Enter your score", placeholder="e.g. 10", required=True)
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        # Hard block: user cannot submit while a pending proof exists and hasn't timed out
        pending = await self.cog.get_active_pending_proof(self.user.id)
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
                f"You already have a submission pending screenshot verification (#{sub_id}). "
                f"Please upload an image in <#{SUBMISSIONS_CHANNEL_ID}> or wait for the timeout{remaining_txt} before submitting again.",
                ephemeral=True,
            )
            return

        # Validate integer
        try:
            value = int(str(self.value_input.value).strip())
        except (TypeError, ValueError):
            await interaction.response.send_message("Please enter a valid integer.", ephemeral=True)
            return

        # Insert into DB (async) and get submission ID (insert verified, then maybe flip to pending)
        try:
            async with aiosqlite.connect(DB_FILE) as db:
                cursor = await db.execute(
                    """
                    INSERT INTO submissions(user_id, stat, value, submitted_at, needs_proof, proof_verified)
                    VALUES(?, ?, ?, ?, 0, 1)
                    """,
                    (self.user.id, self.stat, value, datetime.datetime.utcnow().isoformat()),
                )
                submission_id = cursor.lastrowid
                await db.commit()
        except Exception as e:
            await interaction.response.send_message(f"Failed to record submission: {e}", ephemeral=True)
            return

        # Since leaderboard counts only verified, it already includes this; it may be removed if proof is requested and not provided
        await self.cog.update_leaderboard()

        # Decide if screenshot is required
        submissions_channel = await self.cog._get_channel(SUBMISSIONS_CHANNEL_ID)
        require_ss = random.choice([True, False])

        if submissions_channel:
            try:
                if require_ss:
                    # Mark as needing proof with a deadline (flip to unverified until proof arrives)
                    deadline = datetime.datetime.utcnow() + datetime.timedelta(minutes=PROOF_TIMEOUT_MINUTES)
                    try:
                        async with aiosqlite.connect(DB_FILE) as db:
                            await db.execute(
                                "UPDATE submissions SET needs_proof=1, proof_verified=0, proof_deadline=? WHERE id=?",
                                (deadline.isoformat(), submission_id),
                            )
                            await db.commit()
                    except Exception:
                        pass

                    await submissions_channel.send(
                        f"{self.user.mention} submitted {value} {self.stat}. Screenshot required.\n"
                        f"Please upload an image in this channel within {PROOF_TIMEOUT_MINUTES} minutes.\n"
                        f"Submission ID: #{submission_id}"
                    )
                    # Since we flipped to unverified, refresh to exclude it until verified
                    await self.cog.update_leaderboard()
                else:
                    await submissions_channel.send(
                        f"{self.user.mention} submitted {value} {self.stat}! No screenshot required this time."
                    )
            except Exception:
                pass  # Non-fatal for the user interaction

        await interaction.response.send_message("Submission recorded!", ephemeral=True)

# ---------------- Views (persistent) ----------------
class LeaderboardView(View):
    def __init__(self, cog: HLLLeaderboard):
        # Persistent view: timeout=None; items need fixed custom_id
        super().__init__(timeout=None)
        self.cog = cog
        self.add_item(StatSelect(cog))

class StatSelect(Select):
    def __init__(self, cog: HLLLeaderboard):
        self.cog = cog
        options = [discord.SelectOption(label=stat, value=stat) for stat in STATS]
        # custom_id is required for persistent components
        super().__init__(placeholder="Select stat to submit", options=options, custom_id="hll_stat_select")

    async def callback(self, interaction: discord.Interaction):
        # Early block: prevent opening the modal if user has a pending proof
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
                f"You already have a submission pending screenshot verification (#{sub_id}). "
                f"Please upload an image in <#{SUBMISSIONS_CHANNEL_ID}> or wait for the timeout{remaining_txt} before submitting again.",
                ephemeral=True,
            )
            return

        stat = self.values[0]
        await interaction.response.send_modal(SubmissionModal(self.cog, stat, interaction.user))

# ---------------- Setup ----------------
async def setup(bot: commands.Bot):
    await bot.add_cog(HLLLeaderboard(bot))
