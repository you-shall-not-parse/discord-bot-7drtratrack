import asyncio
import re
import random
from typing import List, Dict, Any, Optional

import discord
from discord.ext import commands, tasks
import aiohttp

# === Configure these ===
CHANNEL_ID = 123456789012345678  # <- set your target channel ID
API_TOKEN = "04f72dfc-0e94-419f-aa28-e06cd7117fbe"  # <- your Bearer token
GIF_URLS = [
    # Add/replace with any GIF URLs you like
    "https://i.giphy.com/media/3o6Zt6ML6BklcajjsA/giphy.gif",
    "https://i.giphy.com/media/l3vR85PnGsBwu1PFK/giphy.gif",
    "https://i.giphy.com/media/26ufdipQqU2lhNA4g/giphy.gif",
]
POLL_SECONDS = 4
API_URL = "https://7dr.hlladmin.com/api/get_recent_logs?filter_action=KILL"

# Optional Tenor integration
TENOR_API_KEY = "AIzaSyAQyA7Ac_EKuMh_J_ctJn9zYpIrFn-lDcY"  # set to your Tenor API key (https://tenor.com/developer)
TENOR_CLIENT_KEY = "my_test_app"  # optional client identifier for Tenor
TENOR_SEARCH_TERMS = [
    "kill",
    "headshot",
    "frag",
    "boom",
    "action",
    "war",
    "explosion",
    "epic",
]

# Regex to remove GUIDs inside parentheses, e.g. "(Allies/xxxxxxxx...)" -> "(Allies)"
_PARENS_GUID_STRIP_RE = re.compile(r"\(([^\)/]+)\/[0-9a-fA-F]{32}\)")

def _clean_message(msg: str) -> str:
    if not msg:
        return ""
    cleaned = _PARENS_GUID_STRIP_RE.sub(r"(\1)", msg)
    return " ".join(cleaned.split())

def _extract_items(payload: Any) -> List[Dict[str, Any]]:
    # Endpoint usually returns a list; still handle some common dict-wrapped shapes defensively.
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "results", "logs"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []

def _make_key(item: Dict[str, Any]) -> str:
    # Prefer 'raw' as a unique-ish key; fallback to message + timestamp
    raw = item.get("raw")
    if raw:
        return raw
    return f"{item.get('timestamp_ms','')}|{item.get('message','')}"

async def _fetch_random_gif(session: aiohttp.ClientSession) -> Optional[str]:
    """
    Fetch a random GIF URL from Tenor using the search endpoint.
    Returns a direct GIF/MP4 URL suitable for Discord messages or None on failure.
    """
    if not TENOR_API_KEY:
        return None
    q = random.choice(TENOR_SEARCH_TERMS)
    url = "https://tenor.googleapis.com/v2/search"
    params = {
        "q": q,
        "key": TENOR_API_KEY,
        "client_key": TENOR_CLIENT_KEY,
        "limit": 1,
        "media_filter": "minimal",
        "contentfilter": "high",
    }
    try:
        async with session.get(url, params=params, timeout=10) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
    except Exception:
        return None

    results = data.get("results") or []
    if not results:
        return None

    media = results[0].get("media_formats") or {}
    for key in ("gif", "tinygif", "mediumgif", "nanogif"):
        if key in media and "url" in media[key]:
            return media[key]["url"]
    for key in ("mp4", "tinymp4", "nanomp4"):
        if key in media and "url" in media[key]:
            return media[key]["url"]

    # Fallback to top-level 'url' field if media_formats is missing
    if "url" in results[0]:
        return results[0]["url"]

    return None

class KillfeedCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        self.seen_keys = set()
        self.cold_start = True
        self.poll_kills.start()

    async def cog_load(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))

    def cog_unload(self):
        self.poll_kills.cancel()
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())

    @tasks.loop(seconds=POLL_SECONDS)
    async def poll_kills(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))

        headers = {
            "Authorization": f"Bearer {API_TOKEN}",
            "Accept": "application/json",
        }

        try:
            async with self.session.get(API_URL, headers=headers) as resp:
                if resp.status != 200:
                    # ...existing code...
                    return
                data = await resp.json(content_type=None)
        except Exception:
            # ...existing code...
            return

        items = _extract_items(data)
        if not items:
            return

        # Sort oldest -> newest, so we post in chronological order
        items.sort(key=lambda x: x.get("timestamp_ms", 0))

        new_messages: List[str] = []
        for it in items:
            key = _make_key(it)

            if self.cold_start:
                self.seen_keys.add(key)
                continue

            if key in self.seen_keys:
                continue

            self.seen_keys.add(key)

            msg = it.get("message") or it.get("line_without_time") or it.get("raw") or ""
            cleaned = _clean_message(msg)
            if cleaned:
                new_messages.append(cleaned)

        # After first pass, flip off cold start to avoid flooding on startup
        if self.cold_start:
            self.cold_start = False
            return

        if not new_messages:
            return

        # Send to channel
        try:
            channel = self.bot.get_channel(CHANNEL_ID) or await self.bot.fetch_channel(CHANNEL_ID)
        except Exception:
            return
        if not isinstance(channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel)):
            return

        for m in new_messages:
            try:
                # Try Tenor first; fallback to static list
                gif_url = await _fetch_random_gif(self.session)
                if not gif_url:
                    gif_url = random.choice(GIF_URLS) if GIF_URLS else ""
                content = f"{m}\n{gif_url}" if gif_url else m
                await channel.send(content)
            except discord.HTTPException:
                # ...existing code...
                continue

    @poll_kills.before_loop
    async def before_poll_kills(self):
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot):
    await bot.add_cog(KillfeedCog(bot))