from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
from pathlib import Path

import discord

from cogs.wardiary import STATE_PATH, SUBMISSION_POST_NAME, WAR_DIARY_FORUM_CHANNEL_ID


DATE_RE = re.compile(r"\*\*Date:\*\*\s*(.+)", re.IGNORECASE)
MAP_RE = re.compile(r"\*\*Map:\*\*\s*(.+)", re.IGNORECASE)
CONTENT_DATE_RE = re.compile(r"^Match date:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
MARKDOWN_LINK_RE = re.compile(r"\((https?://[^)]+)\)")
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

DEFAULT_POST_CHANNEL_ID = 1279831955935854712


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export war diary forum posts to CSV.")
    parser.add_argument("--out", default="wardiary_export.csv", help="Output CSV path.")
    parser.add_argument("--forum-id", type=int, default=WAR_DIARY_FORUM_CHANNEL_ID, help="Discord forum channel ID.")
    parser.add_argument("--token-env", default="DISCORD_BOT_TOKEN", help="Environment variable containing the Discord bot token.")
    parser.add_argument("--post-channel-id", type=int, default=DEFAULT_POST_CHANNEL_ID, help="Discord channel ID to upload the CSV into.")
    parser.add_argument("--message", default="War diary CSV export", help="Message to send with the uploaded CSV.")
    return parser.parse_args()


def load_submission_thread_id() -> int | None:
    state_path = Path(STATE_PATH)
    if not state_path.exists():
        return None
    try:
        with state_path.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
    except Exception:
        return None
    value = state.get("submission_thread_id") if isinstance(state, dict) else None
    try:
        return int(value)
    except Exception:
        return None


def parse_match_date(starter: discord.Message | None) -> str:
    embed = starter.embeds[0] if starter and starter.embeds else None
    if embed and embed.description:
        match = DATE_RE.search(embed.description)
        if match:
            return match.group(1).strip()
    if starter and starter.content:
        match = CONTENT_DATE_RE.search(starter.content)
        if match:
            return match.group(1).strip()
    return ""


def parse_map(thread: discord.Thread, starter: discord.Message | None, submission_thread_id: int | None) -> str:
    embed = starter.embeds[0] if starter and starter.embeds else None
    if embed and embed.description:
        match = MAP_RE.search(embed.description)
        if match:
            return match.group(1).strip()
    for tag in thread.applied_tags:
        name = (tag.name or "").strip()
        if not name:
            continue
        if submission_thread_id is not None and thread.id == submission_thread_id:
            continue
        return name
    return ""


def parse_stats_url(starter: discord.Message | None) -> str:
    if not starter or not starter.embeds:
        return ""
    for embed in starter.embeds:
        for field in embed.fields:
            if (field.name or "").strip().casefold() != "stats link":
                continue
            value = (field.value or "").strip()
            markdown_match = MARKDOWN_LINK_RE.search(value)
            if markdown_match:
                return markdown_match.group(1).strip()
            url_match = URL_RE.search(value)
            if url_match:
                return url_match.group(0).strip()
    return ""


async def fetch_starter_message(thread: discord.Thread) -> discord.Message | None:
    try:
        return await thread.fetch_message(thread.id)
    except Exception:
        return None


async def gather_threads(forum: discord.ForumChannel) -> list[discord.Thread]:
    seen: dict[int, discord.Thread] = {}
    for thread in forum.threads:
        seen[thread.id] = thread
    async for thread in forum.archived_threads(limit=None):
        seen[thread.id] = thread
    return list(seen.values())


async def resolve_upload_channel(client: discord.Client, channel_id: int) -> discord.abc.Messageable:
    channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
    if isinstance(channel, (discord.TextChannel, discord.Thread, discord.DMChannel, discord.GroupChannel)):
        return channel
    raise RuntimeError(f"Channel {channel_id} cannot accept file uploads")


async def export_csv(
    *,
    token: str,
    forum_id: int,
    output_path: Path,
    post_channel_id: int | None = None,
    message: str = "War diary CSV export",
) -> tuple[int, int]:
    intents = discord.Intents.none()
    intents.guilds = True
    client = discord.Client(intents=intents)
    ready_event = asyncio.Event()
    result: dict[str, int] = {"written": 0, "seen": 0}

    @client.event
    async def on_ready() -> None:
        submission_thread_id = load_submission_thread_id()
        try:
            channel = client.get_channel(forum_id) or await client.fetch_channel(forum_id)
            if not isinstance(channel, discord.ForumChannel):
                raise RuntimeError(f"Channel {forum_id} is not a forum channel")

            threads = await gather_threads(channel)
            rows: list[dict[str, str]] = []
            for thread in sorted(threads, key=lambda item: item.created_at or discord.utils.snowflake_time(item.id)):
                result["seen"] += 1
                if thread.name.strip() == SUBMISSION_POST_NAME:
                    continue
                if submission_thread_id is not None and thread.id == submission_thread_id:
                    continue

                starter = await fetch_starter_message(thread)
                rows.append(
                    {
                        "match_date": parse_match_date(starter),
                        "map": parse_map(thread, starter, submission_thread_id),
                        "midpoint": "",
                        "faction": "",
                        "stats_url": parse_stats_url(starter),
                    }
                )

            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["match_date", "map", "midpoint", "faction", "stats_url"])
                writer.writeheader()
                writer.writerows(rows)
            result["written"] = len(rows)

            if post_channel_id is not None:
                upload_channel = await resolve_upload_channel(client, post_channel_id)
                await upload_channel.send(
                    content=message,
                    file=discord.File(str(output_path), filename=output_path.name),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        finally:
            ready_event.set()
            await client.close()

    await client.start(token)
    await ready_event.wait()
    return result["written"], result["seen"]


def main() -> None:
    args = parse_args()
    token = os.getenv(args.token_env, "").strip()
    if not token:
        raise SystemExit(f"Environment variable {args.token_env} is not set")
    written, seen = asyncio.run(
        export_csv(
            token=token,
            forum_id=args.forum_id,
            output_path=Path(args.out),
            post_channel_id=args.post_channel_id,
            message=args.message,
        )
    )
    print(f"Wrote {written} rows to {args.out} from {seen} forum threads scanned.")
    if args.post_channel_id is not None:
        print(f"Uploaded {args.out} to channel {args.post_channel_id}.")
    if written:
        print("midpoint and faction are left blank because war diary does not store them today.")


if __name__ == "__main__":
    main()