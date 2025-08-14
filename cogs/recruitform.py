import discord
from discord.ext import commands
import sqlite3
import os

# === CONFIGURATION ===
FORM_CHANNEL_ID = 1401634001248190515   # Channel where the form embed/button is posted
ANSWER_POST_CHANNEL_ID = 1098331019364552845  # Channel where form responses are posted

QUESTIONS = [
    "What is your current T17 username?",
    "What is your Age? (as a number/integer)",
    "What is your country of residence?",
    "What is your timezone?",
    "What is your Hell Let Loose in-game level?",
    "What is your Discord username?",
    "How did you find us?",
    "Details of any previous milsim experience (established units, not games you've played)?",
    "What do you enjoy about HLL, particular role and/or play style (offensive/defensive etc)?",
]

DB_PATH = os.path.join(os.path.dirname(__file__), "nickname.db")

class RecruitFormCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.embed_message_id = None
        self.db_setup()

    def db_setup(self):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS recruit_embeds (
                user_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def save_embed_message(self, user_id, channel_id, message_id):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT OR REPLACE INTO recruit_embeds (user_id, channel_id, message_id)
            VALUES (?, ?, ?)
        """, (user_id, channel_id, message_id))
        conn.commit()
        conn.close()

    def get_embed_message(self, user_id):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            SELECT channel_id, message_id FROM recruit_embeds WHERE user_id = ?
        """, (user_id,))
        result = c.fetchone()
        conn.close()
        return result if result else (None, None)

    @commands.Cog.listener()
    async def on_ready(self):
        """Posts the recruitment embed with button when bot starts."""
        channel = self.bot.get_channel(FORM_CHANNEL_ID)
        if not channel:
            print(f"Channel ID {FORM_CHANNEL_ID} not found.")
            return
        embed = discord.Embed(
            title="7DR Recruitment Form",
            description="We need this info to get you all set up with a platoon. In completing this form I agree to be an active member of this unit, positively contributing to the discord server and following all unit/server rules.",
            color=discord.Color.blue()
        )
        view = RecruitButtonView(self)
        msg = await channel.send(embed=embed, view=view)
        self.embed_message_id = msg.id

    async def start_form(self, user: discord.User):
        """starts the form"""
        try:
            dm = await user.create_dm()
            await dm.send("**Welcome! Note if you're on a mobile phone you might need to click the commands button and then close the command pane in order to open the text input to this DM.**")
            answers = []
            for question in QUESTIONS:
                await dm.send(question)
                def check(m):
                    return m.author == user and m.channel == dm
                msg = await self.bot.wait_for('message', check=check, timeout=120)
                answers.append(msg.content.strip())
            await dm.send("Thank you! Your answers are now in the #recruitform-responses channel!\n\n **If you have not done so already, your next and final step of the induction process is to change your Discord nickname to match your in-game name and tag (see pinned messages in #recruitform).**")
            await self.post_answers(user, answers)
        except Exception as e:
            print(f"Error in DM form: {e}")
            try:
                await dm.send("An error occurred while processing your form, please try again the same way you did previously.")
            except Exception:
                pass

    async def post_answers(self, user, answers):
        """Posts the answers to the designated channel."""
        channel = self.bot.get_channel(ANSWER_POST_CHANNEL_ID)
        if channel:
            embed = discord.Embed(
                title="New Recruit Form",
                description=f"User: {user.mention}\nNickname: {user.display_name}",
                color=discord.Color.green()
            )
            for idx, (q, a) in enumerate(zip(QUESTIONS, answers), 1):
                # If this is the age question, check if the answer is an integer between 0 and 18
                if idx == 2:  # Age question is second in the list
                    try:
                        age = int(a)
                        if 0 <= age < 18:
                            a = f"{a} 🚩"
                    except ValueError:
                        pass  # Non-integer answer, do not flag
                embed.add_field(name=f"Q{idx}: {q}", value=f"A: {a}", inline=False)
            message = await channel.send(embed=embed)
            # Save the embed message info for nickname updates
            self.save_embed_message(user.id, channel.id, message.id)
        else:
            print(f"Answer post channel ID {ANSWER_POST_CHANNEL_ID} not found.")

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.nick != after.nick:
            channel_id, message_id = self.get_embed_message(after.id)
            if channel_id and message_id:
                channel = self.bot.get_channel(channel_id)
                if channel:
                    try:
                        message = await channel.fetch_message(message_id)
                        if message.embeds:
                            embed = message.embeds[0]
                            # Update Nickname in the embed description
                            embed.description = f"User: {after.mention}\nNickname: {after.display_name}"
                            await message.edit(embed=embed)
                    except Exception as e:
                        print(f"Failed to update nickname in embed: {e}")

class RecruitButtonView(discord.ui.View):
    def __init__(self, cog):
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
        await interaction.response.send_message(
            "Check your DMs for the recruitment form!", ephemeral=True
        )
        await self.cog.start_form(interaction.user)

async def setup(bot):
    await bot.add_cog(RecruitFormCog(bot))
