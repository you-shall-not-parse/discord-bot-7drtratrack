import discord
from discord import app_commands
from discord.ext import commands, tasks
from PyPDF2 import PdfReader
from pdf2image import convert_from_path
from datetime import datetime, timedelta
import os
import asyncio

# === CONFIGURATION ===
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(BASE_DIR, "data")

PDF_PATH = os.path.join(DATA_DIR, "Horus_Rising.pdf")
BOOK_TITLE = "Horus Rising"

STATE_FILE = os.path.join(DATA_DIR, "lore_state.txt")
TEMP_IMAGE_PATH = os.path.join(DATA_DIR, "current_page.jpg")
POPPLER_PATH = "/usr/bin"  # Poppler binaries path

# Fixed thread/channel for daily posting
POST_CHANNEL_ID = 1399102943004721224
DAILY_HOUR = 9  # 09:00 GMT


class LoreCogV2(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        os.makedirs(DATA_DIR, exist_ok=True)

        if not os.path.exists(STATE_FILE):
            with open(STATE_FILE, "w") as f:
                f.write("0")

        print(f"📘 LoreCogV2 loaded. Using data directory: {DATA_DIR}")
        self.daily_task.start()  # start the background daily posting loop

    # === Helpers ===
    def get_page_state(self):
        try:
            with open(STATE_FILE, "r") as f:
                return int(f.read().strip())
        except (FileNotFoundError, ValueError):
            return 0

    def save_page_state(self, page_num: int):
        with open(STATE_FILE, "w") as f:
            f.write(str(page_num))
            f.flush()
            os.fsync(f.fileno())

    async def render_page(self, page_num):
        pages = convert_from_path(
            PDF_PATH, first_page=page_num, last_page=page_num, poppler_path=POPPLER_PATH
        )
        pages[0].save(TEMP_IMAGE_PATH, "JPEG")
        return TEMP_IMAGE_PATH

    async def post_page(self, channel: discord.abc.Messageable):
        """Post the next page into the given channel/thread."""
        try:
            reader = PdfReader(PDF_PATH)
            total_pages = len(reader.pages)
            current_page = self.get_page_state() + 1

            if current_page > total_pages:
                await channel.send(f"✅ **{BOOK_TITLE}** finished! All {total_pages} pages posted.")
                self.save_page_state(0)
                return

            image_path = await self.render_page(current_page)
            file = discord.File(image_path, filename="page.jpg")

            embed = discord.Embed(
                title=f"{BOOK_TITLE}",
                description=f"📖 Page {current_page} of {total_pages}",
                color=discord.Color.dark_red(),
                timestamp=datetime.utcnow()
            )
            embed.set_image(url="attachment://page.jpg")
            embed.set_footer(text=f"Automatic daily post")

            await channel.send(embed=embed, file=file)

            os.remove(image_path)
            self.save_page_state(current_page)
            print(f"📖 Posted page {current_page}/{total_pages} from {BOOK_TITLE}")

        except Exception as e:
            print(f"❌ Error posting page: {e}")

    # === Slash Commands ===
    @app_commands.command(
        name="lorecog_manpage",
        description="Post the next page of the current lore book."
    )
    async def lorecog_manpage(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        await self.post_page(interaction.channel)

    @app_commands.command(
        name="lorecog_reset",
        description="Reset the lore book page back to the first page."
    )
    async def lorecog_reset(self, interaction: discord.Interaction):
        self.save_page_state(0)
        await interaction.response.send_message("✅ Lore book page has been reset to 1.")

    # === Background Daily Posting Task ===
    @tasks.loop(hours=24)
    async def daily_task(self):
        # Wait until bot is ready
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            now = datetime.utcnow()
            target = now.replace(hour=DAILY_HOUR, minute=0, second=0, microsecond=0)
            if target < now:
                target += timedelta(days=1)
            wait_seconds = (target - now).total_seconds()
            await asyncio.sleep(wait_seconds)

            channel = self.bot.get_channel(POST_CHANNEL_ID)
            if channel:
                await self.post_page(channel)
            else:
                print(f"❌ Could not find channel with ID {POST_CHANNEL_ID}")

            # Sleep until next day (24h)
            await asyncio.sleep(1)  # short pause to restart loop cleanly

    @daily_task.before_loop
    async def before_daily_task(self):
        await self.bot.wait_until_ready()


# === Setup Function ===
async def setup(bot):
    await bot.add_cog(LoreCogV2(bot))
