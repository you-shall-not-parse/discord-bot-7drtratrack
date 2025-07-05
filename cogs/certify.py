import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import os

def draw_spaced_text(draw, position, text, font, fill, spacing):
    x, y = position
    for char in text:
        draw.text((x, y), char, font=font, fill=fill)
        bbox = font.getbbox(char)
        char_width = bbox[2] - bbox[0]
        x += char_width + spacing

def get_spaced_text_width(text, font, spacing):
    width = 0
    for i, char in enumerate(text):
        bbox = font.getbbox(char)
        char_width = bbox[2] - bbox[0]
        width += char_width
        if i < len(text) - 1:
            width += spacing
    return width

class Certify(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.template_path = self._find_template()

    def _find_template(self):
        folder = os.path.dirname(__file__)
        for ext in ['.jpg', '.jpeg', '.png']:
            path = os.path.join(folder, f'certificate_template{ext}')
            if os.path.exists(path):
                return path
        raise FileNotFoundError("❌ No certificate template found in Cog folder.")

    @app_commands.command(name="certify", description="Generate a certificate")
    @app_commands.describe(
        person_name="Name of the person",
        certificate_name="Certificate title",
        officer_name="Officer's name"
    )
    async def certify(self, interaction: discord.Interaction,
                      person_name: str,
                      certificate_name: str,
                      officer_name: str):

        # --- Role check block ---
        allowed_roles = {"Assistant"}  # <-- Set your allowed role names here
        member = interaction.user if hasattr(interaction, "user") else interaction.author
        # 'member' might be a User if invoked in a DM, which doesn't have roles
        if not hasattr(member, "roles") or not any(role.name in allowed_roles for role in member.roles):
            await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
            return
        # --- End role check ---

        await interaction.response.defer()

        # Open the image template
        try:
            img = Image.open(self.template_path).convert("RGBA")
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to open image template: {e}")
            return

        draw = ImageDraw.Draw(img)

        # Load font (ensure this font file is present in the same directory or specify a path)
        font_path = os.path.join(os.path.dirname(__file__), "AlegreyaSC-Bold.ttf")
        try:
            font = ImageFont.truetype(font_path, size=72)
        except:
            font = ImageFont.load_default()
            await interaction.followup.send("⚠️ Custom font not found. Using default font.")

        spacing = 5

        # Center the certificate name about a given pixel (e.g., x=700)
        center_x = 700
        y_cert = 905

        cert_width = get_spaced_text_width(certificate_name, font, spacing)
        cert_start_x = center_x - (cert_width // 2)
        draw_spaced_text(draw, (cert_start_x, y_cert), certificate_name, font, "black", spacing)

        # The other fields use fixed positions
        draw_spaced_text(draw, (575, 1265), person_name, font, "black", spacing)
        draw_spaced_text(draw, (420, 1320), officer_name, font, "black", spacing)

        # Save to buffer
        output_buffer = BytesIO()
        img.save(output_buffer, format="PNG")
        output_buffer.seek(0)

        await interaction.followup.send(
            "🎖️ Certificate generated!",
            file=discord.File(fp=output_buffer, filename="certificate.png")
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(Certify(bot))
