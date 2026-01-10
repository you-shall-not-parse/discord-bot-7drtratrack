import asyncio
import json
import os
import re
import time
import urllib.parse
import unicodedata
from datetime import datetime, timezone

import discord
from discord.ext import commands
import requests

# ========= CONFIG =========
TARGET_MESSAGE_ID = 1458515177438838979
OUTPUT_CHANNEL_ID = 1099806153170489485  # set to None to post in same channel
STATE_FILE = "data/rosterizer_state.json"  # stores output message id for editing
UPDATE_DEBOUNCE_SECONDS = 2.0
INCLUDE_HLLRECORDS_LINK = False  # If True, hyperlink names to hllrecords.com when player_id is available

# CRCON API (Bearer token) — same pattern as mapvote.py
CRCON_PANEL_URL = "https://7dr.hlladmin.com/api/"
CRCON_API_KEY = os.getenv("CRCON_API_KEY")
PLAYER_LOOKUP_ENABLED = True
PLAYER_LOOKUP_MAX_PER_UPDATE = 120
PLAYER_LOOKUP_CACHE_TTL_SECONDS = 3600
PLAYER_LOOKUP_NEGATIVE_CACHE_TTL_SECONDS = 120

# Common rank prefixes seen in Discord nicknames (used only for CRCON lookup candidates)
RANK_PREFIXES = ["Field Marshal", "FM", "General", "Gen",
    "Lieutenant General", "Lt Gen", "Lt.Gen", "LtGen",
    "Major General", "Maj Gen", "Maj.Gen", "MajGen",
    "Brigadier", "Brig", "Colonel", "Col",
    "Lieutenant Colonel", "Lt Col", "Lt.Col", "LtCol",
    "Major", "Maj", "Captain", "Cpt", "Lieutenant", "Lt", "Lt.",
    "2nd Lieutenant", "2Lt", "2ndLt", "2 Lt",
    "Regimental Sergeant Major", "RSM", "WO1", "WO2",
    "Warrant Officer", "Warrant Officer",
    "Sergeant major", "SGM", "Staff Sergeant", "SSG",
    "Sergeant", "Sgt", "Corporal", "Cpl",
    "L.Cpl", "LCpl", "L Cpl", "Private", "Pte", "Recruit"]

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
        # cache: normalized_name (lower) -> (player_id_or_none, timestamp)
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

    def _strip_unicode_noise(self, text: str) -> str:
        # Remove combining marks (Mn/Me) and formatting chars (Cf) that often appear in styled nicknames.
        # Example: "⁶̆⁵̤̓ᵗ̧̘ʰ͉͜" should collapse closer to "65th".
        decomposed = unicodedata.normalize("NFKD", text)
        cleaned = "".join(
            ch
            for ch in decomposed
            if unicodedata.category(ch) not in {"Mn", "Me", "Cf", "Cc"}
        )
        return unicodedata.normalize("NFKC", cleaned)

    def _normalize_rank_dot(self, text: str) -> str:
        # Handle names like "Pte.Timekeeper" or "Cpl.Zaar" by inserting a space after a rank token.
        # Only applies when the string starts with a known rank and a dot immediately follows.
        t = text.strip()
        # Common short rank tokens where this occurs.
        rank_tokens = ["Pte", "Cpl", "Sgt", "SSG", "SGM", "RSM", "Lt", "Cpt", "WO1", "WO2"]
        for rt in rank_tokens:
            prefix = rt + "."
            if t.lower().startswith(prefix.lower()) and len(t) > len(prefix) and t[len(prefix)] != " ":
                return rt + " " + t[len(prefix):]
        return text

    def _strip_trailing_ordinal(self, text: str) -> str:
        # Remove suffixes like "65th" / "5th" when they appear at the very end.
        return re.sub(r"\s*\d{1,3}(st|nd|rd|th)$", "", text, flags=re.IGNORECASE).strip()

    def _build_simple_variants(self, text: str) -> list[str]:
        """Build a small set of safe variants for matching player_name.

        This is intentionally limited to avoid exploding the number of HTTP calls.
        """

        text = (text or "").strip()
        if not text:
            return []

        variants: list[str] = [text]

        # Underscore <-> space (common difference between Discord and in-game names)
        if "_" in text:
            variants.append(text.replace("_", " "))
        if " " in text:
            variants.append(text.replace(" ", "_"))

        # Hyphen <-> space
        if "-" in text:
            variants.append(text.replace("-", " "))
        if " " in text:
            variants.append(text.replace(" ", "-"))

        # De-dupe while preserving order
        out: list[str] = []
        seen: set[str] = set()
        for v in variants:
            vv = " ".join(v.split())
            if not vv:
                continue
            key = vv.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(vv)
        return out

    def _normalize_discord_username(self, name: str, *, strip_rank_prefix: bool = False) -> str:
        # "Trimming as needed":
        # - strip leading/trailing whitespace
        # - collapse internal whitespace
        # - normalize unicode so things like superscripts become plain text
        # - strip old-style Discord discriminator tokens like "#1579" / "#0" even if followed by suffixes
        # - (optional) strip leading rank prefix for lookup fallback
        name = (name or "").strip()
        name = unicodedata.normalize("NFKC", name)
        name = self._strip_unicode_noise(name)
        # Replace common separators that can appear "between" tokens.
        name = name.replace("%", " ")
        name = " ".join(name.split())
        # Remove the last "#<digits>" occurrence (Discord discriminator), but keep any suffix after it.
        # Examples:
        # - "Cpt Crump#1597" -> "Cpt Crump"
        # - "Sgt Charlie#8001⁵ᵗʰ" (after NFKC) -> "Sgt Charlie5th" (then remove "#8001" -> "Sgt Charlie5th")
        matches = list(re.finditer(r"\s*#\d{1,6}", name))
        if matches:
            m = matches[-1]
            name = (name[: m.start()] + name[m.end() :]).strip()

        if strip_rank_prefix:
            # Strip leading rank prefix (lookup-only fallback).
            # Only strips when it is a standalone token followed by whitespace.
            rank_pat = r"^(?:" + "|".join(re.escape(r) for r in RANK_PREFIXES) + r")\.?\s+"
            name = re.sub(rank_pat, "", name, flags=re.IGNORECASE).strip()
        return name

    def _escape_for_embed(self, text: str) -> str:
        # Escape markdown and mentions to avoid formatting issues (e.g. underscores, brackets).
        text = discord.utils.escape_mentions(text)
        text = discord.utils.escape_markdown(text, as_needed=False)
        # Also escape link-ish characters that aren't covered by escape_markdown.
        text = text.replace("[", "\\[").replace("]", "\\]")
        text = text.replace("(", "\\(").replace(")", "\\)")
        return text

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

    async def _fetch_player_id_cached(self, player_name: str, *, strip_rank_prefix: bool = False) -> tuple[str | None, bool]:
        """Return (player_id, did_http_request)."""
        normalized = self._normalize_discord_username(player_name, strip_rank_prefix=strip_rank_prefix)
        if not normalized:
            return None, False

        key = normalized.lower()
        now = time.time()

        cached = self._player_id_cache.get(key)
        if cached:
            cached_id, cached_ts = cached
            ttl = PLAYER_LOOKUP_CACHE_TTL_SECONDS if cached_id is not None else PLAYER_LOOKUP_NEGATIVE_CACHE_TTL_SECONDS
            if now - cached_ts <= ttl:
                return cached_id, False

        player_name_q = urllib.parse.quote(normalized, safe="")
        endpoint = f"get_players_history?player_name={player_name_q}&page_size=1"
        data = await self._rcon_get(endpoint)
        if not data or data.get("failed") or data.get("error"):
            # Don't lock in errors for too long.
            self._player_id_cache[key] = (None, now)
            return None, True

        player_id = self._extract_first_player_id(data.get("result", data))
        self._player_id_cache[key] = (player_id, now)
        return player_id, True

    async def fetch_player_id_for_user(
        self,
        user: discord.abc.User,
        member: discord.Member | None,
        http_budget_remaining: int,
    ) -> tuple[str | None, int]:
        """Try to resolve a CRCON player_id by searching likely Discord name variants.

        Prefers server nickname/display name first (matches DISCORDNICKNAME usage), then falls back to raw username.
        """

        raw_candidates: list[str] = []
        if member is not None and member.display_name:
            raw_candidates.append(member.display_name)
        raw_candidates.append(user.name)  # raw username (no discriminator)
        gn = getattr(user, "global_name", None)
        if gn:
            raw_candidates.append(gn)

        # Build candidate variants to improve match rate.
        # IMPORTANT: only strip rank prefixes as a fallback *after* trying the exact (non-rank-stripped) lookup.
        seen: set[str] = set()
        unique_raw: list[str] = []

        for c in raw_candidates:
            if not c:
                continue
            # Keep raw-ish variants (we normalize later, depending on fallback mode).
            for v in (c, c.replace("%", " "), self._normalize_rank_dot(c)):
                nv = v.strip()
                if not nv:
                    continue
                k = nv.lower()
                if k in seen:
                    continue
                seen.add(k)
                unique_raw.append(nv)

        http_used = 0
        for raw in unique_raw:
            if http_used >= http_budget_remaining:
                break

            # Build best-guess query order for this raw candidate.
            # If an ordinal suffix exists ("...5th"), try without it FIRST.
            base_norm = self._normalize_discord_username(raw, strip_rank_prefix=False)
            base_no_ord = self._strip_trailing_ordinal(base_norm)

            base_queries: list[str] = []
            if base_no_ord and base_no_ord != base_norm:
                base_queries.append(base_no_ord)
            if base_norm:
                base_queries.append(base_norm)

            # 1) Non-rank-stripped queries
            for q in base_queries:
                for qq in self._build_simple_variants(q):
                    if http_used >= http_budget_remaining:
                        break
                    pid, did_http = await self._fetch_player_id_cached(qq, strip_rank_prefix=False)
                    if did_http:
                        http_used += 1
                    if pid:
                        return pid, http_used
                if http_used >= http_budget_remaining:
                    break

            if http_used >= http_budget_remaining:
                break

            # 2) Fallback lookup (strip rank prefix) — only if it changes the query.
            stripped_norm = self._normalize_discord_username(raw, strip_rank_prefix=True)
            if stripped_norm and stripped_norm != base_norm:
                stripped_no_ord = self._strip_trailing_ordinal(stripped_norm)

                stripped_queries: list[str] = []
                if stripped_no_ord and stripped_no_ord != stripped_norm:
                    stripped_queries.append(stripped_no_ord)
                stripped_queries.append(stripped_norm)

                for q in stripped_queries:
                    for qq in self._build_simple_variants(q):
                        if http_used >= http_budget_remaining:
                            break
                        pid2, did_http2 = await self._fetch_player_id_cached(qq, strip_rank_prefix=False)
                        if did_http2:
                            http_used += 1
                        if pid2:
                            return pid2, http_used
                    if http_used >= http_budget_remaining:
                        break

        return None, http_used

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

        nickname = self._escape_for_embed(nickname)
        username = self._escape_for_embed(username)
        if player_id:
            if INCLUDE_HLLRECORDS_LINK:
                pid = urllib.parse.quote(str(player_id), safe="")
                url = f"https://www.hllrecords.com/profiles/{pid}"
                return f"[{nickname}]({url}) ({username}) [{player_id}]"
            return f"{nickname} ({username}) [{player_id}]"
        return f"{nickname} ({username})"

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

    def _build_reaction_embeds(
        self,
        guild: discord.Guild,
        key: str,
        entries: list[str],
        now: datetime,
    ) -> list[discord.Embed]:
        title_base = f"{key} reactions"
        header = f"**{key} ({len(entries)})**\n\n"

        if not entries:
            pages = [header + "- None"]
        else:
            body = "\n\n".join(entries)
            pages = self._chunk_embed_descriptions(header + body)

        embeds: list[discord.Embed] = []
        total = len(pages)
        for idx, page in enumerate(pages, start=1):
            title = title_base if total == 1 else f"{title_base} ({idx}/{total})"
            e = discord.Embed(
                title=title,
                description=page,
                color=discord.Color.blurple(),
                timestamp=now,
            )
            e.set_footer(text=f"Updated • {guild.name}")
            embeds.append(e)

        return embeds

    async def _build_results(self, message: discord.Message) -> dict[str, list[str]]:
        results: dict[str, list[str]] = {"I": [], "A": [], "R": []}
        http_lookups_done = 0

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
                if PLAYER_LOOKUP_ENABLED and http_lookups_done < PLAYER_LOOKUP_MAX_PER_UPDATE:
                    remaining = PLAYER_LOOKUP_MAX_PER_UPDATE - http_lookups_done
                    pid, used = await self.fetch_player_id_for_user(user, member, remaining)
                    player_id = pid
                    http_lookups_done += used

                line = self._format_user_line(user, member, player_id)
                if line not in results[key]:
                    results[key].append(line)

        return results

    def _build_embeds(self, guild: discord.Guild, results: dict[str, list[str]]) -> list[discord.Embed]:
        now = datetime.now(timezone.utc)
        embeds: list[discord.Embed] = []
        for key in ["I", "A", "R"]:
            embeds.extend(self._build_reaction_embeds(guild, key, results.get(key, []), now))
        return embeds

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
