import discord
from discord.ext import commands, tasks
from discord import app_commands
import logging

import aiohttp
import asyncio
import json
import os
import random
from datetime import datetime, time, timedelta, timezone
from xml.etree import ElementTree

# ================== CONFIG ==================

POST_TIME_UTC = time(hour=17, minute=51, tzinfo=timezone.utc)

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

# Guild and role configuration for slash command access
GUILD_ID = 1097913605082579024  # Replace with your guild ID
CONTENT_ADMIN_ROLE_ID = 1213495462632361994  # Replace with role ID permitted to use /forcecontent

logger = logging.getLogger("YouTubeFeed")
logger.setLevel(logging.INFO)


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
        try:
            async with session.get(rss_url) as resp:
                if resp.status != 200:
                    logger.warning(f"RSS fetch failed for {creator['name']} (status {resp.status})")
                    return
                text = await resp.text()

            root = ElementTree.fromstring(text)
            ns = {
                "atom": "http://www.w3.org/2005/Atom",
                "yt": "http://www.youtube.com/xml/schemas/2015"
            }

            entries = root.findall("atom:entry", ns)
            if not entries:
                return

            latest_entry = entries[0]
            vid_el = latest_entry.find("yt:videoId", ns)
            if vid_el is None or not vid_el.text:
                return
            video_id = vid_el.text
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
        except Exception as e:
            logger.error(f"Error parsing RSS for {creator['name']}: {e}")

    # ---------------- DAILY POST LOOP ----------------

    @tasks.loop(time=POST_TIME_UTC)
    async def daily_post(self):
        logger.info(f"Daily post task fired at {datetime.now(timezone.utc)}")
        video = self._select_eligible_video()
        if not video:
            logger.warning(f"No eligible videos to post. Total videos: {len(self.known_videos)}, Last posted: {self.last_posted}")
            return
        logger.info(f"Selected video: {video['url']} from {video['creator']}")
        await self._post_video(video)

    @check_feeds.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    @daily_post.before_loop
    async def before_post(self):
        await self.bot.wait_until_ready()

    # ---------------- Helpers ----------------

    def _select_eligible_video(self):
        now = datetime.now(timezone.utc)
        eligible = []
        for v in self.known_videos:
            last_time = self.last_posted.get(v["url"])
            if not last_time:
                eligible.append(v)
                continue
            try:
                last_dt = datetime.fromisoformat(last_time)
            except Exception:
                last_dt = now - timedelta(days=365)
            if now - last_dt >= timedelta(days=REPOST_COOLDOWN_DAYS):
                eligible.append(v)
        logger.debug(f"Video eligibility: {len(self.known_videos)} total, {len(eligible)} eligible for posting")
        if not eligible:
            # Fallback: pick a random video from all known videos
            if self.known_videos:
                logger.info(f"No eligible videos, picking random from {len(self.known_videos)} total videos")
                return random.choice(self.known_videos)
            return None
        eligible.sort(key=lambda x: x["added_at"], reverse=True)
        return eligible[0]

    async def _post_video(self, video):
        now = datetime.now(timezone.utc)
        channel = self.bot.get_channel(video["post_to"])
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(video["post_to"])
            except Exception as e:
                logger.error(f"Unable to fetch channel {video['post_to']}: {e}")
                return
        try:
            await channel.send(f"📺 **{video['creator']}**\n{video['url']}")
            self.last_posted[video["url"]] = now.isoformat()
            save_json(LAST_POSTED_FILE, self.last_posted)
            logger.info(f"Successfully posted video: {video['url']} at {now}")
        except discord.Forbidden:
            logger.error("Missing permissions to post in target channel")
        except discord.HTTPException as e:
            logger.error(f"HTTP error posting content: {e}")

    # ---------------- Slash Command ----------------
    @app_commands.command(name="forcecontent", description="Force-post the latest eligible content video")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def forcecontent(self, interaction: discord.Interaction):
        user = interaction.user
        if not isinstance(user, discord.Member):
            return await interaction.response.send_message("This command is only usable in a server.", ephemeral=True)
        if CONTENT_ADMIN_ROLE_ID not in [r.id for r in user.roles]:
            return await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)

        video = self._select_eligible_video()
        if not video:
            return await interaction.response.send_message("No eligible videos to post right now.", ephemeral=True)
        await self._post_video(video)
        await interaction.response.send_message(f"Posted: {video['url']} from {video['creator']}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(YouTubeFeed(bot))
