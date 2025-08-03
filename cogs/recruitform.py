import discord
from discord.ext import commands

# === CONFIGURATION ===
FORM_CHANNEL_ID = 1099806153170489485   # Channel where the form embed/button is posted
ANSWER_POST_CHANNEL_ID = 1099806153170489485  # Channel where form responses are posted

QUESTIONS = [
    "What is your current T17 username?",
    "What is your Age?",
    "What is your country of residence?",
    "What is your timezone?",
    "What is your Hell Let Loose in-game level?",
    "What is your Discord username?",
    "How did you find us?",
    "Details of any previous milsim experience (established units, not games you've played)?",
    "What do you enjoy about HLL, particular role and/or play style (offensive/defensive etc)?",
]

# Optional: Uncomment and customize for role mapping
# ROLE_MAPPING = {
#    "yes": 123456789012345678,  # Example role IDs
#    "python": 234567890123456789,
# }

class RecruitFormCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.embed_message_id = None

    @commands.Cog.listener()
    async def on_ready(self):
        """Posts the recruitment embed with button when bot starts."""
        channel = self.bot.get_channel(FORM_CHANNEL_ID)
        if not channel:
            print(f"Channel ID {FORM_CHANNEL_ID} not found.")
            return
        embed = discord.Embed(
            title="7DR Recruitment Form",
            description="We need this info to get you all set up with a platoon. In completing this form I agree to be an active member of this unit, positively contributing to the discord server chats, taking part in training sessions 1-2 times per week and regularly attending Friday events. \n\n I understand if I don't positively contribute and stop communicating with my platoon, I will be removed from the unit. \n\n **Click the button below to start your application.**",
            color=discord.Color.blue()
        )
        view = RecruitButtonView(self)
        msg = await channel.send(embed=embed, view=view)
        self.embed_message_id = msg.id

    async def start_form(self, user: discord.User):
        """starts the form"""
        try:
            dm = await user.create_dm("Welcome! Note if you're on a mobile phone you might need to click the commands button and then close the command pane in order to open the text input to this DM and see your phone keyboard!")
            answers = []
            for question in QUESTIONS:
                await dm.send(question)
                def check(m):
                    return m.author == user and m.channel == dm
                msg = await self.bot.wait_for('message', check=check, timeout=120)
                answers.append(msg.content.strip())
            await dm.send("Thank you! Processing your answers...")
            await self.post_answers(user, answers)
            # await self.process_roles(user, answers)  # Uncomment if using roles
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
                description=f"User: {user.mention}",
                color=discord.Color.green()
            )
            for idx, (q, a) in enumerate(zip(QUESTIONS, answers), 1):
                embed.add_field(name=f"Q{idx}: {q}", value=f"A: {a}", inline=False)
            await channel.send(embed=embed)
        else:
            print(f"Answer post channel ID {ANSWER_POST_CHANNEL_ID} not found.")

    # Optional: Role granting based on answers
    # async def process_roles(self, user, answers):
    #     for guild in self.bot.guilds:
    #         member = guild.get_member(user.id)
    #         if member:
    #             roles_to_add = []
    #             for answer in answers:
    #                 role_id = ROLE_MAPPING.get(answer.lower())
    #                 if role_id:
    #                     role = guild.get_role(role_id)
    #                     if role and role not in member.roles:
    #                         roles_to_add.append(role)
    #             if roles_to_add:
    #                 await member.add_roles(*roles_to_add, reason="Recruitment form answers")
    #                 await user.send(f"Roles granted: {', '.join(role.name for role in roles_to_add)}")
    #             else:
    #                 await user.send("No roles were granted based on your answers.")

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
