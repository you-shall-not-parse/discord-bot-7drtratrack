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
from PIL import Image, ImageDraw, ImageFont, ImageOps
from discord.ext import commands

from config.common import CERTIFICATE_BOLD_FONT_PATH, CERTIFICATE_REGULAR_FONT_PATH, MAIN_GUILD_ID
from data_paths import data_path

# ================== CONFIG ==================

logger = logging.getLogger(__name__)

LEAVE_CHANNEL_ID = 1097913605539774484  # 👈 replace with your channel ID
ENTREE_CHANNEL_ID = 1099806153170489485
WELCOME_STATE_PATH = Path(data_path("quick_exit_welcome_state.json"))
WELCOME_IMAGE_SIZE = (1200, 675)

LEAVE_MESSAGE = "**{display} ({name})** has just left the server, fuck em"

MAP_CDN_IMAGES = {
    "Elsenborn Ridge Warfare (Dawn)": "https://cdn.discordapp.com/attachments/1098976074852999261/1444494673149300796/ChatGPT_Image_Nov_30_2025_01_05_17_AM.png?ex=69381ebf&is=6936cd3f&hm=cdb114a6a2550d2d83318d3b3c1d6717022fa0c8665c645818fb8c78b8f71fa3",
    "Carentan Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1444515451727253544/file_00000000e5f871f488f94dd458b30c09.png?ex=69383219&is=6936e099&hm=40998a104cbffc2fe0b37c515f6158c9722606b7c1ec5d33bdc03e5eb4341e2a",
    "Foy Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1444492145913499800/ChatGPT_Image_Nov_30_2025_12_55_43_AM.png?ex=69400564&is=693eb3e4&hm=b9c95afd2e8cb88158af73e707f8dbae744e4458be20369029dd92e8a8a467ab",
    "Hill 400 Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1444497579210707004/ChatGPT_Image_Nov_30_2025_01_15_52_AM.png?ex=69382174&is=6936cff4&hm=f9e16ba8d2b9f20dd799bd5970c11f38c1f427689585e2d139cfd1294888a612",
    "St. Marie Du Mont Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1444515451727253544/file_00000000e5f871f488f94dd458b30c09.png?ex=69383219&is=6936e099&hm=40998a104cbffc2fe0b37c515f6158c9722606b7c1ec5d33bdc03e5eb4341e2a",
    "Juno Beach Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1512197512641904781/ChatGPT_Image_Jun_4_2026_09_52_06_PM.png?ex=6a23372e&is=6a21e5ae&hm=7670187d4fc6f2aa7a1266d02629cf185018407186d3556c1df59e62289d76b8",
    "Utah Beach Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1449831598160740402/ChatGPT_Image_Dec_14_2025_06_32_36_PM.png?ex=69405465&is=693f02e5&hm=ec9dbcc1d930df308756a775714ce19d26bebf261a42f384d20af05dc0014004",
    "St. Mere Eglise Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1447681599117463692/file_000000009b64720e96132fbd67f95f72.png?ex=6938820d&is=6937308d&hm=148aca7f2e9de99f00b1f2cb6c55660ae5ece263e62afa83fbece2f9193610ef",
    "El Alamein Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1448462224795373588/file_00000000627c71f4bbc1994fb582be8c.png?ex=693ff651&is=693ea4d1&hm=e6096c26fb8a2c74e9347ebd8477d3b5956521829486e7b192e18f92cffe8830",
    "Mortain Warfare (Dusk)": "https://cdn.discordapp.com/attachments/1098976074852999261/1448462040632004802/76807A80-FA7B-4965-9A21-0798CEA11042.png?ex=693ff625&is=693ea4a5&hm=3a05171a2a203ba1487a324a893829466e68342cebd2659215d53ab9bc93f4b4",
    "Smolensk Warfare (Dusk)": "https://cdn.discordapp.com/attachments/1098976074852999261/1449390736989491363/file_0000000022f071f4a9771a3645023ed5.png?ex=69400b50&is=693eb9d0&hm=5d2d3dffc888d136aacd11c3525e1e3070907f147277785651ef3c79ee2dae7f&",
    "Driel Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1444671257730744360/file_00000000d254720eb1ce02f6506ae926.png?ex=69381a74&is=6936c8f4&hm=e2772de15b5aa855d3abad443e614d5b2280f7a4f529aaf759f515c70d3ca7cc&",
    "Kursk Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1449501011214598214/Screenshot_20251213_221442_Discord.jpg?ex=693fc943&is=693e77c3&hm=a80dc5533d1f73573ea6d3b0bb1adfa1f51cbd936d81a3fefd5535a1fd3dce67",
    "Carentan Warfare (Night)": "https://cdn.discordapp.com/attachments/1098976074852999261/1449390736410939574/file_0000000083ec72468f8a73042c9f9913.png?ex=69400b4f&is=693eb9cf&hm=48754f26b1b1d209ac351b795e906663f0e9c09d2cd21f6e470d8f72970b9005&",
    "Hurtgen Forest Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1444676650653450411/file_000000005384720e8f124201b4e379a9.png?ex=69381f7a&is=6936cdfa&hm=e2d5ea8302bfd2744a5be5a199388945c8eb60218216aae29a5b2ea71aa1e302",
    "Remagen Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1449390736003960889/file_00000000aa3071f492f35b0111fed5e2.png?ex=69400b4f&is=693eb9cf&hm=d776d5f87f3d73a1b1fdcb782c3204a29a055677368edfbc1aac18e04f53bc94&",
    "Omaha Beach Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1448106330052362301/ChatGPT_Image_Dec_10_2025_12_16_56_AM.png?ex=693a0d9d&is=6938bc1d&hm=6614c98b63a7c58eaea7638a718ef854e5c074796001808cb6faf0557b46ea2a",
    "Kharkov Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1444687960845979780/file_0000000068b47208b053f27323047cda.png?ex=69382a02&is=6936d882&hm=5c7745f15e886825b5b26d3ed4b18a33808332cd2dbedc71e5dba0f8bd9bda8c&",
    "Purple Heart Lane Warfare (Rain)": "https://cdn.discordapp.com/attachments/1098976074852999261/1442258185137295380/file_000000009ba871f4b7700cb80af3a3f3.png?ex=6937e4db&is=6936935b&hm=ffcf7d5e580476b6af6f2c5a1a1055ed656aa86034c14094d9434b0d2019f8cc&g",
    "Tobruk Warfare (Dawn)": "https://cdn.discordapp.com/attachments/1098976074852999261/1449390737593602259/file_00000000735871f4bb2cbbbced7ffbf7.png?ex=69400b50&is=693eb9d0&hm=5ec261995e8bb89a059a686f41ef8da731a5cbdd44dddb4bc356ddec9f368309&",
    "Stalingrad Warfare": "https://cdn.discordapp.com/attachments/1098976074852999261/1449396751206191364/file_00000000d4c871f4ac3d6d200f6a92ca_1.png?ex=694010e9&is=693ebf69&hm=1a90a0b6c9af30b6d400cc70d89d36ad778d88fb759d125abffc669b8511acf2&",
}

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
        return (f"Hey {member.mention}, welcome to **7DR!**", "just joined the server")

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

    async def _build_welcome_image(self, member: discord.Member, detail_line: str) -> discord.File:
        map_name, background_url = random.choice(list(MAP_CDN_IMAGES.items()))
        avatar_url = member.display_avatar.replace(format="png", size=256).url

        try:
            background_bytes, avatar_bytes = await asyncio.gather(
                self._fetch_bytes(background_url),
                self._fetch_bytes(avatar_url),
            )
            with Image.open(io.BytesIO(background_bytes)).convert("RGBA") as background_src:
                background = ImageOps.fit(background_src, WELCOME_IMAGE_SIZE, Image.Resampling.LANCZOS)
        except Exception:
            logger.warning("Failed to fetch quick-exit welcome image assets for %s (%s)", member, member.id, exc_info=True)
            background = self._build_fallback_background()
            avatar_bytes = await self._fetch_bytes(avatar_url)

        overlay = Image.new("RGBA", WELCOME_IMAGE_SIZE, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.rounded_rectangle((90, 48, 1110, 627), radius=30, fill=(5, 8, 14, 168))
        overlay_draw.rectangle((90, 500, 1110, 627), fill=(3, 5, 10, 210))
        background.alpha_composite(overlay)

        draw = ImageDraw.Draw(background)
        title_font = self._fit_text(draw, "WELCOME TO 7DR!", CERTIFICATE_BOLD_FONT_PATH, 900, 74, 48)
        name_font = self._fit_text(draw, member.display_name, CERTIFICATE_BOLD_FONT_PATH, 860, 58, 34)
        subtitle_font = self._fit_text(draw, detail_line, CERTIFICATE_REGULAR_FONT_PATH, 860, 34, 22)
        member_font = self._load_font(CERTIFICATE_REGULAR_FONT_PATH, 28)
        map_font = self._load_font(CERTIFICATE_REGULAR_FONT_PATH, 22)

        avatar = self._render_avatar(avatar_bytes, 220)
        avatar_x = (WELCOME_IMAGE_SIZE[0] - avatar.width) // 2
        avatar_y = 90
        background.alpha_composite(avatar, (avatar_x, avatar_y))

        title_bbox = draw.textbbox((0, 0), "WELCOME TO 7DR!", font=title_font)
        draw.text(((WELCOME_IMAGE_SIZE[0] - (title_bbox[2] - title_bbox[0])) / 2, 26), "WELCOME TO 7DR!", font=title_font, fill=(248, 243, 233, 255))

        name_bbox = draw.textbbox((0, 0), member.display_name, font=name_font)
        draw.text(((WELCOME_IMAGE_SIZE[0] - (name_bbox[2] - name_bbox[0])) / 2, 342), member.display_name, font=name_font, fill=(248, 243, 233, 255))

        subtitle_bbox = draw.textbbox((0, 0), detail_line, font=subtitle_font)
        draw.text(((WELCOME_IMAGE_SIZE[0] - (subtitle_bbox[2] - subtitle_bbox[0])) / 2, 412), detail_line, font=subtitle_font, fill=(205, 213, 225, 255))

        member_text = f"Member #{member.guild.member_count or len(member.guild.members)}"
        member_bbox = draw.textbbox((0, 0), member_text, font=member_font)
        draw.text(((WELCOME_IMAGE_SIZE[0] - (member_bbox[2] - member_bbox[0])) / 2, 458), member_text, font=member_font, fill=(157, 199, 255, 255))

        map_bbox = draw.textbbox((0, 0), map_name, font=map_font)
        draw.text((WELCOME_IMAGE_SIZE[0] - (map_bbox[2] - map_bbox[0]) - 120, 575), map_name, font=map_font, fill=(130, 162, 193, 255))

        output = io.BytesIO()
        background.save(output, format="PNG")
        output.seek(0)
        return discord.File(output, filename=f"welcome-{member.id}.png")

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

            try:
                image_file = await self._build_welcome_image(member, detail_line)
            except Exception:
                logger.warning("Failed to build quick-exit welcome card for %s (%s)", member, member.id, exc_info=True)
                image_file = None

            if image_file is not None:
                await channel.send(message_text, file=image_file)
            else:
                await channel.send(message_text)

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

# ================== SETUP ==================

async def setup(bot: commands.Bot):
    await bot.add_cog(QuickExit(bot))