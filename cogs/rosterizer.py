import asyncio
import json
import os
import re
import time
import urllib.parse
from datetime import datetime, timezone
import discord
from discord.ext import commands
from discord import app_commands
import requests

# ========= CONFIG =========

GUILD_ID = 1097913605082579024

# Only members with ANY of these roles can use /lockroster and /unlockroster
ROSTER_LOCK_ADMIN_ROLE_IDS: list[int] = [1213495462632361994, 1098342675389890670, 1098342769468125214]  # e.g. [123..., 456...]

TARGET_MESSAGE_ID = 1458515177438838979
OUTPUT_CHANNEL_ID = 1459904650831724806  # set to None to post in same channel
ROLE_ID = 1364639604564688917  # Set to role ID to auto-assign when users react (e.g., 1234567890123456789)
STATE_FILE = "data/rosterizer_state.json"  # stores output message id for editing
UPDATE_DEBOUNCE_SECONDS = 2.0
INCLUDE_HLLRECORDS_LINK = False  # If True, hyperlink names to hllrecords.com when player_id is available

LOCKED_DM_MESSAGE = (
    "The roster is currently locked. Please contact the ICs to unlock the roster so you can click an emoji/reaction and be added."
)
LOCKED_DM_COOLDOWN_SECONDS = 60

# CRCON API (Bearer token) — same pattern as mapvote.py
CRCON_PANEL_URL = "https://7dr.hlladmin.com/api/"
CRCON_API_KEY = os.getenv("CRCON_API_KEY")
PLAYER_LOOKUP_ENABLED = True
PLAYER_LOOKUP_MAX_PER_UPDATE = 120
PLAYER_LOOKUP_CACHE_TTL_SECONDS = 3600
PLAYER_LOOKUP_NEGATIVE_CACHE_TTL_SECONDS = 120

# Rank ladder (highest -> lowest) and accepted prefix variants.
# Used for: sorting the roster and (as a fallback) stripping rank prefixes during CRCON lookup.
RANK_ORDER: list[tuple[str, list[str]]] = [
    ("FM", ["Field Marshal", "FM"]),
    ("GEN", ["General", "Gen"]),
    ("LTGEN", ["Lieutenant General", "Lt Gen", "Lt.Gen", "LtGen", "Lt-Gen"]),
    ("MAJGEN", ["Major General", "Maj Gen", "Maj.Gen", "MajGen", "Maj-Gen"]),
    ("BRIG", ["Brigadier", "Brig"]),

    ("COL", ["Colonel", "Col"]),
    ("LTCOL", ["Lieutenant Colonel", "Lt Col", "Lt. Col", "Lt.Col", "LtCol", "Lt-Col"]),
    ("MAJ", ["Major", "Maj"]),
    ("CPT", ["Captain", "Cpt"]),
    ("LT", ["Lieutenant", "Lt", "Lt."]),
    ("2LT", ["2nd Lieutenant", "2Lt", "2Lt.", "2ndLt", "2nd Lt", "2 Lt"]),

    ("RSM", ["Regimental Sergeant Major", "Regimental Sargent Major", "RSM"]),
    ("WO1", ["Warrant Officer 1st Class", "Warrant Officer 1", "WO1"]),
    ("WO2", ["Warrant Officer 2nd Class", "Warrant Officer 2", "WO2"]),

    ("SGM", ["Sergeant Major", "Sergeant major", "SGM"]),
    ("SSG", ["Staff Sergeant", "Staff Sargent", "SSG"]),

    ("SGT", ["Sergeant", "Sgt"]),
    ("CPL", ["Corporal", "Cpl"]),
    ("LCPL", ["Lance Corporal", "L.Cpl", "LCpl", "L Cpl"]),
    ("PTE", ["Private", "Pte", "Pte."])
]

# Flattened variants list (kept for regex building in rank-stripping).
RANK_PREFIXES: list[str] = [v for _code, variants in RANK_ORDER for v in variants]

VALID_REACTIONS = {
    "I": "I",
    "🇮": "I",
    "A": "A",
    "🇦": "A",
    "R": "R",
    "🇷": "R",
}
# ==========================


def _can_manage_roster_lock(interaction: discord.Interaction) -> bool:
    user = interaction.user
    if not isinstance(user, discord.Member):
        return False
    if not ROSTER_LOCK_ADMIN_ROLE_IDS:
        return False
    return any(r.id in ROSTER_LOCK_ADMIN_ROLE_IDS for r in getattr(user, "roles", []))


class ReactionReader(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ran_once = False
        self._update_task: asyncio.Task | None = None
        self._update_lock = asyncio.Lock()
        self._state = self._load_state()
        # cache: normalized_name (lower) -> (player_id_or_none, timestamp)
        self._player_id_cache: dict[str, tuple[str | None, float]] = {}
        self._dm_last_sent: dict[int, float] = {}

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

    def _get_target_state(self, guild_id: int) -> dict:
        bucket = self._get_state_bucket(guild_id)
        return bucket.setdefault(str(TARGET_MESSAGE_ID), {})

    def _get_output_message_id(self, guild_id: int) -> int | None:
        item = self._get_target_state(guild_id)
        msg_id = item.get("output_message_id")
        return int(msg_id) if isinstance(msg_id, int) else None

    def _set_output_message_id(self, guild_id: int, message_id: int) -> None:
        item = self._get_target_state(guild_id)
        item["output_message_id"] = message_id
        item["output_channel_id"] = OUTPUT_CHANNEL_ID
        self._save_state()

    def _is_roster_locked(self, guild_id: int) -> bool:
        item = self._get_target_state(guild_id)
        return bool(item.get("roster_locked", False))

    def _set_roster_locked(self, guild_id: int, locked: bool) -> None:
        item = self._get_target_state(guild_id)
        item["roster_locked"] = bool(locked)
        self._save_state()

    def _is_valid_reaction_emoji(self, emoji_str: str) -> bool:
        return emoji_str in VALID_REACTIONS

    async def _maybe_dm_locked_notice(self, user_id: int) -> None:
        if not LOCKED_DM_MESSAGE:
            return

        now = time.time()
        last = self._dm_last_sent.get(user_id, 0.0)
        if now - last < LOCKED_DM_COOLDOWN_SECONDS:
            return

        user = self.bot.get_user(user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return

        try:
            await user.send(LOCKED_DM_MESSAGE)
            self._dm_last_sent[user_id] = now
        except (discord.Forbidden, discord.HTTPException):
            return

    async def _try_remove_user_reaction(
        self,
        *,
        channel_id: int,
        message_id: int,
        emoji: discord.PartialEmoji | discord.Emoji | str,
        user_id: int,
    ) -> None:
        channel = await self._resolve_channel(channel_id)
        if channel is None:
            return

        fetch_message = getattr(channel, "fetch_message", None)
        if fetch_message is None:
            return

        try:
            msg = await fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

        user = self.bot.get_user(user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return

        try:
            await msg.remove_reaction(emoji, user)
        except (discord.Forbidden, discord.HTTPException):
            return

    async def _sync_app_commands_for_guilds(self) -> None:
        # Ensure guild-scoped commands appear immediately.
        try:
            await self.bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        except Exception:
            pass

    async def _find_target_message(self) -> discord.Message | None:
        # Find the message in all guilds/channels the bot can see
        for guild in self.bot.guilds:
            for channel in guild.text_channels:
                try:
                    message = await channel.fetch_message(TARGET_MESSAGE_ID)
                    if message:
                        return message
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    continue
        return None

    async def _find_target_message_in_guild(self, guild: discord.Guild) -> discord.Message | None:
        for channel in guild.text_channels:
            try:
                message = await channel.fetch_message(TARGET_MESSAGE_ID)
                if message:
                    return message
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                continue
        return None

    @app_commands.command(name="lockroster", description="Lock the roster (disable reaction signups).")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.check(_can_manage_roster_lock)
    async def lockroster(self, interaction: discord.Interaction):
        if interaction.guild_id is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        self._set_roster_locked(interaction.guild_id, True)

        guild = self.bot.get_guild(interaction.guild_id)
        if guild is not None:
            msg = await self._find_target_message_in_guild(guild)
            if msg is not None:
                await self._update_from_message(msg)

        await interaction.followup.send("Roster locked. New reactions will not be accepted.", ephemeral=True)

    @lockroster.error
    async def lockroster_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return
        await interaction.response.send_message(f"Failed to lock roster: {error}", ephemeral=True)

    @app_commands.command(name="unlockroster", description="Unlock the roster (enable reaction signups).")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.check(_can_manage_roster_lock)
    async def unlockroster(self, interaction: discord.Interaction):
        if interaction.guild_id is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        self._set_roster_locked(interaction.guild_id, False)

        guild = self.bot.get_guild(interaction.guild_id)
        if guild is not None:
            msg = await self._find_target_message_in_guild(guild)
            if msg is not None:
                # Backfill: if someone reacted while locked and their reaction remains,
                # they will now receive the role.
                await self._backfill_roles_from_message(msg)
                await self._update_from_message(msg)

        await interaction.followup.send("Roster unlocked. Reactions are now accepted.", ephemeral=True)

    @unlockroster.error
    async def unlockroster_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return
        await interaction.response.send_message(f"Failed to unlock roster: {error}", ephemeral=True)

    async def _try_assign_roster_role(self, guild: discord.Guild, user_id: int, *, reason: str) -> None:
        if ROLE_ID is None:
            return

        role = guild.get_role(ROLE_ID)
        if role is None:
            return

        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return

        if member is None or member.bot:
            return

        if role in member.roles:
            return

        try:
            await member.add_roles(role, reason=reason)
        except discord.Forbidden:
            print(f"Missing permission to assign role {ROLE_ID} to user {user_id}")
        except discord.HTTPException as e:
            print(f"Failed to assign role {ROLE_ID} to user {user_id}: {e}")

    async def _try_remove_roster_role(self, guild: discord.Guild, user_id: int, *, reason: str) -> None:
        if ROLE_ID is None:
            return

        role = guild.get_role(ROLE_ID)
        if role is None:
            return

        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return

        if member is None or member.bot:
            return

        if role not in member.roles:
            return

        try:
            await member.remove_roles(role, reason=reason)
        except discord.Forbidden:
            print(f"Missing permission to remove role {ROLE_ID} from user {user_id}")
        except discord.HTTPException as e:
            print(f"Failed to remove role {ROLE_ID} from user {user_id}: {e}")

    async def _user_still_has_any_valid_reaction(self, message: discord.Message, user_id: int) -> bool:
        for reaction in message.reactions:
            if not self._is_valid_reaction_emoji(str(reaction.emoji)):
                continue
            try:
                async for user in reaction.users():
                    if user.id == user_id:
                        return True
            except (discord.Forbidden, discord.HTTPException):
                # If we can't verify, do not remove roles.
                return True
        return False

    async def _backfill_roles_from_message(self, message: discord.Message) -> None:
        """One-time scan: assign ROLE_ID to anyone who has already reacted."""

        if ROLE_ID is None:
            return

        guild = message.guild
        role = guild.get_role(ROLE_ID)
        if role is None:
            print(f"ROLE_ID {ROLE_ID} not found in guild {guild.id}")
            return

        seen_user_ids: set[int] = set()

        for reaction in message.reactions:
            if not self._is_valid_reaction_emoji(str(reaction.emoji)):
                continue

            try:
                async for user in reaction.users():
                    if getattr(user, "bot", False):
                        continue
                    if user.id in seen_user_ids:
                        continue
                    seen_user_ids.add(user.id)
                    await self._try_assign_roster_role(guild, user.id, reason="Has roster reaction")
            except discord.Forbidden:
                print("Forbidden while scanning reactions for role backfill (missing view/history perms)")
                return
            except discord.HTTPException as e:
                print(f"Failed while scanning reactions for role backfill: {e}")
                return

    def _rank_index_from_display_name(self, display_name: str) -> int:
        """Return rank order index for sorting (lower is higher rank).

        Uses the order of RANK_PREFIXES as the precedence list.
        If no rank prefix is detected, returns a large value (sorted last).
        """

        if not display_name:
            return 10_000

        s = display_name.strip()
        if "#" in s:
            s = s.split("#", 1)[0].strip()
        s = " ".join(s.split())
        s_lower = s.lower()

        best_order: int | None = None
        best_len = -1

        for order_idx, (_code, variants) in enumerate(RANK_ORDER):
            for prefix in variants:
                p = str(prefix).strip()
                if not p:
                    continue
                p_lower = p.lower()

                # Match at the very start. Allow whitespace or '.' after the prefix.
                if s_lower == p_lower or s_lower.startswith(p_lower + " ") or s_lower.startswith(p_lower + "."):
                    if len(p_lower) > best_len:
                        best_len = len(p_lower)
                        best_order = order_idx

        return best_order if best_order is not None else 10_000


    def _cut_at_hash(self, text: str) -> str:
        """Return the portion of text before the first '#', trimmed."""
        t = (text or "").strip()
        if not t:
            return ""
        if "#" in t:
            t = t.split("#", 1)[0].strip()
        return " ".join(t.split())

    def _normalize_discord_username(self, name: str, *, strip_rank_prefix: bool = False) -> str:
        # "Trimming as needed":
        # - strip leading/trailing whitespace
        # - collapse internal whitespace
        # - strip everything from the first '#' onward (e.g. "Name#1579" -> "Name")
        # - (optional) strip leading rank prefix for lookup fallback
        name = self._cut_at_hash(name)
        # Replace common separators that can appear "between" tokens.
        name = name.replace("%", " ")
        name = " ".join(name.split())

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
        # Prefer server nickname/display name first.
        if member is not None and member.display_name:
            raw_candidates.append(member.display_name)
        # Then raw username / global name.
        raw_candidates.append(user.name)
        gn = getattr(user, "global_name", None)
        if gn:
            raw_candidates.append(gn)

        # Hard cutoff at '#' and then (if needed) strip rank prefix.
        # Example: "WO1 Nvil#3292⁵ᵗʰ" -> try "WO1 Nvil" first, then "Nvil".
        http_used = 0
        seen_queries: set[str] = set()

        for raw in raw_candidates:
            if http_used >= http_budget_remaining:
                break
            cut = self._normalize_discord_username(raw, strip_rank_prefix=False)
            if cut:
                k = cut.lower()
                if k not in seen_queries:
                    seen_queries.add(k)
                    pid, did_http = await self._fetch_player_id_cached(cut, strip_rank_prefix=False)
                    if did_http:
                        http_used += 1
                    if pid:
                        return pid, http_used

            if http_used >= http_budget_remaining:
                break

            stripped = self._normalize_discord_username(raw, strip_rank_prefix=True)
            if stripped and stripped != cut:
                k2 = stripped.lower()
                if k2 not in seen_queries:
                    seen_queries.add(k2)
                    pid2, did_http2 = await self._fetch_player_id_cached(stripped, strip_rank_prefix=False)
                    if did_http2:
                        http_used += 1
                    if pid2:
                        return pid2, http_used

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
        nickname = self._cut_at_hash(nickname)
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
        # Build as (rank_idx, line) so we can sort by rank.
        results_ranked: dict[str, list[tuple[int, str]]] = {"I": [], "A": [], "R": []}
        seen_lines: dict[str, set[str]] = {"I": set(), "A": set(), "R": set()}
        http_lookups_done = 0
        locked = self._is_roster_locked(message.guild.id)
        roster_role = message.guild.get_role(ROLE_ID) if ROLE_ID is not None else None

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

                # If roster is locked, only show users who already have the roster role.
                if locked and roster_role is not None:
                    if member is None or roster_role not in getattr(member, "roles", []):
                        continue

                player_id: str | None = None
                if PLAYER_LOOKUP_ENABLED and http_lookups_done < PLAYER_LOOKUP_MAX_PER_UPDATE:
                    remaining = PLAYER_LOOKUP_MAX_PER_UPDATE - http_lookups_done
                    pid, used = await self.fetch_player_id_for_user(user, member, remaining)
                    player_id = pid
                    http_lookups_done += used

                line = self._format_user_line(user, member, player_id)
                if line in seen_lines[key]:
                    continue

                # Rank detection is based on the member's display name when available.
                rank_source = member.display_name if member else (getattr(user, "global_name", None) or user.name)
                rank_idx = self._rank_index_from_display_name(rank_source)

                results_ranked[key].append((rank_idx, line))
                seen_lines[key].add(line)

        # Sort each reaction list by rank (highest->lowest), then by line for stable ordering.
        results: dict[str, list[str]] = {"I": [], "A": [], "R": []}
        for key in ["I", "A", "R"]:
            ranked = sorted(results_ranked[key], key=lambda t: (t[0], t[1].lower()))
            results[key] = [line for _, line in ranked]

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

        await self._sync_app_commands_for_guilds()
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

        if self._is_roster_locked(payload.guild_id):
            await self._maybe_dm_locked_notice(payload.user_id)
            # Best-effort: remove the reaction so it doesn't appear on the roster message.
            await self._try_remove_user_reaction(
                channel_id=payload.channel_id,
                message_id=payload.message_id,
                emoji=payload.emoji,
                user_id=payload.user_id,
            )
            self._schedule_update(payload.guild_id, payload.channel_id)
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is not None:
            await self._try_assign_roster_role(guild, payload.user_id, reason="Reacted to roster message")

        self._schedule_update(payload.guild_id, payload.channel_id)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.message_id != TARGET_MESSAGE_ID:
            return
        if not self._is_valid_reaction_emoji(str(payload.emoji)):
            return
        if payload.guild_id is None:
            return

        # Remove role if configured and the user no longer has ANY valid roster reaction.
        if ROLE_ID is not None:
            try:
                guild = self.bot.get_guild(payload.guild_id)
                channel = await self._resolve_channel(payload.channel_id)
                fetch_message = getattr(channel, "fetch_message", None) if channel is not None else None
                if guild is not None and fetch_message is not None:
                    msg = await fetch_message(TARGET_MESSAGE_ID)
                    still_has = await self._user_still_has_any_valid_reaction(msg, payload.user_id)
                    if not still_has:
                        await self._try_remove_roster_role(guild, payload.user_id, reason="Removed roster reaction")
            except discord.Forbidden:
                print("Forbidden while checking reactions for role removal")
            except (discord.NotFound, discord.HTTPException) as e:
                print(f"Failed to fetch/check message for role removal: {e}")

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
        message = await self._find_target_message()

        if not message:
            print("Target message not found")
            return

        await self._update_from_message(message)
        await self._backfill_roles_from_message(message)

        print("ReactionReader complete — unload when ready")


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionReader(bot))

