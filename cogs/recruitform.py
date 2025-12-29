import asyncio
import discord
from discord.ext import commands
import sqlite3
import os

# === CONFIGURATION ===
FORM_CHANNEL_ID = 1401634001248190515   # Channel where the form embed/button is posted
ANSWER_POST_CHANNEL_ID = 1098331019364552845  # Channel where form responses are posted

QUESTIONS = [
    "**What is your current T17 username?**",
    "**What is your Age? (as a number/integer)**",
    "**What is your country of residence?**",
    "**What is your timezone?**",
    "**What is your Hell Let Loose in-game level?**",
    "**What is your Discord username?**",
    "**How did you find us?**",
    "**Details of any previous milsim experience (established units, not games you've played)?**",
    "**What do you enjoy about HLL, particular role and/or play style (offensive/defensive etc)?**",
]

DB_PATH = os.path.join(os.path.dirname(__file__), "nickname.db")


class RecruitFormCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.embed_message_id = None
        self.db_setup()
        # Track active per-user form sessions (user_id -> asyncio.Task)
        self._sessions: dict[int, asyncio.Task] = {}
        # Prevent posting duplicate embed on reconnects
        self._startup_done = False

    def db_setup(self):
        """Ensure the DB supports multiple submissions per user, migrate if needed."""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        # Detect if table exists
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='recruit_embeds'")
        exists = c.fetchone() is not None

        if not exists:
            # Create new schema allowing multiple messages per user
            c.execute("""
                CREATE TABLE recruit_embeds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL
                )
            """)
            conn.commit()
            conn.close()
            return

        # If table exists, check if it's the old schema (user_id PRIMARY KEY)
        c.execute("PRAGMA table_info(recruit_embeds)")
        cols = c.fetchall()  # cid, name, type, notnull, dflt_value, pk
        pk_cols = [row[1] for row in cols if row[5] > 0]

        if len(pk_cols) == 1 and pk_cols[0] == "user_id" and len(cols) == 3:
            # Migrate old -> new schema with AUTOINCREMENT id
            c.execute("""
                CREATE TABLE IF NOT EXISTS recruit_embeds_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL
                )
            """)
            c.execute("""
                INSERT INTO recruit_embeds_new (user_id, channel_id, message_id)
                SELECT user_id, channel_id, message_id FROM recruit_embeds
            """)
            c.execute("DROP TABLE recruit_embeds")
            c.execute("ALTER TABLE recruit_embeds_new RENAME TO recruit_embeds")

        conn.commit()
        conn.close()

    def save_embed_message(self, user_id: int, channel_id: int, message_id: int):
        """Save a new posted form reference (allows multiple per user)."""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO recruit_embeds (user_id, channel_id, message_id)
            VALUES (?, ?, ?)
        """, (user_id, channel_id, message_id))
        conn.commit()
        conn.close()

    def get_embed_messages(self, user_id: int):
        """Return all (channel_id, message_id) pairs for a user."""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            SELECT channel_id, message_id FROM recruit_embeds
            WHERE user_id = ?
        """, (user_id,))
        results = c.fetchall()
        conn.close()
        return results or []

    async def delete_previous_form_embeds(self):
        """
        Delete previous recruitment form embeds posted by the bot in FORM_CHANNEL_ID.
        This runs at startup so the channel only contains the single current recruitment embed.
        """
        try:
            channel = self.bot.get_channel(FORM_CHANNEL_ID)
            if channel is None:
                # Try fetching if not in cache
                channel = await self.bot.fetch_channel(FORM_CHANNEL_ID)
        except Exception as e:
            print(f"Could not access form channel {FORM_CHANNEL_ID}: {e}")
            return

        try:
            # Inspect recent history and remove older bot-posted form embeds.
            # Increase limit if your channel is busy; adjust as needed.
            async for message in channel.history(limit=200):
                if message.author.id != (self.bot.user.id if self.bot.user else None):
                    continue
                if not message.embeds:
                    continue
                embed = message.embeds[0]
                # Only delete embed messages that match the form title
                if embed.title == "7DR Recruit Form":
                    try:
                        await message.delete()
                        print(f"Deleted previous recruit form embed: {message.id}")
                    except Exception as e:
                        print(f"Failed to delete message {message.id}: {e}")
        except Exception as e:
            print(f"Failed to iterate history in channel {FORM_CHANNEL_ID}: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        """Posts the recruitment embed with button when bot starts. Runs once per session."""
        # Avoid running multiple times on reconnects
        if self._startup_done:
            return

        # Remove prior form embeds so we only have a single current form message.
        await self.delete_previous_form_embeds()

        try:
            channel = self.bot.get_channel(FORM_CHANNEL_ID)
            if channel is None:
                channel = await self.bot.fetch_channel(FORM_CHANNEL_ID)
        except Exception as e:
            print(f"Channel ID {FORM_CHANNEL_ID} not found: {e}")
            return

        embed = discord.Embed(
            title="7DR Recruit Form",
            description=(
                "We need this info to get you all set up! \n"
                "In completing this form I agree to be an active member of this unit,"
                " positively contributing to the discord server chats, taking part in training"
                " sessions 1-2 times per week and regularly attending events. \n\n I understand"
                " if I don't positively contribute and stop communicating with my platoon,"
                " I will be removed from the unit.\n\n **Click the button below to start your application**"
            ),
            color=discord.Color.blue()
        )
        embed.set_image(url="https://cdn.discordapp.com/attachments/1098976074852999261/1441857335378182154/ChatGPT_Image_Nov_22_2025_06_24_48_PM.png?ex=692351c9&is=69220049&hm=9dc55973e194a532f6b4fd576afd712a132713c0d7d48a100b16fb92191ccfe4&")
        
        view = RecruitButtonView(self)
        # Register the view so interactions are handled even if the message is persistent
        try:
            # bot.add_view is synchronous
            self.bot.add_view(view)
        except Exception:
            # Some older discord.py forks may not support add_view; ignore if not available
            pass

        try:
            msg = await channel.send(embed=embed, view=view)
            self.embed_message_id = msg.id
            self._startup_done = True
            print(f"Posted recruit form embed: {msg.id}")
        except Exception as e:
            print(f"Failed to send recruit form embed to channel {FORM_CHANNEL_ID}: {e}")

    async def start_form(self, user: discord.User):
        """Starts the form with the user in DMs."""
        try:
            dm = await user.create_dm()
        except Exception as e:
            print(f"Failed to open DM with {user}: {e}")
            return

        try:
            await dm.send(
                "**Welcome to 7DR Recruitment Form!**\n\n"
                "Filling in this form is one of three short steps to joining us! \n\n"
                "If you're on mobile, you may need to click the speech button to the right in order to open the text input.\n\n"
                "You can type 'cancel' at any time to abort and you can restart by clicking the 'start application' button"
                " in <#1401634001248190515> channel. This form will time-out after 5 minutes. \n\n"
                "By completing this form you agree to follow the rules, be a positive member and attend our events 1-2 times per week.\n\n"
                "Please answer the following questions one by one:\n\n"
            )

            answers = []
            for question in QUESTIONS:
                await dm.send(question)

                def check(m: discord.Message):
                    return m.author == user and m.channel == dm

                try:
                    msg = await self.bot.wait_for('message', check=check, timeout=300)
                except asyncio.TimeoutError:
                    await dm.send("Timed out waiting for a response. Please click the button again to restart the form.")
                    return

                content = msg.content.strip()
                if content.lower() in ("cancel", "stop", "quit", "exit"):
                    await dm.send("Form cancelled. You can restart by clicking the button again.")
                    return

                if not content:
                    await dm.send("I didn't catch that. Please provide a non-empty answer:")
                    try:
                        msg = await self.bot.wait_for('message', check=check, timeout=60)
                        content = msg.content.strip()
                    except asyncio.TimeoutError:
                        await dm.send("Timed out waiting for a response. Please click the button again to restart the form using the button in #recruitform_requests channel.")
                        return
                    if not content:
                        await dm.send("Answer was empty again. Cancelling - please restart the form using the button in #recruitform_requests channel.")
                        return

                answers.append(content)

            # Always post a NEW message; do not update prior ones
            await self.post_answers(user, answers)
            await dm.send(
                "Thank you! Your answers are now in the [#recruitform-responses](<#1098331019364552845>) channel! and are being reviewed by command staff\n\n"
                "2️⃣ Your next step of the induction process is to change your T17 in-game name on Hell Let Loose to"
                " include Pte (Private) at the start and post it in the [#team-17-names](<#1098665953706909848>) channel so we can change that for you. \n\n"
                "If unsure see our [tutorial video](https://discord.com/channels/1097913605082579024/1098665953706909848/1445828966006001808) or ask one of our officers! \n\n"
                "3️⃣ Then add your [7DR] clan tags on the in-game options menu and you're all set! 🥳 \n\n"
                "Discord can be daunting... we have some [tutorial videos](https://discord.com/channels/1097913605082579024/1388800592549511269) to help you sign-up to events and get involved! \n\n"
            )

        except Exception as e:
            print(f"Error in DM form with {user}: {e}")
            try:
                await dm.send("An error occurred while processing your form, please try again the same way you did previously.")
            except Exception:
                pass
        finally:
            # Ensure we clear the session only if this task is the active one
            active = self._sessions.get(user.id)
            if active is asyncio.current_task():
                self._sessions.pop(user.id, None)

    def start_form_session(self, user: discord.User) -> bool:
        """Start the DM form for a user if not already in progress.

        Returns True if a new session was started, False if one is already active.
        """
        existing = self._sessions.get(user.id)
        if existing and not existing.done():
            return False

        task = asyncio.create_task(self.start_form(user))
        self._sessions[user.id] = task
        return True

    async def post_answers(self, user, answers):
        """Posts the answers to the designated channel as a NEW embed every time."""
        channel = self.bot.get_channel(ANSWER_POST_CHANNEL_ID)
        if not channel:
            print(f"Answer post channel ID {ANSWER_POST_CHANNEL_ID} not found.")
            return

        embed = discord.Embed(
            title="New Recruit Form",
            description=f"User: {user.mention}\nNickname: {user.display_name}",
            color=discord.Color.green()
        )
        for idx, (q, a) in enumerate(zip(QUESTIONS, answers), 1):
            # If this is the age question, flag < 18
            if idx == 2:
                try:
                    age = int(a)
                    if 0 <= age < 18:
                        a = f"{a} 🚩"
                except ValueError:
                    pass
            value = f"A: {a}" if a else "A: (no response)"
            embed.add_field(name=f"Q{idx}: {q}", value=value, inline=False)

        message = await channel.send(embed=embed)
        # Record this submission so we can update nicknames across all of a user's posts later
        self.save_embed_message(user.id, channel.id, message.id)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        # Update Nickname in ALL of the user's posted embeds
        if before.nick != after.nick:
            refs = self.get_embed_messages(after.id)
            if not refs:
                return
            for channel_id, message_id in refs:
                try:
                    channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                    message = await channel.fetch_message(message_id)
                    if not message or not message.embeds:
                        continue
                    embed = message.embeds[0]
                    embed.description = f"User: {after.mention}\nNickname: {after.display_name}"
                    await message.edit(embed=embed)
                except Exception as e:
                    # Skip missing/deleted messages quietly
                    print(f"Failed to update nickname in embed {message_id} for user {after.id}: {e}")


class RecruitButtonView(discord.ui.View):
    def __init__(self, cog: RecruitFormCog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Start Application", style=discord.ButtonStyle.green, custom_id="recruit_start")
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Only respond if this is the correct channel/message
        if interaction.channel.id != FORM_CHANNEL_ID or (
            self.cog.embed_message_id and interaction.message.id != self.cog.embed_message_id
        ):
            await interaction.response.send_message("Wrong channel or message.", ephemeral=True)
            return

        user_id = interaction.user.id

        started = self.cog.start_form_session(interaction.user)
        if not started:
            await interaction.response.send_message(
                "You already have a form in progress in your DMs. Please complete it or wait for it to time out.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            "Check your DMs for the recruitment form!",
            ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(RecruitFormCog(bot))
