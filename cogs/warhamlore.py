import discord
from discord.ext import commands, tasks
from discord import app_commands
import requests
from bs4 import BeautifulSoup
import random
import openai
import config

DAILY_CHANNEL_ID = 1399102943004721224  # Only used from here now!

class LoreCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        openai.api_key = config.OPENAI_API_KEY
        self.daily_post.start()

    def get_lexicanum_summary(self, topic):
        url = f"https://wh40k.lexicanum.com/wiki/{topic.replace(' ', '_')}"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            return None, url

        soup = BeautifulSoup(response.content, "html.parser")
        paragraphs = soup.select("div#bodyContent p")
        for p in paragraphs:
            if len(p.text.strip()) > 100:
                return p.text.strip(), url
        return None, url

    def get_random_topic(self):
        with open("lore_topics.txt", "r", encoding="utf-8") as f:
            return random.choice(f.read().splitlines())

    @app_commands.command(name="lore", description="Get lore from Warhammer Lexicanum.")
    async def lore(self, interaction: discord.Interaction, topic: str):
        await interaction.response.defer()
        summary, url = self.get_lexicanum_summary(topic)
        if summary:
            await interaction.followup.send(f"**{topic}**\n{summary}\n<{url}>")
        else:
            await interaction.followup.send(f"No summary found for **{topic}**.\n<{url}>")

    @app_commands.command(name="asklore", description="Ask a lore question (AI answer)")
    async def asklore(self, interaction: discord.Interaction, question: str):
        await interaction.response.defer()
        prompt = (
            "You are a Warhammer 40K lore expert. Answer the following question as if summarizing canon material:\n\n"
            f"Question: {question}"
        )
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400
        )
        answer = response.choices[0].message.content.strip()
        await interaction.followup.send(f"**Q:** {question}\n**A:** {answer}")

    @tasks.loop(hours=24)
    async def daily_post(self):
        topic = self.get_random_topic()
        summary, url = self.get_lexicanum_summary(topic)
        channel = self.bot.get_channel(DAILY_CHANNEL_ID)  # Use the global, not config
        if channel and summary:
            await channel.send(f"**Daily Lore: {topic}**\n{summary}\n<{url}>")

    @daily_post.before_loop
    async def before_post(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(LoreCog(bot))
