import discord
from discord.ext import commands

# === CONFIGURATION ===
FORM_CHANNEL_ID = 1099806153170489485   # Channel where the form embed/button is posted
ANSWER_POST_CHANNEL_ID = 1099806153170489485  # Channel where form responses are posted
# ROLE_MAPPING = {
#    "answer_text": role_id
#    "yes": 111111111111111111,
#    "python": 222222222222222222,
# }

QUESTIONS = [
    "Do you agree to the server rules? (yes/no)",
    "What is your favorite programming language?",
]

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
            title="Recruitment Form",
            description="Click the button below to start your application.",
            color=discord.Color.blue()
        )
        view = RecruitButtonView(self)
        msg = await channel.send(embed=embed, view=view)
        self.embed_message_id = msg.id

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

    # Method to DM the form
    async def start_form(self, user: discord.User):
        try:
            dm = await user.create_dm()
            answers = []
            for question in QUESTIONS:
                await dm.send(question)
                def check(m):
                    return m.author == user and m.channel == dm
                msg = await self.cog.bot.wait_for('message', check=check, timeout=120)
                answers.append(msg.content.strip())
            await dm.send("Thank you! Processing your answers...")
            await self.post_answers(user, answers)
            await self.process_roles(user, answers)
        except Exception as e:
            print(f"Error in DM form: {e}")

    # Post answers in the defined channel
    async def post_answers(self, user, answers):
        channel = self.cog.bot.get_channel(ANSWER_POST_CHANNEL_ID)
        if channel:
            embed = discord.Embed(
                title="New Application Submission",
                description=f"User: {user.mention}",
                color=discord.Color.green()
            )
            for idx, (q, a) in enumerate(zip(QUESTIONS, answers), 1):
                embed.add_field(name=f"Q{idx}: {q}", value=f"A: {a}", inline=False)
            await channel.send(embed=embed)
        else:
            print(f"Answer post channel ID {ANSWER_POST_CHANNEL_ID} not found.")

# Role granting
#    async def process_roles(self, user, answers):
#        for guild in self.cog.bot.guilds:
#            member = guild.get_member(user.id)
#            if member:
#                roles_to_add = []
#                for answer in answers:
#                    role_id = ROLE_MAPPING.get(answer.lower())
#                    if role_id:
#                        role = guild.get_role(role_id)
#                        if role and role not in member.roles:
#                            roles_to_add.append(role)
#                if roles_to_add:
#                    await member.add_roles(*roles_to_add, reason="Recruitment form answers")
#                    await user.send(f"Roles granted: {', '.join(role.name for role in roles_to_add)}")
#                else:
#                    await user.send("No roles were granted based on your answers.")

def setup(bot):
    bot.add_cog(RecruitFormCog(bot))
