import discord
from discord import app_commands
from discord.ext import commands
from PyPDF2 import PdfReader
from pdf2image import convert_from_path
from datetime import datetime
import os

# === CONFIGURATION ===
# Store book, state, and temp image in /data (one folder up from /cogs)
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(BASE_DIR, "data")

# Update these manually when changing books
PDF_PATH = os.path.join(DATA_DIR, "Horus_Rising.pdf")
BOOK_TITLE = "Horus Rising - Dan Abnett"

# Internal files
STATE_FILE = os.path.join(DATA_DIR, "lore_state.txt")
TEMP_IMAGE_PATH = os.path.join(DATA_DIR, "current_page.jpg")


class LoreCogV2(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        os.makedirs(DATA_DIR, exist_ok=True)

        # Ensure state file exists
        if not os.path.exists(STATE_FILE):
            with open(STATE_FILE, "w") as f:
                f.write("0")

        print(f"📘 LoreCogV2 loaded. Using data directory: {DATA_DIR}")

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
        """Convert a PDF page to image and return image path."""
        pages = convert_from_path(PDF_PATH, first_page=page_num, last_page=page_num)
        pages[0].save(TEMP_IMAGE_PATH, "JPEG")
        return TEMP_IMAGE_PATH

    # === Slash Command ===
    @app_commands.command(
        name="lorecog_manpage",
        description="Post the next page of the current lore book."
    )
    async def lorecog_manpage(self, interaction: discord.Interaction):
        """Manually post the next lore page."""
        await interaction.response.defer(thinking=True)

        try:
            reader = PdfReader(PDF_PATH)
            total_pages = len(reader.pages)
            current_page = self.get_page_state() + 1

            if current_page > total_pages:
                await interaction.followup.send(
                    f"✅ **{BOOK_TITLE}** finished! All {total_pages} pages posted."
                )
                self.save_page_state(0)
                return

            # Render and send the page
            image_path = await self.render_page(current_page)
            file = discord.File(image_path, filename="page.jpg")

            embed = discord.Embed(
                title=f"{BOOK_TITLE}",
                description=f"📖 Page {current_page} of {total_pages}",
                color=discord.Color.dark_red(),
                timestamp=datetime.utcnow()
            )
            embed.set_image(url="attachment://page.jpg")
            embed.set_footer(text=f"Requested by {interaction.user.display_name}")

            await interaction.followup.send(embed=embed, file=file)

            # Clean up + persist state
            os.remove(image_path)
            self.save_page_state(current_page)
            print(f"📖 Posted page {current_page}/{total_pages} from {BOOK_TITLE}")

        except Exception as e:
            await interaction.followup.send(f"❌ Error: `{e}`")
            print(f"Error in /lorecog_manpage: {e}")


# === Setup Function ===
async def setup(bot):
    await bot.add_cog(LoreCogV2(bot))
