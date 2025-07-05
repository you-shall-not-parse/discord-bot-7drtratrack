import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import os

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
        unit="Unit name",
        officer_name="Officer's name"
    )
    async def certify(self, interaction: discord.Interaction,
                      person_name: str,
                      certificate_name: str,
                      unit: str,
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
        font_path = os.path.join(os.path.dirname(__file__), "EBGaramond-VariableFont_wght.ttf")
        try:
            font = ImageFont.truetype(font_path, size=40)
        except:
            font = ImageFont.load_default()
            await interaction.followup.send("⚠️ Custom font not found. Using default font.")

        # Adjust positions to match your design
        draw.text((200, 150), certificate_name, font=font, fill="black")
        draw.text((200, 230), f"Awarded to: {person_name}", font=font, fill="black")
        draw.text((200, 310), f"Unit: {unit}", font=font, fill="black")
        draw.text((200, 390), f"Issued by: {officer_name}", font=font, fill="black")

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
