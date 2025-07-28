import discord
from discord.ext import commands, tasks
import sqlite3
import random
import datetime
import requests
from bs4 import BeautifulSoup

def init_db():
    conn = sqlite3.connect("quotes.db")
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quote TEXT NOT NULL,
            author TEXT
        )
    ''')
    conn.commit()
    conn.close()

def add_quote(quote, author=None):
    conn = sqlite3.connect("quotes.db")
    c = conn.cursor()
    c.execute('INSERT INTO quotes (quote, author) VALUES (?, ?)', (quote, author))
    conn.commit()
    conn.close()

def get_random_quote():
    conn = sqlite3.connect("quotes.db")
    c = conn.cursor()
    c.execute('SELECT quote, author FROM quotes ORDER BY RANDOM() LIMIT 1')
    row = c.fetchone()
    conn.close()
    if row:
        quote, author = row
        return f'"{quote}"\n— {author}' if author else f'"{quote}"'
    return "No quotes found."

def get_lexicanum_lore(topic):
    url = f"https://wh40k.lexicanum.com/wiki/{topic.replace(' ', '_')}"
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        return f"Could not fetch lore for {topic}.", url
    soup = BeautifulSoup(response.content, "html.parser")
    content_div = soup.find("div", id="bodyContent")
    summary = None
    if content_div:
        for elem in content_div.find_all(["p", "h2"], recursive=False):
            if elem.name == "h2":
                break
            if elem.name == "p":
                text = elem.get_text(strip=True)
                if len(text) > 100:
                    summary = text
                    break
    return (summary if summary else "No suitable lore found."), url

class LoreCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        init_db()
        self.daily_quote_channel_id = 1399102943004721224 # <-- Replace with your channel ID (as integer)
        self.daily_quote_task.start()

    def cog_unload(self):
        self.daily_quote_task.cancel()

    @tasks.loop(time=datetime.time(hour=9, minute=0))  # Posts at 09:00 UTC
    async def daily_quote_task(self):
        await self.bot.wait_until_ready()
        channel = self.bot.get_channel(self.daily_quote_channel_id)
        if channel is not None:
            quote = get_random_quote()
            await channel.send(f"**Daily Lore Quote:**\n{quote}")

    @discord.app_commands.command(name="addquote", description="Add your own lore quote!")
    @discord.app_commands.describe(quote="The quote to add", author="(Optional) Who said it?")
    async def addquote(self, interaction: discord.Interaction, quote: str, author: str = None):
        add_quote(quote, author)
        await interaction.response.send_message("Your quote has been added!", ephemeral=True)

    @discord.app_commands.command(name="lore", description="Get a random lore quote from the database.")
    async def lore(self, interaction: discord.Interaction):
        quote = get_random_quote()
        await interaction.response.send_message(f"**Lore Quote:**\n{quote}")

    @discord.app_commands.command(name="lexlore", description="Get summary lore from Lexicanum.")
    @discord.app_commands.describe(topic="The Lexicanum topic (e.g., Space Marines)")
    async def lexlore(self, interaction: discord.Interaction, topic: str):
        await interaction.response.defer()
        summary, url = get_lexicanum_lore(topic)
        await interaction.followup.send(f"**Lexicanum Lore for '{topic}':**\n{summary}\n<{url}>")

async def setup(bot):
    await bot.add_cog(LoreCog(bot))
