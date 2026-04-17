import asyncio
import re
import random
from typing import List, Dict, Any, Optional

import discord
from discord.ext import commands, tasks
import aiohttp

# === Configure these ===
CHANNEL_ID = 1446627459863937064  # <- set your target channel ID
CRCON_API_KEY = os.getenv("CRCON_API_KEY")
GIF_URLS = [
    # Add/replace with any GIF URLs you like
    "https://media.tenor.com/iIQj7WAkiyQAAAAd/jon-hamm-jonhamm.gif"
]
POLL_SECONDS = 4
API_URL = "https://7dr.hlladmin.com/api/get_recent_logs?filter_action=KILL"

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
            "Authorization": f"Bearer {CRCON_API_KEY}",
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
        # Accept any channel-like object that supports .send()
        if not hasattr(channel, "send"):
            return

        for m in new_messages:
            try:
                # Use only the static GIF_URLS list (no Tenor)
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