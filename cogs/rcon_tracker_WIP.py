import discord
from discord.ext import commands, tasks
from discord import app_commands
from rcon.source import Client
from dotenv import load_dotenv
import sqlite3
import re
import os
import asyncio

load_dotenv()

class RconTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        self.rcon_host = os.getenv("RCON_HOST")
        self.rcon_port = int(os.getenv("RCON_PORT"))
        self.rcon_password = os.getenv("RCON_PASSWORD")

        self.db_path = os.path.join(os.path.dirname(__file__), "kd_stats.db")
        self.last_line_path = os.path.join(os.path.dirname(__file__), "last_line.txt")

        self.db = sqlite3.connect(self.db_path)
        self.create_table()
        self.last_seen_line = self.load_last_line()

        self.rcon_task.start()

    
    def create_table(self):
        with self.db:
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS player_stats (
                    name TEXT PRIMARY KEY,
                    kills INTEGER DEFAULT 0,
                    deaths INTEGER DEFAULT 0
                );
            """)

    def load_last_line(self):
        if os.path.exists(self.last_line_path):
            with open(self.last_line_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        return None

    def save_last_line(self, line: str):
        with open(self.last_line_path, "w", encoding="utf-8") as f:
            f.write(line)

    def update_kd(self, killer: str, victim: str):
        killer = killer.strip()
        victim = victim.strip()

        with self.db:
            self.db.execute("""
                INSERT INTO player_stats (name, kills, deaths)
                VALUES (?, 1, 0)
                ON CONFLICT(name) DO UPDATE SET kills = kills + 1;
            """, (killer,))
            self.db.execute("""
                INSERT INTO player_stats (name, kills, deaths)
                VALUES (?, 0, 1)
                ON CONFLICT(name) DO UPDATE SET deaths = deaths + 1;
            """, (victim,))

    @tasks.loop(seconds=5)
    async def rcon_task(self):
        try:
            with Client(self.rcon_host, self.rcon_port, passwd=self.rcon_password) as client:
                logs = client.run("status")
                lines = logs.strip().split("\n")

                # Determine new lines to process
                if self.last_seen_line in lines:
                    new_lines = lines[lines.index(self.last_seen_line)+1:]
                else:
                    new_lines = lines  # first time or old line expired

                for line in new_lines:
                    if "KILL:" in line:
                        match = re.match(r"KILL: (.+?) \(.*?\) killed (.+?) \(.*?\)", line)
                        if match:
                            killer, victim = match.groups()
                            self.update_kd(killer, victim)

                if lines:
                    self.last_seen_line = lines[-1]
                    self.save_last_line(self.last_seen_line)

        except Exception as e:
            print(f"[RCON ERROR] {e}")

    @app_commands.command(name="kd", description="Get lifetime kill/death stats for a player")
    @app_commands.describe(player_name="The exact name of the player to check K/D for")
    async def kd(self, interaction: discord.Interaction, player_name: str):
        name = player_name.strip()

        cursor = self.db.cursor()
        cursor.execute("SELECT kills, deaths FROM player_stats WHERE name = ?", (name,))
        row = cursor.fetchone()

        if not row:
            await interaction.response.send_message(f"No stats found for `{name}`.")
            return

        kills, deaths = row
        ratio = kills / deaths if deaths > 0 else kills
        await interaction.response.send_message(
            f"📊 `{name}` — {kills} K / {deaths} D — K/D: `{ratio:.2f}`"
        )

    def cog_unload(self):
        self.db.close()

async def setup(bot):
    await bot.add_cog(RconTracker(bot))
