import asyncio
import io
import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from PIL import Image, ImageDraw, ImageFont, ImageOps
from discord.ext import commands

from config.common import CERTIFICATE_BOLD_FONT_PATH, CERTIFICATE_REGULAR_FONT_PATH, MAIN_GUILD_ID
from data_paths import data_path

# ================== CONFIG ==================

logger = logging.getLogger(__name__)

LEAVE_CHANNEL_ID = 1097913605539774484  # 👈 replace with your channel ID
ENTREE_CHANNEL_ID = 1099806153170489485
WELCOME_STATE_PATH = Path(data_path("quick_exit_welcome_state.json"))
MAP_IMAGES_DIR = Path(data_path("map_images"))
WELCOME_IMAGE_SIZE = (1200, 675)
TARGET_GUILD = discord.Object(id=MAIN_GUILD_ID)

LEAVE_MESSAGE = "**{display} ({name})** has just left the server, fuck em"

MAP_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".avif"}

# ================== COG ==================

class QuickExit(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._welcome_tasks: dict[int, asyncio.Task] = {}
        self._state_lock = asyncio.Lock()
        self._feature_started_at = datetime.now(timezone.utc)
        self._welcomed_member_ids: set[int] = set()
        self._pending_member_ids: set[int] = set()
        self._backfill_complete = False
        self._load_state()

    def cog_unload(self) -> None:
        for task in self._welcome_tasks.values():
            if not task.done():
                task.cancel()

    def _load_state(self) -> None:
        default_state = {
            "feature_started_at": self._feature_started_at.isoformat(),
            "welcomed_member_ids": [],
            "pending_member_ids": [],
        }

        if not WELCOME_STATE_PATH.exists():
            WELCOME_STATE_PATH.write_text(json.dumps(default_state, indent=2), encoding="utf-8")
            return

        try:
            state = json.loads(WELCOME_STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("Failed to load quick-exit welcome state; using defaults.", exc_info=True)
            return

        started_at_raw = state.get("feature_started_at")
        if isinstance(started_at_raw, str):
            try:
                parsed = datetime.fromisoformat(started_at_raw)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                self._feature_started_at = parsed.astimezone(timezone.utc)
            except ValueError:
                logger.warning("Invalid quick-exit feature_started_at: %s", started_at_raw)

        self._welcomed_member_ids = {
            int(member_id)
            for member_id in state.get("welcomed_member_ids", [])
            if str(member_id).isdigit()
        }
        self._pending_member_ids = {
            int(member_id)
            for member_id in state.get("pending_member_ids", [])
            if str(member_id).isdigit()
        }

    def _write_state_locked(self) -> None:
        state = {
            "feature_started_at": self._feature_started_at.isoformat(),
            "welcomed_member_ids": sorted(self._welcomed_member_ids),
            "pending_member_ids": sorted(self._pending_member_ids),
        }
        WELCOME_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")

    async def _queue_welcome(self, member_id: int) -> None:
        async with self._state_lock:
            if member_id in self._welcomed_member_ids:
                return
            self._pending_member_ids.add(member_id)
            self._write_state_locked()

    async def _mark_welcomed(self, member_id: int) -> None:
        async with self._state_lock:
            self._pending_member_ids.discard(member_id)
            self._welcomed_member_ids.add(member_id)
            self._write_state_locked()

    async def _forget_member(self, member_id: int) -> None:
        async with self._state_lock:
            self._pending_member_ids.discard(member_id)
            self._welcomed_member_ids.discard(member_id)
            self._write_state_locked()

    def _cancel_welcome_task(self, member_id: int) -> None:
        task = self._welcome_tasks.pop(member_id, None)
        if task and not task.done():
            task.cancel()

    def _schedule_welcome(self, member_id: int) -> None:
        self._cancel_welcome_task(member_id)
        self._welcome_tasks[member_id] = asyncio.create_task(self._deliver_welcome(member_id))

    async def _fetch_bytes(self, url: str) -> bytes:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                return await response.read()

    def _load_font(self, path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            return ImageFont.load_default()

    def _fit_text(self, draw: ImageDraw.ImageDraw, text: str, font_path: str, max_width: int, start_size: int, min_size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        for size in range(start_size, min_size - 1, -2):
            font = self._load_font(font_path, size)
            bbox = draw.textbbox((0, 0), text, font=font)
            if bbox[2] - bbox[0] <= max_width:
                return font
        return self._load_font(font_path, min_size)

    async def _resolve_member(self, member_id: int) -> Optional[discord.Member]:
        guild = self.bot.get_guild(MAIN_GUILD_ID)
        if guild is None:
            return None

        member = guild.get_member(member_id)
        if member is not None:
            return member

        try:
            return await guild.fetch_member(member_id)
        except discord.NotFound:
            return None
        except discord.HTTPException:
            logger.warning("Failed to fetch member %s for quick-exit welcome flow", member_id, exc_info=True)
            return None

    def _compose_welcome_copy(self, member: discord.Member) -> tuple[str, str]:
        return (f"Hey {member.mention}, **welcome to 7DR!**", "just joined the server")

    def _build_fallback_background(self) -> Image.Image:
        background = Image.new("RGBA", WELCOME_IMAGE_SIZE, (8, 12, 20, 255))
        gradient = Image.new("RGBA", WELCOME_IMAGE_SIZE, (0, 0, 0, 0))
        gradient_draw = ImageDraw.Draw(gradient)
        for index in range(WELCOME_IMAGE_SIZE[1]):
            alpha = int(190 * (index / WELCOME_IMAGE_SIZE[1]))
            gradient_draw.line((0, index, WELCOME_IMAGE_SIZE[0], index), fill=(0, 0, 0, alpha))
        background.alpha_composite(gradient)
        return background

    def _render_avatar(self, avatar_bytes: bytes, diameter: int) -> Image.Image:
        with Image.open(io.BytesIO(avatar_bytes)).convert("RGBA") as avatar_src:
            avatar = ImageOps.fit(avatar_src, (diameter, diameter), Image.Resampling.LANCZOS)
        mask = Image.new("L", (diameter, diameter), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, diameter - 1, diameter - 1), fill=255)
        avatar.putalpha(mask)
        return avatar

    async def _load_background_image(self) -> tuple[Image.Image, str]:
        map_paths = [
            path for path in MAP_IMAGES_DIR.iterdir()
            if path.is_file() and path.suffix.lower() in MAP_IMAGE_SUFFIXES
        ]
        random.shuffle(map_paths)

        for image_path in map_paths:
            try:
                with Image.open(image_path).convert("RGBA") as background_src:
                    background = ImageOps.fit(background_src, WELCOME_IMAGE_SIZE, Image.Resampling.LANCZOS)
                return background, image_path.stem
            except Exception:
                logger.warning("Failed to load welcome background image %s", image_path, exc_info=True)

        logger.warning("No usable welcome background images found in %s; using fallback background.", MAP_IMAGES_DIR)
        return self._build_fallback_background(), "7DR Welcome"

    async def _build_welcome_image(self, member: discord.Member, display_name: str, detail_line: str) -> discord.File:
        avatar_url = member.display_avatar.replace(format="png", size=256).url

        try:
            avatar_bytes = await self._fetch_bytes(avatar_url)
        except Exception:
            logger.warning("Failed to fetch quick-exit welcome avatar for %s (%s)", member, member.id, exc_info=True)
            raise

        background, map_name = await self._load_background_image()

        overlay = Image.new("RGBA", WELCOME_IMAGE_SIZE, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.rounded_rectangle((0, 46, 1200, 628), radius=30, fill=(5, 8, 14, 168))
        overlay_draw.rectangle((0, 530, 1200, 628), fill=(3, 5, 10, 210))
        background.alpha_composite(overlay)

        draw = ImageDraw.Draw(background)
        title_font = self._fit_text(draw, "WELCOME TO 7DR!", CERTIFICATE_BOLD_FONT_PATH, 900, 74, 48)
        name_font = self._fit_text(draw, display_name, CERTIFICATE_BOLD_FONT_PATH, 860, 58, 34)
        subtitle_font = self._fit_text(draw, detail_line, CERTIFICATE_REGULAR_FONT_PATH, 860, 34, 22)
        member_font = self._load_font(CERTIFICATE_REGULAR_FONT_PATH, 28)
        map_font = self._load_font(CERTIFICATE_REGULAR_FONT_PATH, 22)

        avatar = self._render_avatar(avatar_bytes, 220)
        avatar_x = (WELCOME_IMAGE_SIZE[0] - avatar.width) // 2
        avatar_y = 155
        background.alpha_composite(avatar, (avatar_x, avatar_y))

        title_bbox = draw.textbbox((0, 0), "WELCOME TO 7DR!", font=title_font)
        draw.text(((WELCOME_IMAGE_SIZE[0] - (title_bbox[2] - title_bbox[0])) / 2, 28), "WELCOME TO 7DR!", font=title_font, fill=(248, 243, 233, 255))

        name_bbox = draw.textbbox((0, 0), display_name, font=name_font)
        draw.text(((WELCOME_IMAGE_SIZE[0] - (name_bbox[2] - name_bbox[0])) / 2, 415), display_name, font=name_font, fill=(248, 243, 233, 255))

        subtitle_bbox = draw.textbbox((0, 0), detail_line, font=subtitle_font)
        draw.text(((WELCOME_IMAGE_SIZE[0] - (subtitle_bbox[2] - subtitle_bbox[0])) / 2, 485), detail_line, font=subtitle_font, fill=(205, 213, 225, 255))

        member_text = f"Member #{member.guild.member_count or len(member.guild.members)}"
        member_bbox = draw.textbbox((0, 0), member_text, font=member_font)
        draw.text(((WELCOME_IMAGE_SIZE[0] - (member_bbox[2] - member_bbox[0])) / 2, 532), member_text, font=member_font, fill=(157, 199, 255, 255))

        map_bbox = draw.textbbox((0, 0), map_name, font=map_font)
        draw.text((WELCOME_IMAGE_SIZE[0] - (map_bbox[2] - map_bbox[0]) - 80, 575), map_name, font=map_font, fill=(130, 162, 193, 255))

        output = io.BytesIO()
        background.save(output, format="PNG")
        output.seek(0)
        return discord.File(output, filename=f"welcome-{member.id}.png")

    async def _send_welcome_preview(
        self,
        channel: discord.abc.Messageable,
        member: discord.Member,
        *,
        display_name: Optional[str] = None,
        message_text: Optional[str] = None,
        detail_line: str = "just joined the server",
    ) -> None:
        preview_name = display_name or member.display_name
        preview_message = message_text or f"**{preview_name}** has joined 7DR."

        try:
            image_file = await self._build_welcome_image(member, preview_name, detail_line)
        except Exception:
            logger.warning("Failed to build quick-exit welcome card for %s (%s)", member, member.id, exc_info=True)
            image_file = None

        if image_file is not None:
            await channel.send(preview_message, file=image_file)
        else:
            await channel.send(preview_message)

    async def _get_entree_channel(self) -> Optional[discord.abc.Messageable]:
        channel = self.bot.get_channel(ENTREE_CHANNEL_ID)
        if channel is not None:
            return channel
        try:
            return await self.bot.fetch_channel(ENTREE_CHANNEL_ID)
        except discord.HTTPException:
            logger.warning("Failed to fetch entree channel %s", ENTREE_CHANNEL_ID, exc_info=True)
            return None

    async def _deliver_welcome(self, member_id: int) -> None:
        try:
            async with self._state_lock:
                if member_id in self._welcomed_member_ids or member_id not in self._pending_member_ids:
                    return

            member = await self._resolve_member(member_id)
            if member is None or member.bot:
                await self._forget_member(member_id)
                return

            message_text, detail_line = self._compose_welcome_copy(member)

            channel = await self._get_entree_channel()
            if channel is None:
                return

            await self._send_welcome_preview(
                channel,
                member,
                display_name=member.display_name,
                message_text=message_text,
                detail_line=detail_line,
            )

            await self._mark_welcomed(member_id)
        except asyncio.CancelledError:
            return
        except discord.HTTPException:
            logger.warning("Failed to send quick-exit welcome for member %s", member_id, exc_info=True)
        finally:
            self._welcome_tasks.pop(member_id, None)

    async def _backfill_welcomes(self) -> None:
        if self._backfill_complete:
            return
        self._backfill_complete = True

        guild = self.bot.get_guild(MAIN_GUILD_ID)
        if guild is None:
            return

        try:
            if not guild.chunked:
                await guild.chunk(cache=True)
        except discord.HTTPException:
            logger.warning("Failed to chunk guild %s for quick-exit welcome backfill", guild.id, exc_info=True)

        pending_ids: list[int] = []
        for member in guild.members:
            if member.bot or member.id in self._welcomed_member_ids or member.joined_at is None:
                continue
            if member.joined_at.astimezone(timezone.utc) >= self._feature_started_at:
                pending_ids.append(member.id)

        for member_id in pending_ids:
            await self._queue_welcome(member_id)
            self._schedule_welcome(member_id)

    @commands.Cog.listener()
    async def on_ready(self):
        await self._backfill_welcomes()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.guild.id != MAIN_GUILD_ID or member.bot:
            return

        await self._forget_member(member.id)
        await self._queue_welcome(member.id)
        self._schedule_welcome(member.id)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        channel = self.bot.get_channel(LEAVE_CHANNEL_ID)
        if member.guild.id == MAIN_GUILD_ID and not member.bot:
            self._cancel_welcome_task(member.id)
            await self._forget_member(member.id)

        if not channel:
            return

        message = LEAVE_MESSAGE.format(
            display=member.display_name,
            name=member.name
        )

        await channel.send(message)

    @app_commands.guilds(TARGET_GUILD)
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.command(name="testjoin", description="Post a fake join welcome card for preview/testing.")
    @app_commands.describe(name="Display name to use for the fake member", avatar_source="Optional member whose avatar should be used on the card")
    async def testjoin(
        self,
        interaction: discord.Interaction,
        name: Optional[str] = None,
        avatar_source: Optional[discord.Member] = None,
    ) -> None:
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Administrator permission is required.", ephemeral=True)
            return

        channel = await self._get_entree_channel()
        if channel is None:
            await interaction.response.send_message("Entree channel is unavailable.", ephemeral=True)
            return

        preview_member = avatar_source or interaction.user
        preview_name = (name or "Test Member").strip() or "Test Member"

        await interaction.response.send_message(f"Posted test join preview for **{preview_name}**.", ephemeral=True)
        await self._send_welcome_preview(channel, preview_member, display_name=preview_name)

# ================== SETUP ==================

async def setup(bot: commands.Bot):
    await bot.add_cog(QuickExit(bot))