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

        # Font paths
        cert_font_path = os.path.join(os.path.dirname(__file__), "AlegreyaSC-Bold.ttf")
        person_font_path = os.path.join(os.path.dirname(__file__), "AlegreyaSC-Regular.ttf")
        officer_font_path = os.path.join(os.path.dirname(__file__), "AlegreyaSC-Regular.ttf")

        # Font sizes
        cert_font_size = 76
        person_font_size = 46   # Change as desired
        officer_font_size = 46  # Change as desired

        # Load fonts
        try:
            cert_font = ImageFont.truetype(cert_font_path, size=cert_font_size)
        except:
            cert_font = ImageFont.load_default()
            await interaction.followup.send("⚠️ Certificate title font not found. Using default font.")

        try:
            person_font = ImageFont.truetype(person_font_path, size=person_font_size)
        except:
            person_font = ImageFont.load_default()
            await interaction.followup.send("⚠️ Person font not found. Using default font.")

        try:
            officer_font = ImageFont.truetype(officer_font_path, size=officer_font_size)
        except:
            officer_font = ImageFont.load_default()
            await interaction.followup.send("⚠️ Officer font not found. Using default font.")

        # Spacing
        cert_spacing = 24
        person_spacing = 22
        officer_spacing = 22

        # Center the certificate name about a given pixel (e.g., x=700)
        center_x = 700
        y_cert = 1000

        cert_width = get_spaced_text_width(certificate_name, cert_font, cert_spacing)
        cert_start_x = center_x - (cert_width // 2)
        draw_spaced_text(draw, (cert_start_x, y_cert), certificate_name, cert_font, "black", cert_spacing)

        # The other fields use fixed positions
        draw_spaced_text(draw, (575, 1380), person_name, person_font, "black", person_spacing)
        draw_spaced_text(draw, (420, 1448), officer_name, officer_font, "black", officer_spacing)

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
