from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import threading
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from config import MAIN_GUILD_ID
from data_paths import data_path
from state_io import atomic_json_dump
from cogs.nameshame import AdminManageView, DetailsSelect

LOGGER = logging.getLogger(__name__)
CHANNEL_ID = 1549529105874165911
INDEX_MESSAGE_ID = 1550858825077358643
WEBSITE_URL = "https://hllfrontline.com/login?next=/"
GUIDE_PATH = Path(data_path("7DR NCO and Admin Guide (1).pdf"))
STATE_PATH = data_path("officer_info_state.json")
PDF_LOCK = threading.Lock()


def render_page(index: int) -> tuple[bytes, int]:
    # PDFium is not thread safe; serialize work even across independent readers.
    import pypdfium2 as pdfium

    with PDF_LOCK, pdfium.PdfDocument(GUIDE_PATH) as document:
        count = len(document)
        if not 0 <= index < count:
            raise ValueError("Page is outside the guide")
        page = document[index]
        try:
            bitmap = page.render(scale=2)
            try:
                with bitmap.to_pil() as image:
                    output = io.BytesIO()
                    image.save(output, format="PNG")
                    return output.getvalue(), count
            finally:
                bitmap.close()
        finally:
            page.close()


class GuideReader(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=600)
        self.page = 0
        self.lock = asyncio.Lock()

    async def display(self, interaction: discord.Interaction, delta: int = 0, *, first: bool = False) -> None:
        await interaction.response.defer(ephemeral=True, thinking=first)
        async with self.lock:
            target = max(0, self.page + delta)
            try:
                png, count = await asyncio.to_thread(render_page, target)
            except Exception:
                LOGGER.exception("Could not render officer guide")
                await interaction.followup.send("The guide could not be opened. Please ask an administrator to check the PDF and renderer.", ephemeral=True)
                return
            self.previous.disabled = target == 0
            self.next.disabled = target == count - 1
            embed = discord.Embed(title="7DR NCO and Admin Guide", color=discord.Color.dark_green())
            embed.set_image(url="attachment://guide-page.png")
            embed.set_footer(text=f"Page {target + 1} of {count} • Reader closes after 10 minutes of inactivity; reopen from the panel.")
            file = discord.File(io.BytesIO(png), filename="guide-page.png")
            try:
                if first:
                    await interaction.followup.send(embed=embed, file=file, view=self, ephemeral=True)
                else:
                    await interaction.edit_original_response(embed=embed, attachments=[file], view=self)
                self.page = target
            finally:
                file.close()

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.display(interaction, -1)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.display(interaction, 1)


class OfficerPanel(discord.ui.View):
    def __init__(self, reporting=None) -> None:
        super().__init__(timeout=None)
        if reporting is not None:
            self.add_item(DetailsSelect(reporting))

    @discord.ui.button(label="Browse NCO & Admin Guide", custom_id="officer-info:guide:v1", style=discord.ButtonStyle.primary)
    async def guide(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await GuideReader().display(interaction, first=True)

    @discord.ui.button(label="Report Player", custom_id="officer-info:report:v1", style=discord.ButtonStyle.danger)
    async def report(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = interaction.client.get_cog("NameShame")
        if cog is None:
            await interaction.response.send_message("Player reporting is currently unavailable. Please contact an administrator.", ephemeral=True)
            return
        await cog.open_report(interaction)

    @discord.ui.button(label="Admin Reports", custom_id="officer-info:admin:v1", style=discord.ButtonStyle.primary)
    async def admin(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = interaction.client.get_cog("NameShame")
        if cog is None:
            await interaction.response.send_message("Player reporting is currently unavailable.", ephemeral=True)
            return
        if not await cog.is_admin_reports(interaction):
            await interaction.response.send_message("You cannot use Admin Reports.", ephemeral=True)
            return
        view = AdminManageView(cog)
        await interaction.response.send_message(content=view._content(), ephemeral=True, view=view)


class OfficerInfo(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.view = OfficerPanel()
        self.lock = asyncio.Lock()

    async def cog_load(self) -> None:
        self.bot.add_view(self.view)

    def cog_unload(self) -> None:
        self.view.stop()

    async def publish(self) -> discord.Message:
        async with self.lock:
            pin = os.getenv("OFFICER_WEBSITE_PIN", "").strip()
            if not pin:
                raise ValueError("Set OFFICER_WEBSITE_PIN in the bot environment before posting the panel.")
            if not GUIDE_PATH.is_file():
                raise ValueError(f"Officer guide is missing: {GUIDE_PATH.name}")
            channel = self.bot.get_channel(CHANNEL_ID) or await self.bot.fetch_channel(CHANNEL_ID)
            if not isinstance(channel, discord.TextChannel) or channel.guild.id != MAIN_GUILD_ID:
                raise ValueError("The officer panel channel must be a text channel in the main guild.")
            embed = discord.Embed(title="7DR Officer Information", description="Officer resources and reference guide.", color=discord.Color.dark_green())
            embed.add_field(name="HLL Frontline", value=f"[Open the website]({WEBSITE_URL})\n**PIN:** `{pin}`", inline=False)
            embed.add_field(name="T17 Member Index", value=f"[Open the T17 member index message](https://discord.com/channels/{MAIN_GUILD_ID}/{CHANNEL_ID}/{INDEX_MESSAGE_ID})", inline=False)
            embed.add_field(name="NCO & Admin Guide", value="Browse the PDF pages privately using the guide button below.", inline=False)
            reporting = self.bot.get_cog("NameShame")
            if reporting is None:
                raise ValueError("The NameShame reporting backend must be loaded before publishing the combined panel.")
            reports_embed = reporting.build_main_embed(channel.guild)
            if reports_embed.description:
                embed.add_field(name="Player Reporting", value=reports_embed.description, inline=False)
            for field in reports_embed.fields:
                embed.add_field(name=field.name, value=field.value, inline=field.inline)
            embed.timestamp = reports_embed.timestamp
            view = OfficerPanel(reporting)
            try:
                state = json.loads(Path(STATE_PATH).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                state = {}
            message = None
            if isinstance(state, dict) and state.get("channel_id") == CHANNEL_ID and isinstance(state.get("message_id"), int):
                try:
                    message = await channel.fetch_message(state["message_id"])
                except discord.NotFound:
                    pass
            self.view.stop()
            if message is None:
                message = await channel.send(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
            else:
                await message.edit(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
            self.view = view
            atomic_json_dump(STATE_PATH, {"channel_id": CHANNEL_ID, "message_id": message.id})
            try:
                await reporting.retire_legacy_panel()
            except discord.HTTPException:
                LOGGER.exception("Combined panel is live, but the old reporting message could not be removed")
            return message

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        try:
            await self.publish()
        except Exception:
            LOGGER.exception("Could not publish officer information panel")

    @app_commands.command(name="officer-info", description="Create or refresh the officer information panel")
    @app_commands.guilds(discord.Object(id=MAIN_GUILD_ID))
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def officer_info(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            message = await self.publish()
        except (ValueError, discord.HTTPException) as exc:
            await interaction.followup.send(f"Could not post the panel: {exc}", ephemeral=True)
            return
        await interaction.followup.send(f"Officer information panel: {message.jump_url}", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OfficerInfo(bot))
