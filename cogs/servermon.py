import discord
from discord.ext import commands, tasks
from discord import app_commands
from rcon.source import Client
import sqlite3
import re
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "kd_stats.db")

class RconTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.rcon_host = "YOUR.SERVER.IP"
        self.rcon_port = 12345
        self.rcon_password = "your_password"
        self.last_seen_lines = set()
        self.db = sqlite3.connect(DB_PATH)
        self.create_table()
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

    def update_kd(self, killer: str, victim: str):
        killer = killer.strip()
        victim = victim.strip()

        with self.db:
            # Update killer
            self.db.execute("""
                INSERT INTO player_stats (name, kills, deaths)
                VALUES (?, 1, 0)
                ON CONFLICT(name) DO UPDATE SET kills = kills + 1;
            """, (killer,))
            # Update victim
            self.db.execute("""
                INSERT INTO player_stats (name, kills, deaths)
                VALUES (?, 0, 1)
                ON CONFLICT(name) DO UPDATE SET deaths = deaths + 1;
            """, (victim,))

    @tasks.loop(seconds=5)
    async def rcon_task(self):
        try:
            with Client(self.rcon_host, self.rcon_port, passwd=self.rcon_password) as client:
                logs = client.run("GetLogLines 100")
                for line in logs.split("\n"):
                    if "KILL:" in line and line not in self.last_seen_lines:
                        match = re.match(r"KILL: (.+?) \(.*?\) killed (.+?) \(.*?\)", line)
                        if match:
                            killer, victim = match.groups()
                            self.update_kd(killer, victim)
                            self.last_seen_lines.add(line)
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

    async def cog_load(self):
        self.bot.tree.add_command(self.kd)

    def cog_unload(self):
        self.db.close()

async def setup(bot):
    await bot.add_cog(RconTracker(bot))
