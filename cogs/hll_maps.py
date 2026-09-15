from __future__ import annotations

from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from config import MAIN_GUILD_ID
from data_paths import data_path


MAP_IMAGE_FILES: dict[str, str] = {
    "Carentan": "carentan.webp",
    "Driel": "driel.webp",
    "El Alamein": "elalamein.webp",
    "Elsenborn Ridge": "elsenbornridge.webp",
    "Foy": "foy.webp",
    "Hill 400": "hill400.webp",
    "Hurtgen Forest": "hurtgenforest.webp",
    "Juno Beach": "junobeach.webp",
    "Kharkov": "kharkov.webp",
    "Kursk": "kursk.webp",
    "Mortain": "mortain.webp",
    "Omaha Beach": "omahabeach.webp",
    "Purple Heart Lane (PHL)": "purpleheartlane.webp",
    "Remagen": "remagen.webp",
    "Smolensk": "smolensk.webp",
    "St. Marie Du Mont (SMDM)": "stmariedumont.webp",
    "St. Mere Eglise (SME)": "stmereeglise.webp",
    "Stalingrad": "stalingrad.webp",
    "Tobruk": "tobruk.webp",
    "Utah Beach": "utahbeach.webp",
}

MAP_CHOICES = [
    app_commands.Choice(name=map_name, value=map_name)
    for map_name in sorted(MAP_IMAGE_FILES, key=str.casefold)
]
MAP_IMAGES_DIR = Path(data_path("tac_maps", ensure_dir=False))


class HLLMaps(commands.Cog):
    """Allow any member to post one of the bundled HLL map images."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @staticmethod
    def map_path(map_name: str) -> Path | None:
        filename = MAP_IMAGE_FILES.get(map_name)
        return MAP_IMAGES_DIR / filename if filename else None

    @app_commands.command(name="map", description="Post an image for a Hell Let Loose map")
    @app_commands.guilds(discord.Object(id=MAIN_GUILD_ID))
    @app_commands.guild_only()
    @app_commands.rename(map_name="map")
    @app_commands.describe(map_name="Choose the Hell Let Loose map to post")
    @app_commands.choices(map_name=MAP_CHOICES)
    async def map_command(
        self,
        interaction: discord.Interaction,
        map_name: app_commands.Choice[str],
    ) -> None:
        await interaction.response.defer()

        image_path = self.map_path(map_name.value)
        if image_path is None or not image_path.is_file():
            await interaction.followup.send(
                "That map image is currently unavailable. Please let an admin know.",
                ephemeral=True,
            )
            return

        upload_name = "hll-tactical-map.webp"
        embed = discord.Embed(title=map_name.value, color=discord.Color.from_rgb(97, 107, 75))
        embed.set_image(url=f"attachment://{upload_name}")
        embed.set_footer(text=f"Requested by {interaction.user.display_name}")
        await interaction.followup.send(
            embed=embed,
            file=discord.File(image_path, filename=upload_name),
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HLLMaps(bot))
