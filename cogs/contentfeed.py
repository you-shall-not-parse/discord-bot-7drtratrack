import discord
from discord.ext import commands, tasks
from discord import app_commands
import logging
import aiohttp
import json
import os
import random
from datetime import datetime, time, timezone
from xml.etree import ElementTree

# ================== CONFIG ==================

POST_TIME_UTC = time(hour=12, minute=0, tzinfo=timezone.utc)

CHECK_INTERVAL_MINUTES = 30   # how often RSS feeds are checked

# Use an absolute data directory so the bot doesn't depend on the process working directory.
# (Fixes cases where yt_*.json are created somewhere else, causing repeated posts.)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

CREATORS = [
    {
        "name": "YarnHub",
        "channel_id": "UC-f2WBfSCZiu0bOBydjot3w",
        "post_to": 1106900027659522108
    },
    {
        "name": "TheIntelReport",
        "channel_id": "UC7Ay_bxnYWSS9ZDPpqAE1RQ",
        "post_to": 1106900027659522108
    },
    {
        "name": "OculusImperia",
        "channel_id": "UC8AaO8zkIoxbUp1_p0rl13g",
        "post_to": 1399102943004721224
    }
]

# How many videos to keep per creator in the local pool.
MAX_VIDEOS_PER_CREATOR = 50

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
        # Create the file so state persists across restarts.
        save_json(path, default)
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        # If the file is corrupt/empty, reset it.
        save_json(path, default)
        return default


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

        # Remove any videos/entries from creators no longer configured
        self.prune_removed_creators()

        # Ensure all stored videos point at the currently configured target channels.
        # (Prevents stale persisted `post_to` values from sending content to the wrong channel.)
        self._normalize_video_targets()

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

            # Track the newest video we've seen for this creator (helps reduce churn).
            newest_vid_el = entries[0].find("yt:videoId", ns)
            if newest_vid_el is not None and newest_vid_el.text:
                self.last_seen[creator["channel_id"]] = newest_vid_el.text

            # Build a quick lookup to avoid O(n) scans for every entry.
            known_urls = {v.get("url") for v in self.known_videos}

            # Add multiple recent entries (not only the newest), so random selection has a pool.
            for entry in entries:
                vid_el = entry.find("yt:videoId", ns)
                if vid_el is None or not vid_el.text:
                    continue
                video_id = vid_el.text
                video_url = f"https://www.youtube.com/watch?v={video_id}"

                if video_url in known_urls:
                    continue

                published_el = entry.find("atom:published", ns)
                added_at = datetime.now(timezone.utc).isoformat()
                if published_el is not None and published_el.text:
                    try:
                        # YouTube RSS uses RFC3339 like: 2025-12-23T12:34:56+00:00 or ...Z
                        ts = published_el.text.replace("Z", "+00:00")
                        added_at = datetime.fromisoformat(ts).astimezone(timezone.utc).isoformat()
                    except Exception:
                        pass

                self.known_videos.append({
                    "creator": creator["name"],
                    "channel_id": creator["channel_id"],
                    "url": video_url,
                    "added_at": added_at,
                    "post_to": creator["post_to"]
                })
                known_urls.add(video_url)

            # Cap stored videos per creator so the pool doesn't grow forever.
            creator_videos = [v for v in self.known_videos if v.get("channel_id") == creator["channel_id"]]
            others = [v for v in self.known_videos if v.get("channel_id") != creator["channel_id"]]
            creator_videos.sort(key=lambda x: x.get("added_at", ""), reverse=True)
            self.known_videos = others + creator_videos[:MAX_VIDEOS_PER_CREATOR]
        except Exception as e:
            logger.error(f"Error parsing RSS for {creator['name']}: {e}")

    # ---------------- DAILY POST LOOP ----------------

    @tasks.loop(time=POST_TIME_UTC)
    async def daily_post(self):
        logger.info(f"Daily post task fired at {datetime.now(timezone.utc)}")

        videos_by_channel = self._videos_by_target_channel()
        
        if not videos_by_channel:
            logger.warning("No videos available for any channel")
            return
        
        # For each channel, select and post one eligible video
        for channel_id, videos in videos_by_channel.items():
            video = self._select_eligible_video_from_pool(videos)
            if not video:
                logger.warning(f"No eligible video for channel {channel_id}. Total: {len(videos)}, Posted: {len([v for v in videos if self.last_posted.get(v['url'])])}")
                continue
            logger.info(f"Selected video for channel {channel_id}: {video['url']} from {video['creator']}")
            await self._post_video(video)

    @check_feeds.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    @daily_post.before_loop
    async def before_post(self):
        await self.bot.wait_until_ready()

    # ---------------- Helpers ----------------

    def _select_eligible_video_from_pool(self, videos):
        """Pick a random video from the provided pool (per-channel).

        This intentionally ignores cooldown/eligibility and instead focuses on variety.
        It will try to avoid immediately re-posting the most recently posted URL in this pool.
        """
        if not videos:
            return None

        # Try to avoid the most-recently-posted URL in this pool (helps when spamming /forcecontent).
        most_recent_url = None
        most_recent_dt = None
        for v in videos:
            url = v.get("url")
            ts = self.last_posted.get(url)
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts)
            except Exception:
                continue
            if most_recent_dt is None or dt > most_recent_dt:
                most_recent_dt = dt
                most_recent_url = url

        if most_recent_url and len(videos) > 1:
            candidates = [v for v in videos if v.get("url") != most_recent_url]
            if candidates:
                return random.choice(candidates)

        return random.choice(videos)

    def _select_eligible_video(self):
        """Select an eligible video from all known videos (for backward compatibility)."""
        return self._select_eligible_video_from_pool(self.known_videos)

    def _videos_by_target_channel(self):
        videos_by_channel = {}
        post_to_by_creator_channel = {c["channel_id"]: c["post_to"] for c in CREATORS}

        for video in self.known_videos:
            creator_channel_id = video.get("channel_id")
            post_to = post_to_by_creator_channel.get(creator_channel_id)
            if post_to is None:
                continue

            # Keep video dict consistent with current config.
            video["post_to"] = post_to
            videos_by_channel.setdefault(post_to, []).append(video)

        return videos_by_channel

    def _normalize_video_targets(self):
        """Rewrite stored video 'post_to' fields to match current CREATORS config."""
        post_to_by_creator_channel = {c["channel_id"]: c["post_to"] for c in CREATORS}
        changed = False
        for video in self.known_videos:
            creator_channel_id = video.get("channel_id")
            post_to = post_to_by_creator_channel.get(creator_channel_id)
            if post_to is None:
                continue
            if video.get("post_to") != post_to:
                video["post_to"] = post_to
                changed = True
        if changed:
            save_json(KNOWN_VIDEOS_FILE, self.known_videos)

    def prune_removed_creators(self):
        """Drop persisted videos/state for creators no longer in CREATORS."""
        allowed_channels = {c["channel_id"] for c in CREATORS}

        # Filter known videos
        before_known = len(self.known_videos)
        self.known_videos = [v for v in self.known_videos if v.get("channel_id") in allowed_channels]

        # Filter last_seen (keyed by channel_id)
        before_seen = len(self.last_seen)
        self.last_seen = {k: v for k, v in self.last_seen.items() if k in allowed_channels}

        # Filter last_posted (keyed by video URL) to only keep videos still in known_videos
        valid_urls = {v["url"] for v in self.known_videos}
        before_posted = len(self.last_posted)
        self.last_posted = {k: v for k, v in self.last_posted.items() if k in valid_urls}

        # Normalize targets after filtering (covers config changes).
        post_to_by_creator_channel = {c["channel_id"]: c["post_to"] for c in CREATORS}
        for video in self.known_videos:
            creator_channel_id = video.get("channel_id")
            post_to = post_to_by_creator_channel.get(creator_channel_id)
            if post_to is not None:
                video["post_to"] = post_to

        if (before_known != len(self.known_videos)) or (before_seen != len(self.last_seen)) or (before_posted != len(self.last_posted)):
            logger.info(
                f"Pruned removed creators: known {before_known}->{len(self.known_videos)}, "
                f"last_seen {before_seen}->{len(self.last_seen)}, last_posted {before_posted}->{len(self.last_posted)}"
            )
            save_json(KNOWN_VIDEOS_FILE, self.known_videos)
            save_json(LAST_SEEN_FILE, self.last_seen)
            save_json(LAST_POSTED_FILE, self.last_posted)

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
    @app_commands.command(name="forcecontent", description="Force-post the latest eligible content video to all channels")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def forcecontent(self, interaction: discord.Interaction):
        user = interaction.user
        if not isinstance(user, discord.Member):
            return await interaction.response.send_message("This command is only usable in a server.", ephemeral=True)
        if CONTENT_ADMIN_ROLE_ID not in [r.id for r in user.roles]:
            return await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)

        # Refresh feeds now (so /forcecontent doesn't depend on the 30-minute loop).
        # Defer to avoid Discord's interaction timeout during network fetches.
        await interaction.response.defer(ephemeral=True)
        try:
            async with aiohttp.ClientSession() as session:
                for creator in CREATORS:
                    await self.fetch_creator_feed(session, creator)
            save_json(KNOWN_VIDEOS_FILE, self.known_videos)
            save_json(LAST_SEEN_FILE, self.last_seen)
        except Exception as e:
            logger.error(f"Forcecontent RSS refresh failed: {e}")

        videos_by_channel = self._videos_by_target_channel()
        
        if not videos_by_channel:
            return await interaction.followup.send("No videos available for any channel.")
        
        posted_videos = []
        # For each channel, select and post one eligible video
        for channel_id, videos in videos_by_channel.items():
            video = self._select_eligible_video_from_pool(videos)
            if video:
                await self._post_video(video)
                posted_videos.append(f"{video['creator']} to <#{channel_id}>")
        
        if not posted_videos:
            return await interaction.followup.send("No videos available to post right now.")
        
        summary = "\n".join(posted_videos)
        await interaction.followup.send(f"Posted videos:\n{summary}")


async def setup(bot):
    await bot.add_cog(YouTubeFeed(bot))
