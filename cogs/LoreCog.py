import discord
from discord.ext import commands, tasks
from discord import app_commands
import requests
from bs4 import BeautifulSoup
import random
import openai
import os
import logging

DAILY_CHANNEL_ID = 1399102943004721224

# Optional: Configure logging for error visibility
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LoreCog")

class LoreCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        openai.api_key = os.environ.get("OPENAI_API_KEY")
        self.daily_post.start()

    def get_lexicanum_summary(self, topic):
        url = f"https://wh40k.lexicanum.com/wiki/{topic.replace(' ', '_')}"
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code != 200:
                logger.warning(f"Lexicanum request failed ({response.status_code}) for {url}")
                return None, url
            soup = BeautifulSoup(response.content, "html.parser")
            paragraphs = soup.select("div#bodyContent p")
            for p in paragraphs:
                if len(p.text.strip()) > 100:
                    return p.text.strip(), url
            return None, url
        except requests.RequestException as e:
            logger.error(f"Error fetching Lexicanum summary: {e}")
            return None, url
        except Exception as e:
            logger.error(f"Unexpected error in get_lexicanum_summary: {e}")
            return None, url

    def get_random_topic(self):
        try:
            with open("lore_topics.txt", "r", encoding="utf-8") as f:
                topics = [line.strip() for line in f if line.strip()]
            if not topics:
                logger.error("lore_topics.txt is empty.")
                return None
            return random.choice(topics)
        except FileNotFoundError:
            logger.error("lore_topics.txt not found.")
            return None
        except Exception as e:
            logger.error(f"Error reading lore_topics.txt: {e}")
            return None

    @app_commands.command(name="lore", description="Get lore from Warhammer Lexicanum.")
    async def lore(self, interaction: discord.Interaction, topic: str):
        await interaction.response.defer()
        try:
            summary, url = self.get_lexicanum_summary(topic)
            if summary:
                await interaction.followup.send(f"**{topic}**\n{summary}\n<{url}>")
            else:
                await interaction.followup.send(f"No summary found for **{topic}**.\n<{url}>")
        except Exception as e:
            logger.error(f"Error in /lore command: {e}")
            await interaction.followup.send("An error occurred while fetching lore. Please try again later.")

    @app_commands.command(name="asklore", description="Ask a lore question (AI answer)")
    async def asklore(self, interaction: discord.Interaction, question: str):
        await interaction.response.defer()
        prompt = (
            "You are a Warhammer 40K lore expert. Answer the following question as if summarizing canon material:\n\n"
            f"Question: {question}"
        )
        try:
            response = openai.ChatCompletion.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400
            )
            # Defensive: OpenAI API structure could change
            try:
                answer = response.choices[0].message.content.strip()
            except Exception:
                logger.error(f"Unexpected OpenAI API response: {response}")
                answer = "Sorry, I couldn't retrieve an answer from the AI."
            await interaction.followup.send(f"**Q:** {question}\n**A:** {answer}")
        except Exception as e:
            logger.error(f"Error in /asklore command or OpenAI API: {e}")
            await interaction.followup.send("An error occurred while getting an AI answer. Please try again later.")

    @tasks.loop(hours=24)
    async def daily_post(self):
        topic = self.get_random_topic()
        if not topic:
            logger.error("No topic available for daily post.")
            return
        summary, url = self.get_lexicanum_summary(topic)
        channel = self.bot.get_channel(DAILY_CHANNEL_ID)
        if not channel:
            logger.error(f"Channel with ID {DAILY_CHANNEL_ID} not found for daily lore.")
            return
        if not summary:
            logger.warning(f"No summary found for topic '{topic}'. Skipping daily lore post.")
            return
        try:
            await channel.send(f"**Daily Lore: {topic}**\n{summary}\n<{url}>")
        except discord.HTTPException as e:
            logger.error(f"Failed to send daily lore message: {e}")
        except Exception as e:
            logger.error(f"Unexpected error during daily_post: {e}")

    @daily_post.before_loop
    async def before_post(self):
        await self.bot.wait_until_ready()

    # Optional: Global error handler for this cog (for command errors)
    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        logger.error(f"Command error in LoreCog: {error}")
        if isinstance(error, commands.CommandInvokeError):
            await ctx.send("An internal error occurred while processing your command.")
        else:
            await ctx.send(f"Error: {error}")

async def setup(bot):
    await bot.add_cog(LoreCog(bot))
