import logging
import re
import json
import os
import asyncio
import io
import random
import calendar
from urllib.parse import urlencode
from typing import Optional
from datetime import datetime, timedelta, timezone

import aiohttp
import discord
from discord.ext import commands, tasks

from config.common import SCOREBOARD_FONT_PATH
from data_paths import data_path

logger = logging.getLogger(__name__)

# =============================
# CONFIG (EDIT THIS)
# =============================
# Channel ID where events will be posted
EVENT_DISPLAY_CHANNEL_ID = 1332736267485708419  # Replace with your channel ID

# How often to update the events display (in minutes)
UPDATE_INTERVAL_MINUTES = 30

# Maximum number of events to display - 25 is the max allowed by Discord per embed
MAX_EVENTS_TO_DISPLAY = 25

# Discord embed limits relevant to this calendar display.
EMBED_TOTAL_CHAR_LIMIT = 6000
EMBED_FIELD_VALUE_LIMIT = 1024

# Color for the embed
EMBED_COLOR = 0x5865F2  # Discord blurple

# Path to save events JSON
EVENTS_JSON_PATH = data_path("events_history.json")

# Path to persist the display message across restarts
EVENTS_DISPLAY_STATE_PATH = data_path("events_display_state.json")

# -----------------------------
# EVENT THREADS (AUTO)
# -----------------------------
# When a new scheduled event is created, the bot will create a thread in this channel.
# Default: use the same channel as the calendar embed.
EVENT_THREADS_PARENT_CHANNEL_ID = 1192922522673500190

# Auto-archive duration for the created threads (minutes).
# Valid values depend on the server settings: 60, 1440, 4320, 10080.
EVENT_THREAD_AUTO_ARCHIVE_MINUTES = 10080

# Forum channel where event discussion posts should be created.
EVENT_FORUM_CHANNEL_ID = 1506797609489272934

# Random background image pool used for event cover images.
EVENT_FORUM_BACKGROUND_URLS: list[str] = [
    "https://cdn.discordapp.com/attachments/1098976074852999261/1448099075143503922/file_0000000040dc7208b0cf42742a355373.png?ex=6a0ef11b&is=6a0d9f9b&hm=3362f8760b775caaabe6f44e7bf55ad66bf54a42bd5f2f86911ab60a6b8eab54",
    "https://cdn.discordapp.com/attachments/1098976074852999261/1444494673149300796/ChatGPT_Image_Nov_30_2025_01_05_17_AM.png?ex=6a0f033f&is=6a0db1bf&hm=57a1df865acdd76d2f13f81a92d3509cec8ee5e95ede2f8fd4de102c2b614122",
    "https://cdn.discordapp.com/attachments/1098976074852999261/1444490230957608980/ChatGPT_Image_Nov_30_2025_12_47_57_AM.png?ex=6a0eff1c&is=6a0dad9c&hm=76b339a9b853a022c9588097227b29a9fe0503c10a8f5f8eaffff8eea9f60288",
]

EVENT_FORUM_FILENAME = "event-cover.png"
EVENT_FORUM_IMAGE_SIZE = (1600, 900)
EVENT_FORUM_OPEN_LEAD = timedelta(hours=24)
EVENT_FORUM_SYNC_WINDOW = timedelta(hours=24)
EVENT_IMAGE_FONT_PATH = SCOREBOARD_FONT_PATH

# Persist which events we've already handled so we don't create duplicate threads.
EVENTS_THREAD_STATE_PATH = data_path("events_threads_state.json")

# -----------------------------
# EVENT TITLE EMOJI TAGGING
# -----------------------------
# If an event name contains one of these keywords, the bot will append the
# corresponding custom server emoji *after* that keyword in the displayed title.
#
# Put the emoji name in Discord's short-name format (e.g. ":48th:") and make sure
# the custom emoji exists in the same server as the event.
KEYWORD_EMOJI_TAGS: dict[str, str] = {
    "RDG": ":RDG:",
    "RMC": ":RMC:",
    "48th": ":48th:",
    "HellEU": ":helleu:",
    "7DR": ":7DR:",
    "KRTS": ":KRTS:",
    "50A": ":flag_es:",
    "OFIN": ":flag_fi:",
    "DLB": ":flag_nl:",
    "PG60": ":flag_de:",
}


class EventDisplayCog(commands.Cog, name="EventDisplayCog"):
    """
    A cog that reads Discord scheduled events and displays them in an embed.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.display_message_id: Optional[int] = self._load_display_message_id()
        self._target_guild_id: Optional[int] = None
        self._update_lock = asyncio.Lock()
        self._debounce_task: Optional[asyncio.Task] = None
        self._thread_state = self._load_thread_state()
        self._background_cache: dict[str, bytes] = {}
        self._missing_background_sources: set[str] = set()
        self.update_events_display.start()
        logger.info("EventDisplayCog initialized")

    def cog_unload(self):
        """Stop the background task when the cog is unloaded."""
        self.update_events_display.cancel()
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
        logger.info("EventDisplayCog unloaded")

    @tasks.loop(minutes=UPDATE_INTERVAL_MINUTES)
    async def update_events_display(self):
        """Periodic refresh."""
        await self._update_once(reason="interval")

    @update_events_display.before_loop
    async def before_update_events_display(self):
        """Wait until the bot is ready before starting the loop."""
        await self.bot.wait_until_ready()
        logger.info("EventDisplayCog: Bot is ready, starting event display loop")

        # On startup, establish the target guild and optionally create threads for any
        # events that appeared while the bot was offline.
        await self._startup_sync_threads()

    def _load_thread_state(self) -> dict:
        try:
            if not os.path.exists(EVENTS_THREAD_STATE_PATH):
                return {"initialized": False, "seen_event_ids": [], "threads": {}}
            with open(EVENTS_THREAD_STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
            if not isinstance(state, dict):
                return {"initialized": False, "seen_event_ids": [], "threads": {}}
            state.setdefault("initialized", False)
            state.setdefault("seen_event_ids", [])
            state.setdefault("threads", {})
            return state
        except Exception:
            logger.warning("Could not read events thread state; will recreate it.", exc_info=True)
            return {"initialized": False, "seen_event_ids": [], "threads": {}}

    def _save_thread_state(self) -> None:
        try:
            self._thread_state["updated_at"] = datetime.utcnow().isoformat()
            with open(EVENTS_THREAD_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(self._thread_state, f, indent=2, ensure_ascii=False)
        except Exception:
            logger.warning("Failed to persist events thread state.", exc_info=True)

    def _is_event_seen(self, event_id: int) -> bool:
        return str(event_id) in set(map(str, self._thread_state.get("seen_event_ids", [])))

    def _mark_event_seen(self, event_id: int) -> None:
        seen = set(map(str, self._thread_state.get("seen_event_ids", [])))
        seen.add(str(event_id))
        self._thread_state["seen_event_ids"] = sorted(seen)

    def _get_event_state(self, event_id: int) -> dict:
        return self._thread_state.setdefault("threads", {}).setdefault(str(event_id), {})

    async def _startup_sync_threads(self) -> None:
        """Initialize thread state and handle events created while offline."""

        try:
            channel = self.bot.get_channel(EVENT_DISPLAY_CHANNEL_ID)
            if not isinstance(channel, discord.TextChannel):
                return
            guild = channel.guild
            if not guild:
                return

            self._target_guild_id = guild.id

            current_events = await guild.fetch_scheduled_events(with_counts=False)

            # First ever run: mark all existing events as seen so we don't spam threads.
            if not self._thread_state.get("initialized", False):
                for ev in current_events:
                    self._mark_event_seen(ev.id)
                self._thread_state["initialized"] = True
                await self._sync_forum_posts(guild, current_events)
                self._save_thread_state()
                logger.info("Initialized events thread state (existing events marked as seen)")
                return

            # Subsequent runs: create threads for any events we haven't seen yet.
            for ev in current_events:
                if not self._is_event_seen(ev.id):
                    await self._create_event_thread(ev)
                    self._mark_event_seen(ev.id)

            await self._sync_forum_posts(guild, current_events)
            self._save_thread_state()

        except Exception:
            logger.warning("Startup thread sync failed.", exc_info=True)

    async def _create_event_thread(self, scheduled_event: discord.ScheduledEvent) -> None:
        parent = self.bot.get_channel(EVENT_THREADS_PARENT_CHANNEL_ID)
        if not isinstance(parent, discord.TextChannel):
            logger.warning("Thread parent channel is missing or not a text channel")
            return

        # Build a starter message; threads are created from messages reliably.
        start_time_str = (
            f"<t:{int(scheduled_event.start_time.timestamp())}:F>"
            if scheduled_event.start_time
            else "TBA"
        )

        organiser = "Unknown"
        if getattr(scheduled_event, "creator", None):
            organiser = scheduled_event.creator.mention
        elif getattr(scheduled_event, "creator_id", None):
            organiser = f"<@{scheduled_event.creator_id}>"

        title = self._format_event_title(parent.guild, scheduled_event.name)

        # No URLs in the starter text to avoid link embeds.
        starter_text = (
            f"📅 New event created: **{title}**\n"
            f"**Date/Time:** {start_time_str}\n"
            f"**Added By:** {organiser}"
        )

        state = self._get_event_state(scheduled_event.id)

        try:
            starter_msg = await parent.send(starter_text)

            date_suffix = "TBA"
            if scheduled_event.start_time:
                date_suffix = scheduled_event.start_time.strftime("%d/%m/%Y")

            thread_name = f"{scheduled_event.name} - {date_suffix}".strip()
            if len(thread_name) > 100:
                thread_name = thread_name[:97] + "..."

            thread = await starter_msg.create_thread(
                name=thread_name,
                auto_archive_duration=EVENT_THREAD_AUTO_ARCHIVE_MINUTES,
            )

            state.update(
                {
                    "thread_id": thread.id,
                    "starter_message_id": starter_msg.id,
                    "created_at": datetime.utcnow().isoformat(),
                }
            )
            logger.info(f"Created thread {thread.id} for event {scheduled_event.id}")

        except discord.Forbidden:
            logger.warning("Missing permissions to create event thread (send message / create thread)")
        except Exception:
            logger.warning("Failed to create event thread.", exc_info=True)

    async def _fetch_raw_scheduled_events(self, guild: discord.Guild) -> dict[int, dict]:
        token = getattr(getattr(self.bot, "http", None), "token", None)
        if not token:
            return {}

        headers = {"Authorization": f"Bot {token}"}
        url = f"https://discord.com/api/v10/guilds/{guild.id}/scheduled-events?with_user_count=true"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        logger.warning("Failed to fetch raw scheduled events for guild %s (status %s)", guild.id, response.status)
                        return {}
                    payload = await response.json()
        except Exception:
            logger.warning("Failed to fetch raw scheduled events for guild %s", guild.id, exc_info=True)
            return {}

        result: dict[int, dict] = {}
        for item in payload if isinstance(payload, list) else []:
            try:
                result[int(item["id"])] = item
            except Exception:
                continue
        return result

    def _parse_iso_datetime(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            return None

    def _is_recurring_event_payload(self, payload: Optional[dict]) -> bool:
        return bool(payload and payload.get("recurrence_rule"))

    def _add_months(self, value: datetime, months: int) -> datetime:
        total_month = (value.month - 1) + months
        year = value.year + (total_month // 12)
        month = (total_month % 12) + 1
        day = min(value.day, calendar.monthrange(year, month)[1])
        return value.replace(year=year, month=month, day=day)

    def _add_years(self, value: datetime, years: int) -> datetime:
        year = value.year + years
        day = value.day
        if value.month == 2 and value.day == 29 and not calendar.isleap(year):
            day = 28
        return value.replace(year=year, day=day)

    def _matches_weekday_rules(self, candidate: datetime, recurrence_rule: dict) -> bool:
        by_weekday = recurrence_rule.get("by_weekday") or []
        if by_weekday and candidate.weekday() not in {day for day in by_weekday if isinstance(day, int)}:
            return False

        by_n_weekday = recurrence_rule.get("by_n_weekday") or []
        if by_n_weekday:
            matched = False
            for item in by_n_weekday:
                if not isinstance(item, dict):
                    continue
                day = item.get("day")
                ordinal = item.get("n")
                if not isinstance(day, int) or not isinstance(ordinal, int):
                    continue
                if candidate.weekday() != day:
                    continue
                weekday_occurrence = ((candidate.day - 1) // 7) + 1
                if weekday_occurrence == ordinal:
                    matched = True
                    break
            if not matched:
                return False

        return True

    def _matches_month_rules(self, candidate: datetime, recurrence_rule: dict) -> bool:
        by_month = recurrence_rule.get("by_month") or []
        if by_month and candidate.month not in {month for month in by_month if isinstance(month, int)}:
            return False

        by_month_day = recurrence_rule.get("by_month_day") or []
        if by_month_day and candidate.day not in {day for day in by_month_day if isinstance(day, int)}:
            return False

        by_year_day = recurrence_rule.get("by_year_day") or []
        if by_year_day:
            day_of_year = candidate.timetuple().tm_yday
            if day_of_year not in {day for day in by_year_day if isinstance(day, int)}:
                return False

        return True

    def _candidate_matches_recurrence(
        self,
        candidate: datetime,
        *,
        rule_start: datetime,
        recurrence_rule: dict,
    ) -> bool:
        if candidate < rule_start:
            return False

        frequency = recurrence_rule.get("frequency")
        interval = max(int(recurrence_rule.get("interval") or 1), 1)

        if frequency == 3:
            delta_days = (candidate.date() - rule_start.date()).days
            if delta_days < 0 or delta_days % interval != 0:
                return False
        elif frequency == 2:
            delta_days = (candidate.date() - rule_start.date()).days
            if delta_days < 0:
                return False
            weeks_apart = delta_days // 7
            if weeks_apart % interval != 0:
                return False
        elif frequency == 1:
            months_apart = (candidate.year - rule_start.year) * 12 + (candidate.month - rule_start.month)
            if months_apart < 0 or months_apart % interval != 0:
                return False
        elif frequency == 0:
            years_apart = candidate.year - rule_start.year
            if years_apart < 0 or years_apart % interval != 0:
                return False
        else:
            return False

        return self._matches_weekday_rules(candidate, recurrence_rule) and self._matches_month_rules(candidate, recurrence_rule)

    def _iter_candidate_occurrences(
        self,
        *,
        rule_start: datetime,
        recurrence_rule: dict,
        window_start: datetime,
        window_end: datetime,
    ):
        frequency = recurrence_rule.get("frequency")
        interval = max(int(recurrence_rule.get("interval") or 1), 1)
        count = recurrence_rule.get("count")
        max_count = count if isinstance(count, int) and count > 0 else None

        if frequency == 3:
            cursor = rule_start + timedelta(days=max(0, (window_start.date() - rule_start.date()).days // interval) * interval)
            while cursor < window_start:
                cursor += timedelta(days=interval)
            step = lambda dt: dt + timedelta(days=interval)
        elif frequency == 2:
            delta_days = max(0, (window_start.date() - rule_start.date()).days)
            weeks_offset = delta_days // 7
            cursor = rule_start + timedelta(weeks=(weeks_offset // interval) * interval)
            while cursor < window_start - timedelta(days=7):
                cursor += timedelta(weeks=interval)
            step = lambda dt: dt + timedelta(weeks=interval)
        elif frequency == 1:
            months_offset = max(0, (window_start.year - rule_start.year) * 12 + (window_start.month - rule_start.month))
            cursor = self._add_months(rule_start, (months_offset // interval) * interval)
            while cursor < window_start.replace(day=1) - timedelta(days=31):
                cursor = self._add_months(cursor, interval)
            step = lambda dt: self._add_months(dt, interval)
        elif frequency == 0:
            years_offset = max(0, window_start.year - rule_start.year)
            cursor = self._add_years(rule_start, (years_offset // interval) * interval)
            while cursor < window_start.replace(month=1, day=1) - timedelta(days=366):
                cursor = self._add_years(cursor, interval)
            step = lambda dt: self._add_years(dt, interval)
        else:
            return

        emitted = 0
        while cursor <= window_end:
            if self._candidate_matches_recurrence(cursor, rule_start=rule_start, recurrence_rule=recurrence_rule):
                emitted += 1
                if max_count is None or emitted <= max_count:
                    yield cursor
                else:
                    return
            cursor = step(cursor)

    def _find_matching_occurrence(
        self,
        *,
        rule_start: datetime,
        recurrence_rule: dict,
        window_start: datetime,
        window_end: datetime,
    ) -> Optional[datetime]:
        rule_end = self._parse_iso_datetime(recurrence_rule.get("end"))
        if rule_end is not None and rule_end < window_start:
            return None

        if rule_end is not None and rule_end < window_end:
            window_end = rule_end

        for candidate in self._iter_candidate_occurrences(
            rule_start=rule_start,
            recurrence_rule=recurrence_rule,
            window_start=window_start,
            window_end=window_end,
        ):
            return candidate.astimezone(timezone.utc)
        return None

    def _get_due_occurrence_start(
        self,
        payload: Optional[dict],
        *,
        now: datetime,
        fallback_start: Optional[datetime],
    ) -> Optional[datetime]:
        if not payload:
            return fallback_start

        recurrence_rule = payload.get("recurrence_rule")
        scheduled_start = self._parse_iso_datetime(payload.get("scheduled_start_time")) or fallback_start
        if not recurrence_rule:
            return scheduled_start

        rule_start = self._parse_iso_datetime(recurrence_rule.get("start")) or scheduled_start
        if rule_start is None:
            return scheduled_start

        try:
            window_start = now - EVENT_FORUM_SYNC_WINDOW
            candidate = self._find_matching_occurrence(
                rule_start=rule_start,
                recurrence_rule=recurrence_rule,
                window_start=window_start,
                window_end=now + EVENT_FORUM_OPEN_LEAD,
            )
        except Exception:
            logger.warning("Failed to calculate recurring event occurrence for payload %s", payload.get("id"), exc_info=True)
            return scheduled_start

        if candidate is None:
            return None
        return candidate.astimezone(timezone.utc)

    def _format_event_datetime_text(
        self,
        start_time: Optional[datetime],
        end_time: Optional[datetime],
    ) -> str:
        if start_time is None:
            return "Date and time to be confirmed"

        start_utc = start_time.astimezone(timezone.utc)
        if end_time is not None:
            end_utc = end_time.astimezone(timezone.utc)
            if start_utc.date() == end_utc.date():
                return f"{start_utc.strftime('%d %b %Y')}  |  {start_utc.strftime('%H:%M')} - {end_utc.strftime('%H:%M')} UTC"
            return f"{start_utc.strftime('%d %b %Y %H:%M')} UTC  |  {end_utc.strftime('%d %b %Y %H:%M')} UTC"

        return f"{start_utc.strftime('%d %b %Y  |  %H:%M UTC')}"

    def _truncate_thread_name(self, value: str) -> str:
        value = " ".join((value or "").split())
        if len(value) <= 100:
            return value
        return value[:97].rstrip() + "..."

    def _build_event_post_name(self, event_name: str, occurrence_start: Optional[datetime]) -> str:
        suffix = "TBA"
        if occurrence_start is not None:
            suffix = occurrence_start.astimezone(timezone.utc).strftime("%d/%m/%Y")
        return self._truncate_thread_name(f"{event_name} - {suffix}".strip())

    def _build_event_post_embed(
        self,
        *,
        scheduled_event: discord.ScheduledEvent,
        title: str,
        occurrence_start: Optional[datetime],
    ) -> discord.Embed:
        embed = discord.Embed(
            title=title,
            colour=discord.Colour.blurple(),
            timestamp=datetime.utcnow(),
        )
        embed.description = self._truncate_text(
            scheduled_event.description or "Discussion thread for this scheduled event.",
            4000,
        )
        embed.add_field(
            name="Date / Time",
            value=self._format_event_datetime_text(occurrence_start or scheduled_event.start_time, scheduled_event.end_time),
            inline=False,
        )
        if scheduled_event.location:
            embed.add_field(name="Location", value=scheduled_event.location, inline=False)
        elif scheduled_event.channel:
            embed.add_field(name="Channel", value=scheduled_event.channel.mention, inline=False)
        organiser = "Unknown"
        if getattr(scheduled_event, "creator", None):
            organiser = scheduled_event.creator.mention
        elif getattr(scheduled_event, "creator_id", None):
            organiser = f"<@{scheduled_event.creator_id}>"
        embed.add_field(name="Added By", value=organiser, inline=False)
        if getattr(scheduled_event, "url", None):
            embed.add_field(name="Event Link", value=str(scheduled_event.url), inline=False)
        google_calendar_url = self._build_google_calendar_url(scheduled_event)
        if google_calendar_url:
            embed.add_field(name="Google Calendar", value=google_calendar_url, inline=False)
        if scheduled_event.guild_id:
            thread_info = self._thread_state.get("threads", {}).get(str(scheduled_event.id))
            if isinstance(thread_info, dict):
                thread_id = thread_info.get("thread_id")
                if isinstance(thread_id, int):
                    embed.add_field(
                        name="Discussion Thread",
                        value=f"https://discord.com/channels/{scheduled_event.guild_id}/{thread_id}",
                        inline=False,
                    )
        embed.set_image(url=f"attachment://{EVENT_FORUM_FILENAME}")
        embed.set_footer(text="Event details update automatically")
        return embed

    async def _load_background_bytes(self, source: str) -> Optional[bytes]:
        cached = self._background_cache.get(source)
        if cached is not None:
            return cached
        if source in self._missing_background_sources:
            return None

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(source) as response:
                    if response.status != 200:
                        self._missing_background_sources.add(source)
                        logger.warning("Failed to fetch event background %s (status %s)", source, response.status)
                        return None
                    data = await response.read()
        except Exception:
            self._missing_background_sources.add(source)
            logger.warning("Failed to load event background from %s", source, exc_info=True)
            return None

        self._background_cache[source] = data
        return data

    def _pick_event_background(self, event_id: int, state: dict) -> Optional[str]:
        stored = state.get("background_url")
        if stored:
            return stored
        if not EVENT_FORUM_BACKGROUND_URLS:
            return None
        url = random.choice(EVENT_FORUM_BACKGROUND_URLS)
        state["background_url"] = url
        return url

    async def _render_event_cover_image(
        self,
        *,
        title: str,
        start_time: Optional[datetime],
        end_time: Optional[datetime],
        background_url: Optional[str],
    ) -> bytes:
        from PIL import Image, ImageDraw, ImageFont, ImageOps  # pyright: ignore[reportMissingImports]

        width, height = EVENT_FORUM_IMAGE_SIZE
        background_bytes = await self._load_background_bytes(background_url) if background_url else None

        if background_bytes:
            try:
                with Image.open(io.BytesIO(background_bytes)) as source_image:
                    base = ImageOps.fit(source_image.convert("RGBA"), (width, height), method=Image.Resampling.LANCZOS)
            except Exception:
                logger.warning("Failed to render event background from %s", background_url, exc_info=True)
                base = Image.new("RGBA", (width, height), (18, 24, 38, 255))
        else:
            base = Image.new("RGBA", (width, height), (18, 24, 38, 255))

        overlay = Image.new("RGBA", (width, height), (8, 12, 20, 145))
        base = Image.alpha_composite(base, overlay)
        draw = ImageDraw.Draw(base)
        text_fill = (255, 255, 255, 255)
        content_width = width - 220
        title_top = 170

        def load_font(size: int):
            try:
                return ImageFont.truetype(EVENT_IMAGE_FONT_PATH, size)
            except Exception:
                return ImageFont.load_default()

        def wrap_text(text: str, font, max_width: int) -> list[str]:
            words = text.split()
            if not words:
                return [""]

            lines: list[str] = []
            current = words[0]
            for word in words[1:]:
                candidate = f"{current} {word}".strip()
                bbox = draw.textbbox((0, 0), candidate, font=font)
                if (bbox[2] - bbox[0]) <= max_width:
                    current = candidate
                else:
                    lines.append(current)
                    current = word
            lines.append(current)
            return lines

        def fit_multiline_font(text: str, max_width: int, max_height: int, start_size: int, min_size: int):
            for size in range(start_size, min_size - 1, -2):
                font = load_font(size)
                lines = wrap_text(text, font, max_width)
                spacing = max(10, size // 5)
                bbox = draw.multiline_textbbox((0, 0), "\n".join(lines), font=font, spacing=spacing, align="center")
                if (bbox[2] - bbox[0]) <= max_width and (bbox[3] - bbox[1]) <= max_height:
                    return font, lines, spacing
            font = load_font(min_size)
            lines = wrap_text(text, font, max_width)
            return font, lines, max(8, min_size // 5)

        title_font, wrapped_title, title_spacing = fit_multiline_font(title, content_width, 360, 110, 38)
        title_text = "\n".join(wrapped_title)
        title_bbox = draw.multiline_textbbox((0, 0), title_text, font=title_font, spacing=title_spacing, align="center")
        title_height = title_bbox[3] - title_bbox[1]

        date_text = self._format_event_datetime_text(start_time, end_time)
        date_font, wrapped_date, date_spacing = fit_multiline_font(date_text, content_width, 140, 62, 24)
        date_render = "\n".join(wrapped_date)
        date_bbox = draw.multiline_textbbox((0, 0), date_render, font=date_font, spacing=date_spacing, align="center")
        date_height = date_bbox[3] - date_bbox[1]

        title_y = max(title_top, (height - title_height - date_height - 80) // 2)
        date_y = min(height - 180 - date_height, title_y + title_height + 60)

        draw.multiline_text((width // 2, title_y), title_text, font=title_font, fill=text_fill, anchor="ma", align="center", spacing=title_spacing)
        draw.multiline_text((width // 2, date_y), date_render, font=date_font, fill=text_fill, anchor="ma", align="center", spacing=date_spacing)

        out = io.BytesIO()
        base.save(out, format="PNG")
        out.seek(0)
        return out.getvalue()

    async def _update_existing_event_thread(self, scheduled_event: discord.ScheduledEvent, occurrence_start: Optional[datetime]) -> None:
        state = self._thread_state.get("threads", {}).get(str(scheduled_event.id))
        if not isinstance(state, dict):
            return

        thread_id = state.get("thread_id")
        starter_message_id = state.get("starter_message_id")
        if not isinstance(thread_id, int) or not isinstance(starter_message_id, int):
            return

        parent = self.bot.get_channel(EVENT_THREADS_PARENT_CHANNEL_ID)
        thread = self.bot.get_channel(thread_id)
        if not isinstance(parent, discord.TextChannel) or not isinstance(thread, discord.Thread):
            return

        title = self._format_event_title(parent.guild, scheduled_event.name)
        organiser = "Unknown"
        if getattr(scheduled_event, "creator", None):
            organiser = scheduled_event.creator.mention
        elif getattr(scheduled_event, "creator_id", None):
            organiser = f"<@{scheduled_event.creator_id}>"

        start_to_display = occurrence_start or scheduled_event.start_time
        start_time_str = f"<t:{int(start_to_display.timestamp())}:F>" if start_to_display else "TBA"
        starter_text = (
            f"📅 New event created: **{title}**\n"
            f"**Date/Time:** {start_time_str}\n"
            f"**Added By:** {organiser}"
        )

        try:
            starter_message = await parent.fetch_message(starter_message_id)
            await starter_message.edit(content=starter_text)
        except Exception:
            logger.warning("Failed to update starter message for event %s", scheduled_event.id, exc_info=True)

        try:
            await thread.edit(name=self._build_event_post_name(scheduled_event.name, start_to_display))
        except Exception:
            logger.warning("Failed to rename thread for event %s", scheduled_event.id, exc_info=True)

    async def _upsert_forum_post(
        self,
        scheduled_event: discord.ScheduledEvent,
        *,
        occurrence_start: Optional[datetime],
    ) -> None:
        forum = self.bot.get_channel(EVENT_FORUM_CHANNEL_ID)
        if not isinstance(forum, discord.ForumChannel):
            logger.warning("Forum channel %s is missing or not a forum channel", EVENT_FORUM_CHANNEL_ID)
            return

        state = self._get_event_state(scheduled_event.id)
        if state.get("forum_deleted_manually"):
            return

        title = self._format_event_title(forum.guild, scheduled_event.name)
        embed = self._build_event_post_embed(scheduled_event=scheduled_event, title=title, occurrence_start=occurrence_start)

        state["forum_occurrence_start"] = occurrence_start.isoformat() if occurrence_start else None

        forum_thread_id = state.get("forum_thread_id")
        forum_message_id = state.get("forum_message_id")
        existing_thread = self.bot.get_channel(forum_thread_id) if isinstance(forum_thread_id, int) else None

        if isinstance(forum_thread_id, int) and existing_thread is None:
            state["forum_deleted_manually"] = True
            state["forum_deleted_at"] = datetime.utcnow().isoformat()
            logger.info(
                "Forum post for event %s is missing; treating it as manually deleted and not recreating it",
                scheduled_event.id,
            )
            return

        if isinstance(existing_thread, discord.Thread) and isinstance(forum_message_id, int):
            try:
                starter_message = await existing_thread.fetch_message(forum_message_id)
                await starter_message.edit(content=None, embed=embed)
                await existing_thread.edit(name=self._build_event_post_name(scheduled_event.name, occurrence_start or scheduled_event.start_time))
                return
            except discord.NotFound:
                state["forum_deleted_manually"] = True
                state["forum_deleted_at"] = datetime.utcnow().isoformat()
                logger.info(
                    "Forum starter message for event %s is missing; treating it as manually deleted and not recreating it",
                    scheduled_event.id,
                )
                return
            except Exception:
                logger.warning("Failed to update forum post for event %s", scheduled_event.id, exc_info=True)

        try:
            background_url = self._pick_event_background(scheduled_event.id, state)
            image_bytes = await self._render_event_cover_image(
                title=scheduled_event.name,
                start_time=occurrence_start or scheduled_event.start_time,
                end_time=scheduled_event.end_time,
                background_url=background_url,
            )
            created = await forum.create_thread(
                name=self._build_event_post_name(scheduled_event.name, occurrence_start or scheduled_event.start_time),
                embed=embed,
                file=discord.File(io.BytesIO(image_bytes), filename=EVENT_FORUM_FILENAME),
                auto_archive_duration=EVENT_THREAD_AUTO_ARCHIVE_MINUTES,
            )
            state.update(
                {
                    "forum_thread_id": created.thread.id,
                    "forum_message_id": created.message.id,
                    "forum_created_at": datetime.utcnow().isoformat(),
                }
            )
        except discord.Forbidden:
            logger.warning("Missing permissions to create event forum post")
        except Exception:
            logger.warning("Failed to create event forum post for event %s", scheduled_event.id, exc_info=True)

    async def _sync_forum_posts(
        self,
        guild: discord.Guild,
        events: list[discord.ScheduledEvent],
    ) -> None:
        raw_events = await self._fetch_raw_scheduled_events(guild)
        now = datetime.now(timezone.utc)

        for scheduled_event in events:
            if scheduled_event.status not in (discord.EventStatus.scheduled, discord.EventStatus.active):
                continue

            payload = raw_events.get(scheduled_event.id)
            occurrence_start = self._get_due_occurrence_start(
                payload,
                now=now,
                fallback_start=scheduled_event.start_time,
            )
            is_recurring = self._is_recurring_event_payload(payload)

            if is_recurring and occurrence_start is None:
                continue

            await self._update_existing_event_thread(scheduled_event, occurrence_start)
            await self._upsert_forum_post(scheduled_event, occurrence_start=occurrence_start)

    def _load_display_message_id(self) -> Optional[int]:
        try:
            if not os.path.exists(EVENTS_DISPLAY_STATE_PATH):
                return None
            with open(EVENTS_DISPLAY_STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
            channel_id = state.get("channel_id")
            message_id = state.get("message_id")
            if channel_id != EVENT_DISPLAY_CHANNEL_ID:
                return None
            if isinstance(message_id, int):
                return message_id
            return None
        except Exception:
            logger.warning("Could not read events display state; will create a new message.", exc_info=True)
            return None

    def _save_display_message_id(self) -> None:
        try:
            state = {
                "channel_id": EVENT_DISPLAY_CHANNEL_ID,
                "message_id": self.display_message_id,
                "updated_at": datetime.utcnow().isoformat(),
            }
            with open(EVENTS_DISPLAY_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
        except Exception:
            logger.warning("Failed to persist events display state.", exc_info=True)

    def _resolve_custom_emoji(self, guild: discord.Guild, emoji_tag: str) -> str:
        """Resolve a tag like ':name:' to '<:name:id>' if possible."""

        emoji_name = emoji_tag.strip(":")
        if not emoji_name:
            return emoji_tag

        for emoji in getattr(guild, "emojis", []):
            if emoji.name == emoji_name:
                return str(emoji)

        # Not found; return the original tag (will display as text)
        return emoji_tag

    def _format_event_title(self, guild: discord.Guild, title: str) -> str:
        """Append configured emojis after matching keywords in the title."""

        if not title or not KEYWORD_EMOJI_TAGS:
            return title

        formatted = title

        # Longer keys first to avoid partial matches.
        for keyword in sorted(KEYWORD_EMOJI_TAGS.keys(), key=len, reverse=True):
            emoji_tag = KEYWORD_EMOJI_TAGS.get(keyword)
            if not emoji_tag:
                continue

            emoji_str = self._resolve_custom_emoji(guild, emoji_tag)

            # Match keyword as a standalone token (not inside another word).
            pattern = re.compile(rf"(?<!\\w){re.escape(keyword)}(?!\\w)")

            def _repl(match: re.Match) -> str:
                return f"{match.group(0)} {emoji_str}"  # append with a space before emoji

            formatted = pattern.sub(_repl, formatted)

        return formatted

    def _build_google_calendar_url(self, event: discord.ScheduledEvent) -> Optional[str]:
        if not event.start_time:
            return None

        start_time = event.start_time.astimezone(timezone.utc)
        end_time = event.end_time.astimezone(timezone.utc) if event.end_time else start_time + timedelta(hours=2)

        params = {
            "action": "TEMPLATE",
            "text": event.name,
            "dates": f"{start_time.strftime('%Y%m%dT%H%M%SZ')}/{end_time.strftime('%Y%m%dT%H%M%SZ')}",
        }

        if event.location:
            params["location"] = self._truncate_text(event.location, 80)
        elif event.channel:
            params["location"] = f"Discord channel: #{event.channel.name}"

        return f"https://calendar.google.com/calendar/render?{urlencode(params)}"

    def _truncate_text(self, text: str, limit: int) -> str:
        if limit <= 0:
            return ""
        if len(text) <= limit:
            return text
        if limit <= 3:
            return text[:limit]
        return text[:limit - 3].rstrip() + "..."

    def _fit_event_field(self, header: str, fixed_lines: list[str], detail_text: str, limit: int) -> Optional[str]:
        if limit <= len(header) + 1:
            return None

        remaining = limit - len(header) - 1
        lines: list[str] = []

        for line in fixed_lines:
            line_len = len(line)
            extra_len = line_len if not lines else line_len + 1
            if extra_len > remaining:
                break
            lines.append(line)
            remaining -= extra_len

        if detail_text and remaining > 0:
            detail_line = self._truncate_text(detail_text, remaining if not lines else remaining - 1)
            if detail_line:
                lines.append(detail_line)

        if not lines:
            return header

        return f"{header}\n" + "\n".join(lines)

    async def _update_once(self, *, reason: str) -> None:
        async with self._update_lock:
            try:
                channel = self.bot.get_channel(EVENT_DISPLAY_CHANNEL_ID)
                if not channel:
                    logger.error(f"Channel with ID {EVENT_DISPLAY_CHANNEL_ID} not found")
                    return

                if not isinstance(channel, discord.TextChannel):
                    logger.error(f"Channel {EVENT_DISPLAY_CHANNEL_ID} is not a text channel")
                    return

                guild = channel.guild
                if not guild:
                    logger.error("Guild not found for the specified channel")
                    return

                self._target_guild_id = guild.id

                # Fetch scheduled events
                events = await guild.fetch_scheduled_events(with_counts=True)

                # Filter for only scheduled (future) or active (live) events
                filtered_events = [
                    e for e in events
                    if e.status in (discord.EventStatus.scheduled, discord.EventStatus.active)
                ]

                display_limit = min(MAX_EVENTS_TO_DISPLAY, 25)
                sorted_events = sorted(
                    filtered_events,
                    key=lambda e: e.start_time if e.start_time else datetime.max
                )[:display_limit]

                embed = await self.create_events_embed(guild, sorted_events)

                # Save all events (not just filtered ones) to JSON
                await self.save_events_to_json(events)
                await self._sync_forum_posts(guild, events)
                self._save_thread_state()

                # Edit existing display message if possible (persists across restarts)
                message: Optional[discord.Message] = None
                if self.display_message_id:
                    try:
                        message = await channel.fetch_message(self.display_message_id)
                    except discord.NotFound:
                        message = None
                    except discord.Forbidden:
                        logger.warning("No permission to fetch the existing events message; will create a new one.")
                        message = None
                    except Exception:
                        logger.warning("Failed to fetch the existing events message; will create a new one.", exc_info=True)
                        message = None

                if message is not None:
                    try:
                        await message.edit(embed=embed)
                        logger.info(f"Refreshed events display ({reason}) with {len(sorted_events)} events")
                        return
                    except discord.Forbidden:
                        logger.warning("No permission to edit the existing events message; will create a new one.")
                    except Exception:
                        logger.warning("Failed to edit the existing events message; will create a new one.", exc_info=True)

                # Fallback: send a new message and persist its id
                new_message = await channel.send(embed=embed)
                self.display_message_id = new_message.id
                self._save_display_message_id()
                logger.info(f"Posted new events display ({reason}) with {len(sorted_events)} events")

            except Exception as e:
                logger.error(f"Error updating events display: {e}", exc_info=True)

    def _debounced_refresh(self, *, delay_seconds: float = 3.0) -> None:
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
        self._debounce_task = asyncio.create_task(self._debounce_worker(delay_seconds))

    async def _debounce_worker(self, delay_seconds: float) -> None:
        try:
            await asyncio.sleep(delay_seconds)
            await self._update_once(reason="event_change")
        except asyncio.CancelledError:
            return

    @commands.Cog.listener()
    async def on_scheduled_event_create(self, scheduled_event: discord.ScheduledEvent):
        if self._target_guild_id and scheduled_event.guild_id != self._target_guild_id:
            return

        # If we haven't initialized yet (race at startup), sync once.
        if not self._thread_state.get("initialized", False):
            await self._startup_sync_threads()

        # Only create a thread once per event.
        if not self._is_event_seen(scheduled_event.id):
            await self._create_event_thread(scheduled_event)
            self._mark_event_seen(scheduled_event.id)
            self._save_thread_state()

        if scheduled_event.guild is not None:
            await self._sync_forum_posts(scheduled_event.guild, [scheduled_event])
            self._save_thread_state()

        self._debounced_refresh()

    @commands.Cog.listener()
    async def on_scheduled_event_delete(self, scheduled_event: discord.ScheduledEvent):
        if self._target_guild_id and scheduled_event.guild_id != self._target_guild_id:
            return

        state = self._thread_state.get("threads", {}).get(str(scheduled_event.id))
        if isinstance(state, dict):
            state["deleted_at"] = datetime.utcnow().isoformat()
            self._save_thread_state()

        self._debounced_refresh()

    @commands.Cog.listener()
    async def on_scheduled_event_update(self, before: discord.ScheduledEvent, after: discord.ScheduledEvent):
        guild_id = after.guild_id if after else before.guild_id
        if self._target_guild_id and guild_id != self._target_guild_id:
            return

        if after and after.guild is not None:
            await self._sync_forum_posts(after.guild, [after])
            self._save_thread_state()

        self._debounced_refresh()

    async def save_events_to_json(self, events: list[discord.ScheduledEvent]):
        """
        Save all events to a JSON file for historical tracking.
        
        Args:
            events: List of all scheduled events
        """
        try:
            # Load existing data if file exists
            existing_data = {}
            if os.path.exists(EVENTS_JSON_PATH):
                try:
                    with open(EVENTS_JSON_PATH, 'r', encoding='utf-8') as f:
                        existing_data = json.load(f)
                except json.JSONDecodeError:
                    logger.warning("Could not read existing events JSON, creating new file")
                    existing_data = {}
            
            # Update with current events
            for event in events:
                event_data = {
                    "id": event.id,
                    "name": event.name,
                    "description": event.description,
                    "start_time": event.start_time.isoformat() if event.start_time else None,
                    "end_time": event.end_time.isoformat() if event.end_time else None,
                    "status": event.status.name,
                    "location": event.location,
                    "channel_id": event.channel.id if event.channel else None,
                    "user_count": event.user_count,
                    "creator_id": event.creator_id,
                    "url": str(event.url),
                    "last_updated": datetime.utcnow().isoformat()
                }
                existing_data[str(event.id)] = event_data
            
            # Save to file
            with open(EVENTS_JSON_PATH, 'w', encoding='utf-8') as f:
                json.dump(existing_data, f, indent=2, ensure_ascii=False)
            
            logger.debug(f"Saved {len(events)} events to JSON")
            
        except Exception as e:
            logger.error(f"Error saving events to JSON: {e}", exc_info=True)

    async def create_events_embed(
        self,
        guild: discord.Guild,
        events: list[discord.ScheduledEvent]
    ) -> discord.Embed:
        """
        Create an embed displaying the scheduled events.
        
        Args:
            guild: The Discord guild
            events: List of scheduled events
            
        Returns:
            A Discord embed with event information
        """
        embed = discord.Embed(
            title=f"📅 Upcoming Events for {guild.name}",
            color=EMBED_COLOR,
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text="Last updated")

        if not events:
            embed.description = "No upcoming events scheduled."
        else:
            for event in events:
                thread_url: Optional[str] = None
                google_calendar_url = self._build_google_calendar_url(event)
                thread_info = self._thread_state.get("threads", {}).get(str(event.id))
                if isinstance(thread_info, dict):
                    thread_id = thread_info.get("thread_id")
                    if isinstance(thread_id, int):
                        thread_url = f"https://discord.com/channels/{guild.id}/{thread_id}"

                # Format the event time
                start_time_str = (
                    f"<t:{int(event.start_time.timestamp())}:F>"
                    if event.start_time
                    else "TBA"
                )

                organiser_str = "Unknown"
                if getattr(event, "creator", None):
                    organiser_str = event.creator.mention
                elif getattr(event, "creator_id", None):
                    organiser_str = f"<@{event.creator_id}>"

                # Location information
                location_str = ""
                if event.location:
                    location_str = f"\n**Server:** {event.location}"
                elif event.channel:
                    location_str = f"\n**Channel:** {event.channel.mention}"

                fixed_lines = [
                    f"**Date/Time:** {start_time_str}",
                    f"**Added By:** {organiser_str}",
                ]
                if location_str:
                    fixed_lines.append(location_str.lstrip("\n"))

                detail_line = ""

                if event.description:
                    # Check for channel mentions and URLs in description
                    # Pattern 1: <#1234567890>
                    channel_mentions = re.findall(r'<#(\d+)>', event.description)
                    # Pattern 2: https://discord.com/channels/GUILD_ID/CHANNEL_ID
                    channel_urls = re.findall(r'https?://(?:discord|discordapp)\.com/channels/\d+/(\d+)', event.description)
                    
                    # Combine all found channel IDs
                    all_channel_ids = channel_mentions + channel_urls
                    
                    if all_channel_ids:
                        # Use the first channel ID as sign-up channel
                        channel_id = int(all_channel_ids[0])
                        fixed_lines.append(f"📝 **Sign-Up Channel:** <#{channel_id}>")
                        
                        # Show rest of description (excluding channel mentions and URLs)
                        description = re.sub(r'<#\d+>', '', event.description)
                        description = re.sub(r'https?://(?:discord|discordapp)\.com/channels/\d+/\d+', '', description).strip()
                        if description:
                            description = self._truncate_text(description, 100)
                            if thread_url:
                                detail_line = f"**[Details]({thread_url})**: {description}"
                            else:
                                detail_line = f"**Details:** {description}"
                    else:
                        # No channel mention or URL, show description normally
                        description = self._truncate_text(event.description, 100)
                        if thread_url:
                            detail_line = f"**[Details]({thread_url})**: {description}"
                        else:
                            detail_line = f"**Details:** {description}"

                elif thread_url:
                    # No description, but still provide a link to the event thread.
                    fixed_lines.append(f"**[Details]({thread_url})**")

                if google_calendar_url:
                    fixed_lines.append(f"**[Add to Google Calendar]({google_calendar_url})**")

                field_name = "\u200b"
                event_title = self._truncate_text(self._format_event_title(guild, event.name), 160)
                field_header = f"📌 **[{event_title}]({event.url})**"

                remaining_embed_chars = EMBED_TOTAL_CHAR_LIMIT - len(embed) - len(field_name)
                minimum_detail_chars = len(field_header) + 1
                if remaining_embed_chars <= minimum_detail_chars:
                    break

                detail_limit = min(
                    EMBED_FIELD_VALUE_LIMIT - minimum_detail_chars,
                    remaining_embed_chars - minimum_detail_chars,
                )
                if detail_limit <= 0:
                    break

                field_body = self._fit_event_field(field_header, fixed_lines, detail_line, len(field_header) + 1 + detail_limit)
                if field_body is None:
                    break

                embed.add_field(
                    name=field_name,
                    value=field_body,
                    inline=False
                )
        
        #if guild.icon:
        #    embed.set_thumbnail(url=guild.icon.url)

        return embed

async def setup(bot: commands.Bot):
    """Load the cog."""
    await bot.add_cog(EventDisplayCog(bot))
    logger.info("EventDisplayCog loaded successfully")
