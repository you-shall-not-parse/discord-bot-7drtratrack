import discord
from discord.ext import commands, tasks
from discord import app_commands
import random
import openai
import os
import logging

DAILY_CHANNEL_ID = 1399102943004721224

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LoreCog")


class LoreCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.openai_client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        self.daily_post.start()

    def get_random_topic(self):
        try:
            lore_file = os.path.join(os.path.dirname(__file__), "lore_topics.txt")
            with open(lore_file, "r", encoding="utf-8") as f:
                topics = [line.strip() for line in f if line.strip()]
            if not topics:
                logger.error("lore_topics.txt is empty.")
                return None
            return random.choice(topics)
        except FileNotFoundError:
            logger.error("lore_topics.txt not found in cogs directory.")
            return None
        except Exception as e:
            logger.error(f"Error reading lore_topics.txt: {e}")
            return None

    async def ai_lore(self, prompt, model="gpt-3.5-turbo", max_tokens=400):
        try:
            response = self.openai_client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            return "Sorry, I couldn't retrieve an answer from the AI."

    @app_commands.command(name="lore", description="Get AI-generated lore about a Warhammer 40K topic.")
    async def lore(self, interaction: discord.Interaction, topic: str):
        try:
            await interaction.response.defer()
        except Exception as e:
            logger.error(f"Failed to defer interaction in /lore: {e}")
            return
        prompt = (
            f"You are a Warhammer 40K lore expert. Give an in-universe, informative, and canon-style summary of '{topic}'."
            "\nKeep your answer under 300 words."
        )
        answer = await self.ai_lore(prompt)
        await interaction.followup.send(f"**{topic}**\n{answer}")

    @app_commands.command(name="asklore", description="Ask an AI any Warhammer 40K lore question.")
    async def asklore(self, interaction: discord.Interaction, question: str):
        try:
            await interaction.response.defer()
        except Exception as e:
            logger.error(f"Failed to defer interaction in /asklore: {e}")
            return
        prompt = (
            "You are a Warhammer 40K lore expert. Answer the following lore question as accurately as possible, referencing canon where appropriate:\n\n"
            f"Question: {question}\n"
            "Keep your answer under 300 words."
        )
        answer = await self.ai_lore(prompt)
        await interaction.followup.send(f"**Q:** {question}\n**A:** {answer}")

    @tasks.loop(hours=24)
    async def daily_post(self):
        topic = self.get_random_topic()
        if not topic:
            logger.error("No topic available for daily post.")
            return
        prompt = (
            f"You are a Warhammer 40K lore expert. Provide a daily lore summary on the topic: '{topic}'. "
            "The explanation should be in-universe, concise, and accurate, under 300 words."
        )
        summary = await self.ai_lore(prompt)
        channel = self.bot.get_channel(DAILY_CHANNEL_ID)
        if not channel:
            logger.error(f"Channel with ID {DAILY_CHANNEL_ID} not found for daily lore.")
            return
        try:
            await channel.send(f"**Daily Lore: {topic}**\n{summary}")
        except discord.HTTPException as e:
            logger.error(f"Failed to send daily lore message: {e}")
        except Exception as e:
            logger.error(f"Unexpected error during daily_post: {e}")

    @daily_post.before_loop
    async def before_post(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        logger.error(f"Command error in LoreCog: {error}")
        if isinstance(error, commands.CommandInvokeError):
            await ctx.send("An internal error occurred while processing your command.")
        else:
            await ctx.send(f"Error: {error}")

async def setup(bot):
    await bot.add_cog(LoreCog(bot))
