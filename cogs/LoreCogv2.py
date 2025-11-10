import discord
from discord import app_commands
from discord.ext import commands
from PyPDF2 import PdfReader
from pdf2image import convert_from_path
from datetime import datetime
import os

# === CONFIGURATION ===
PDF_PATH = "/cogs/Warhammer_Lore_Book.pdf"  # update manually
BOOK_TITLE = "Warhammer Lore Compendium"
STATE_DIR = "/cogs"
STATE_FILE = os.path.join(STATE_DIR, "lore_state.txt")
TEMP_IMAGE_PATH = "current_page.jpg"


class LoreCogV2(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        os.makedirs(STATE_DIR, exist_ok=True)  # ensure directory exists
        if not os.path.exists(STATE_FILE):
            with open(STATE_FILE, "w") as f:
                f.write("0")

    # === Helpers ===
    def get_page_state(self):
        try:
            with open(STATE_FILE, "r") as f:
                return int(f.read().strip())
        except (FileNotFoundError, ValueError):
            return 0

    def save_page_state(self, page_num: int):
        # Write instantly to avoid data loss on crash
        with open(STATE_FILE, "w") as f:
            f.write(str(page_num))
        f.flush()
        os.fsync(f.fileno())  # force write to disk

    async def render_page(self, page_num):
        """Convert a PDF page to image and return image path."""
        pages = convert_from_path(PDF_PATH, first_page=page_num, last_page=page_num)
        pages[0].save(TEMP_IMAGE_PATH, "JPEG")
        return TEMP_IMAGE_PATH

    # === Slash Command ===
    @app_commands.command(name="lorecog_manpage", description="Post the next page of the current lore book.")
    async def lorecog_manpage(self, interaction: discord.Interaction):
        """Manually post the next lore page."""
        await interaction.response.defer(thinking=True)

        try:
            reader = PdfReader(PDF_PATH)
            total_pages = len(reader.pages)
            current_page = self.get_page_state() + 1

            if current_page > total_pages:
                await interaction.followup.send(f"✅ **{BOOK_TITLE}** finished! All {total_pages} pages posted.")
                self.save_page_state(0)
                return

            image_path = await self.render_page(current_page)
            file = discord.File(image_path, filename="page.jpg")
            embed = discord.Embed(
                title=f"{BOOK_TITLE} — Page {current_page}/{total_pages}",
                color=discord.Color.dark_red(),
                timestamp=datetime.utcnow()
            )
            embed.set_image(url="attachment://page.jpg")

            await interaction.followup.send(embed=embed, file=file)
            os.remove(image_path)
            self.save_page_state(current_page)
            print(f"📖 Posted page {current_page}/{total_pages}")

        except Exception as e:
            await interaction.followup.send(f"❌ Error: `{e}`")
            print(f"Error in /lorecog_manpage: {e}")


async def setup(bot):
    await bot.add_cog(LoreCogV2(bot))