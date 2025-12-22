import discord
from discord.ext import commands, tasks

import aiohttp
import asyncio
import json
import os
import random
from datetime import datetime, time, timedelta, timezone
from xml.etree import ElementTree

# ================== CONFIG ==================

POST_TIME_UTC = time(hour=1, minute=15, tzinfo=timezone.utc)

CHECK_INTERVAL_MINUTES = 30   # how often RSS feeds are checked

DATA_DIR = "data"

CREATORS = [
    {
        "name": "WarStoriesChannel",
        "channel_id": "UC3fOzMSxcmCXZLmAM9vy1IQ",
        "post_to": 1099806153170489485  # <-- Discord channel ID
    },
    {
        "name": "YarnHub",
        "channel_id": "UC-f2WBfSCZiu0bOBydjot3w",
        "post_to": 1099806153170489485
    }
]

REPOST_COOLDOWN_DAYS = 7

# ============================================

KNOWN_VIDEOS_FILE = os.path.join(DATA_DIR, "yt_known_videos.json")
LAST_SEEN_FILE = os.path.join(DATA_DIR, "yt_last_seen.json")
LAST_POSTED_FILE = os.path.join(DATA_DIR, "yt_last_posted.json")


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


class YouTubeFeed(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        self.known_videos = load_json(KNOWN_VIDEOS_FILE, [])
        self.last_seen = load_json(LAST_SEEN_FILE, {})
        self.last_posted = load_json(LAST_POSTED_FILE, {})

        self.check_feeds.start()
        self.daily_post.start()

    def cog_unload(self):
        self.check_feeds.cancel()
        self.daily_post.cancel()

    # ---------------- RSS CHECK LOOP ----------------

    @tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
    async def check_feeds(self):
        async with aiohttp.ClientSession() as session:
            for creator in CREATORS:
                await self.fetch_creator_feed(session, creator)

        save_json(KNOWN_VIDEOS_FILE, self.known_videos)
        save_json(LAST_SEEN_FILE, self.last_seen)

    async def fetch_creator_feed(self, session, creator):
        rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={creator['channel_id']}"

        async with session.get(rss_url) as resp:
            if resp.status != 200:
                return

            text = await resp.text()

        root = ElementTree.fromstring(text)
        ns = {"yt": "http://www.youtube.com/xml/schemas/2015"}

        entries = root.findall("entry", ns)
        if not entries:
            return

        latest_entry = entries[0]
        video_id = latest_entry.find("yt:videoId", ns).text
        video_url = f"https://www.youtube.com/watch?v={video_id}"

        if self.last_seen.get(creator["channel_id"]) == video_id:
            return

        self.last_seen[creator["channel_id"]] = video_id

        if video_url not in [v["url"] for v in self.known_videos]:
            self.known_videos.append({
                "creator": creator["name"],
                "channel_id": creator["channel_id"],
                "url": video_url,
                "added_at": datetime.now(timezone.utc).isoformat(),
                "post_to": creator["post_to"]
            })

    # ---------------- DAILY POST LOOP ----------------

    @tasks.loop(time=POST_TIME_UTC)
    async def daily_post(self):
        now = datetime.now(timezone.utc)

        # videos not posted recently
        eligible = []
        for v in self.known_videos:
            last_time = self.last_posted.get(v["url"])
            if not last_time:
                eligible.append(v)
                continue

            last_dt = datetime.fromisoformat(last_time)
            if now - last_dt >= timedelta(days=REPOST_COOLDOWN_DAYS):
                eligible.append(v)

        if not eligible:
            return

        # prefer newest unposted
        eligible.sort(key=lambda x: x["added_at"], reverse=True)
        video = eligible[0]

        channel = self.bot.get_channel(video["post_to"])
        if not channel:
            return

        await channel.send(
            f"📺 **{video['creator']}**\n{video['url']}"
        )

        self.last_posted[video["url"]] = now.isoformat()
        save_json(LAST_POSTED_FILE, self.last_posted)

    @check_feeds.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    @daily_post.before_loop
    async def before_post(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(YouTubeFeed(bot))
