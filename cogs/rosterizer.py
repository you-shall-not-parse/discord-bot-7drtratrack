import asyncio
import json
import os
import time
import urllib.parse
from datetime import datetime, timezone

import discord
from discord.ext import commands
import requests

# ========= CONFIG =========
TARGET_MESSAGE_ID = 1458515177438838979
OUTPUT_CHANNEL_ID = 1099806153170489485  # set to None to post in same channel
STATE_FILE = "data/rosterizer_state.json"  # stores output message id for editing
UPDATE_DEBOUNCE_SECONDS = 2.0

# CRCON API (Bearer token) — same pattern as mapvote.py
CRCON_PANEL_URL = "https://7dr.hlladmin.com/api/"
CRCON_API_KEY = os.getenv("CRCON_API_KEY")
PLAYER_LOOKUP_ENABLED = True
PLAYER_LOOKUP_MAX_PER_UPDATE = 40
PLAYER_LOOKUP_CACHE_TTL_SECONDS = 3600
VALID_REACTIONS = {
    "I": "I",
    "🇮": "I",
    "A": "A",
    "🇦": "A",
    "R": "R",
    "🇷": "R",
}
# ==========================


class ReactionReader(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ran_once = False
        self._update_task: asyncio.Task | None = None
        self._update_lock = asyncio.Lock()
        self._state = self._load_state()
        # cache: normalized_username -> (player_id_or_none, timestamp)
        self._player_id_cache: dict[str, tuple[str | None, float]] = {}

    def _load_state(self) -> dict:
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_state(self) -> None:
        os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(self._state, f, indent=2)

    def _get_state_bucket(self, guild_id: int) -> dict:
        return self._state.setdefault(str(guild_id), {})

    def _get_output_message_id(self, guild_id: int) -> int | None:
        bucket = self._get_state_bucket(guild_id)
        item = bucket.get(str(TARGET_MESSAGE_ID), {})
        msg_id = item.get("output_message_id")
        return int(msg_id) if isinstance(msg_id, int) else None

    def _set_output_message_id(self, guild_id: int, message_id: int) -> None:
        bucket = self._get_state_bucket(guild_id)
        bucket[str(TARGET_MESSAGE_ID)] = {
            "output_message_id": message_id,
            "output_channel_id": OUTPUT_CHANNEL_ID,
        }
        self._save_state()

    def _is_valid_reaction_emoji(self, emoji_str: str) -> bool:
        return emoji_str in VALID_REACTIONS

    def _normalize_discord_username(self, name: str) -> str:
        # Minimal "trimming as needed": strip and collapse internal whitespace.
        name = (name or "").strip()
        name = " ".join(name.split())
        return name

    async def _rcon_get(self, endpoint: str) -> dict:
        if not CRCON_API_KEY:
            return {"error": "CRCON_API_KEY is not set"}

        url = CRCON_PANEL_URL + endpoint

        def _do_request() -> dict:
            try:
                r = requests.get(
                    url,
                    headers={"Authorization": f"Bearer {CRCON_API_KEY}"},
                    timeout=10,
                )
                return r.json()
            except Exception as e:
                return {"error": str(e)}

        return await asyncio.to_thread(_do_request)

    def _extract_first_player_id(self, data) -> str | None:
        # Be tolerant to API shape changes: search for the first 'player_id' key.
        if isinstance(data, dict):
            if "player_id" in data and data["player_id"] is not None:
                return str(data["player_id"])
            for v in data.values():
                found = self._extract_first_player_id(v)
                if found:
                    return found
            return None
        if isinstance(data, list):
            for item in data:
                found = self._extract_first_player_id(item)
                if found:
                    return found
        return None

    async def fetch_player_id_by_discord_username(self, discord_username: str) -> str | None:
        """Fetch player_id using CRCON get_players_history by player_name.

        Uses Bearer token from CRCON_API_KEY env var.
        """

        normalized = self._normalize_discord_username(discord_username)
        if not normalized:
            return None

        now = time.time()
        cached = self._player_id_cache.get(normalized.lower())
        if cached:
            cached_id, cached_ts = cached
            if now - cached_ts <= PLAYER_LOOKUP_CACHE_TTL_SECONDS:
                return cached_id

        player_name_q = urllib.parse.quote(normalized, safe="")
        endpoint = f"get_players_history?player_name={player_name_q}&page_size=1"
        data = await self._rcon_get(endpoint)
        if not data or data.get("failed") or data.get("error"):
            self._player_id_cache[normalized.lower()] = (None, now)
            return None

        player_id = self._extract_first_player_id(data.get("result", data))
        self._player_id_cache[normalized.lower()] = (player_id, now)
        return player_id

    async def _resolve_channel(self, channel_id: int) -> discord.abc.Messageable | None:
        ch = self.bot.get_channel(channel_id)
        if ch is not None:
            return ch
        try:
            return await self.bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    async def _ensure_output_message(
        self, source_message: discord.Message
    ) -> tuple[discord.abc.Messageable, discord.Message]:
        output_channel: discord.abc.Messageable | None
        if OUTPUT_CHANNEL_ID is None:
            output_channel = source_message.channel
        else:
            output_channel = await self._resolve_channel(OUTPUT_CHANNEL_ID)
            if output_channel is None:
                output_channel = source_message.channel

        existing_id = self._get_output_message_id(source_message.guild.id)
        if existing_id is not None:
            try:
                existing = await output_channel.fetch_message(existing_id)  # type: ignore[attr-defined]
                return output_channel, existing
            except (discord.NotFound, discord.Forbidden, discord.HTTPException, AttributeError):
                pass

        placeholder = discord.Embed(
            title="Roster reactions",
            description="Preparing roster…",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        sent = await output_channel.send(
            embeds=[placeholder],
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self._set_output_message_id(source_message.guild.id, sent.id)
        return output_channel, sent

    def _format_user_line(
        self,
        user: discord.abc.User,
        member: discord.Member | None,
        player_id: str | None,
    ) -> str:
        nickname = member.display_name if member else (getattr(user, "global_name", None) or user.name)
        username = self._normalize_discord_username(user.name)
        if player_id:
            return f"- {nickname} ({username}) — {player_id}"
        return f"- {nickname} ({username})"

    def _chunk_embed_descriptions(self, text: str, max_len: int = 3900) -> list[str]:
        # Embed description max is 4096; keep some slack.
        if len(text) <= max_len:
            return [text]

        parts: list[str] = []
        buf = ""
        for line in text.split("\n"):
            proposed = (buf + "\n" + line) if buf else line
            if len(proposed) > max_len:
                if buf:
                    parts.append(buf)
                buf = line
            else:
                buf = proposed
        if buf:
            parts.append(buf)
        return parts

    async def _build_results(self, message: discord.Message) -> dict[str, list[str]]:
        results: dict[str, list[str]] = {"I": [], "A": [], "R": []}
        lookups_done = 0

        for reaction in message.reactions:
            key = VALID_REACTIONS.get(str(reaction.emoji))
            if not key:
                continue

            async for user in reaction.users():
                if getattr(user, "bot", False):
                    continue

                member = message.guild.get_member(user.id)
                if member is None:
                    try:
                        member = await message.guild.fetch_member(user.id)
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        member = None

                player_id: str | None = None
                if PLAYER_LOOKUP_ENABLED and lookups_done < PLAYER_LOOKUP_MAX_PER_UPDATE:
                    player_id = await self.fetch_player_id_by_discord_username(user.name)
                    lookups_done += 1

                line = self._format_user_line(user, member, player_id)
                if line not in results[key]:
                    results[key].append(line)

        return results

    def _build_embeds(self, guild: discord.Guild, results: dict[str, list[str]]) -> list[discord.Embed]:
        blocks: list[str] = []
        for key in ["I", "A", "R"]:
            blocks.append(f"**{key} ({len(results[key])})**")
            if results[key]:
                blocks.extend(results[key])
            else:
                blocks.append("- None")
            blocks.append("")

        body = "\n".join(blocks).strip()
        pages = self._chunk_embed_descriptions(body)

        embeds: list[discord.Embed] = []
        now = datetime.now(timezone.utc)
        for i, page in enumerate(pages):
            e = discord.Embed(
                title="Roster reactions" if i == 0 else None,
                description=page,
                color=discord.Color.blurple(),
                timestamp=now,
            )
            if i == 0:
                e.set_footer(text=f"Updated • {guild.name}")
            embeds.append(e)

        return embeds[:10]  # Discord allows up to 10 embeds per message

    async def _update_from_message(self, message: discord.Message) -> None:
        async with self._update_lock:
            output_channel, output_message = await self._ensure_output_message(message)
            results = await self._build_results(message)
            embeds = self._build_embeds(message.guild, results)

            try:
                await output_message.edit(
                    content=None,
                    embeds=embeds,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.Forbidden:
                print(
                    f"Missing permission to edit/send in channel {getattr(output_channel, 'id', None)}. "
                    f"Check the bot's permissions and channel overrides."
                )
            except discord.HTTPException as e:
                print(f"Failed to update roster embed due to HTTPException: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        if self._ran_once:
            return
        self._ran_once = True

        print("ReactionReader loaded — running one-time scan")
        await self.run_once()

    def _schedule_update(self, guild_id: int, channel_id: int) -> None:
        if self._update_task and not self._update_task.done():
            self._update_task.cancel()

        async def _runner() -> None:
            try:
                await asyncio.sleep(UPDATE_DEBOUNCE_SECONDS)
                channel = await self._resolve_channel(channel_id)
                if channel is None:
                    print(f"Cannot resolve channel for reaction update: {channel_id}")
                    return

                # fetch_message is only on TextChannel/Thread/etc; guard with getattr
                fetch_message = getattr(channel, "fetch_message", None)
                if fetch_message is None:
                    print(f"Channel does not support fetch_message: {channel_id}")
                    return

                msg = await fetch_message(TARGET_MESSAGE_ID)
                await self._update_from_message(msg)
            except asyncio.CancelledError:
                return
            except discord.Forbidden:
                print("Forbidden while fetching message for reaction update (missing view/history perms)")
            except (discord.NotFound, discord.HTTPException) as e:
                print(f"Failed to fetch/update message for reaction update: {e}")

        self._update_task = asyncio.create_task(_runner())

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.message_id != TARGET_MESSAGE_ID:
            return
        if not self._is_valid_reaction_emoji(str(payload.emoji)):
            return
        if payload.guild_id is None:
            return
        self._schedule_update(payload.guild_id, payload.channel_id)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.message_id != TARGET_MESSAGE_ID:
            return
        if not self._is_valid_reaction_emoji(str(payload.emoji)):
            return
        if payload.guild_id is None:
            return
        self._schedule_update(payload.guild_id, payload.channel_id)

    @commands.Cog.listener()
    async def on_raw_reaction_clear(self, payload: discord.RawReactionClearEvent):
        if payload.message_id != TARGET_MESSAGE_ID:
            return
        if payload.guild_id is None:
            return
        self._schedule_update(payload.guild_id, payload.channel_id)

    @commands.Cog.listener()
    async def on_raw_reaction_clear_emoji(self, payload: discord.RawReactionClearEmojiEvent):
        if payload.message_id != TARGET_MESSAGE_ID:
            return
        if payload.guild_id is None:
            return
        self._schedule_update(payload.guild_id, payload.channel_id)

    async def run_once(self):
        message = None

        # Find the message in all guilds/channels the bot can see
        for guild in self.bot.guilds:
            for channel in guild.text_channels:
                try:
                    message = await channel.fetch_message(TARGET_MESSAGE_ID)
                    if message:
                        break
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    continue
            if message:
                break

        if not message:
            print("Target message not found")
            return

        await self._update_from_message(message)

        print("ReactionReader complete — unload when ready")


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionReader(bot))
