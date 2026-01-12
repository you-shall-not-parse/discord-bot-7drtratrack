import asyncio
import json
import os
import re
import time
import urllib.parse
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

import requests

# ========= CONFIG =========

GUILD_ID = 1097913605082579024

# Only members with ANY of these roles can use /lockroster and /unlockroster
ROSTER_LOCK_ADMIN_ROLE_IDS: list[int] = [1213495462632361994, 1098342675389890670, 1098342769468125214]

OUTPUT_CHANNEL_ID = 1459904650831724806  # set to None to use the guild's system channel
ROLE_ID = 1364639604564688917

STATE_FILE = "data/rosterizer_manual_state.json"
UPDATE_DEBOUNCE_SECONDS = 2.0

ROSTER_OUTPUT_TITLE = "Roster"

LOCKED_DM_MESSAGE = (
    "The roster is currently locked. Please contact the ICs to unlock the roster so you can be added."
)
LOCKED_DM_COOLDOWN_SECONDS = 60

# If True: when roster is locked and someone manually adds ROLE_ID, revert it.
BLOCK_MANUAL_ROLE_ADDS_WHEN_LOCKED = True

# Best-effort: DM the executor (the role-adder) when roster is locked.
AUDITLOG_LOOKBACK_SECONDS = 15

# CRCON API (Bearer token) — same pattern as rosterizer.py
CRCON_PANEL_URL = "https://7dr.hlladmin.com/api/"
CRCON_API_KEY = os.getenv("CRCON_API_KEY")

PLAYER_LOOKUP_ENABLED = True
PLAYER_LOOKUP_MAX_PER_UPDATE = 120
PLAYER_LOOKUP_CACHE_TTL_SECONDS = 3600
PLAYER_LOOKUP_NEGATIVE_CACHE_TTL_SECONDS = 120

INCLUDE_HLLRECORDS_LINK = False  # If True, hyperlink names to hllrecords.com when player_id is available

# Rank ladder (highest -> lowest) and accepted prefix variants. Used only for sorting.
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
    ("PTE", ["Private", "Pte", "Pte."]),
]

# Flattened variants list (kept for regex building in rank-stripping).
RANK_PREFIXES: list[str] = [v for _code, variants in RANK_ORDER for v in variants]


def _can_manage_roster_lock(interaction: discord.Interaction) -> bool:
    user = interaction.user
    if not isinstance(user, discord.Member):
        return False
    if not ROSTER_LOCK_ADMIN_ROLE_IDS:
        return False
    return any(r.id in ROSTER_LOCK_ADMIN_ROLE_IDS for r in getattr(user, "roles", []))


class ManualRoster(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ran_once = False
        self._update_lock = asyncio.Lock()
        self._update_task: asyncio.Task | None = None
        self._state = self._load_state()
        self._dm_last_sent: dict[int, float] = {}
        # cache: normalized_name (lower) -> (player_id_or_none, timestamp)
        self._player_id_cache: dict[str, tuple[str | None, float]] = {}

    # ---------- state ----------

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
        msg_id = bucket.get("output_message_id")
        return int(msg_id) if isinstance(msg_id, int) else None

    def _set_output_message_id(self, guild_id: int, message_id: int) -> None:
        bucket = self._get_state_bucket(guild_id)
        bucket["output_message_id"] = message_id
        bucket["output_channel_id"] = OUTPUT_CHANNEL_ID
        self._save_state()

    def _is_roster_locked(self, guild_id: int) -> bool:
        bucket = self._get_state_bucket(guild_id)
        return bool(bucket.get("roster_locked", False))

    def _set_roster_locked(self, guild_id: int, locked: bool) -> None:
        bucket = self._get_state_bucket(guild_id)
        bucket["roster_locked"] = bool(locked)
        self._save_state()

    # ---------- helpers ----------

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

    def _escape_for_embed(self, text: str) -> str:
        text = discord.utils.escape_mentions(text)
        text = discord.utils.escape_markdown(text, as_needed=False)
        text = text.replace("[", "\\[").replace("]", "\\]")
        text = text.replace("(", "\\(").replace(")", "\\)")
        return text

    def _rank_index_from_display_name(self, display_name: str) -> int:
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

                if s_lower == p_lower or s_lower.startswith(p_lower + " ") or s_lower.startswith(p_lower + "."):
                    if len(p_lower) > best_len:
                        best_len = len(p_lower)
                        best_order = order_idx

        return best_order if best_order is not None else 10_000

    def _cut_at_hash(self, text: str) -> str:
        t = (text or "").strip()
        if not t:
            return ""
        if "#" in t:
            t = t.split("#", 1)[0].strip()
        return " ".join(t.split())

    def _normalize_discord_username(self, name: str, *, strip_rank_prefix: bool = False) -> str:
        name = self._cut_at_hash(name)
        name = name.replace("%", " ")
        name = " ".join(name.split())

        if strip_rank_prefix:
            rank_pat = r"^(?:" + "|".join(re.escape(r) for r in RANK_PREFIXES) + r")\.?\s+"
            name = re.sub(rank_pat, "", name, flags=re.IGNORECASE).strip()

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
            self._player_id_cache[key] = (None, now)
            return None, True

        player_id = self._extract_first_player_id(data.get("result", data))
        self._player_id_cache[key] = (player_id, now)
        return player_id, True

    async def fetch_player_id_for_member(self, member: discord.Member, http_budget_remaining: int) -> tuple[str | None, int]:
        """Try to resolve a CRCON player_id by searching likely Discord name variants."""

        raw_candidates: list[str] = []
        if member.display_name:
            raw_candidates.append(member.display_name)
        raw_candidates.append(member.name)
        gn = getattr(member, "global_name", None)
        if gn:
            raw_candidates.append(gn)

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

    def _format_member_line(self, member: discord.Member, player_id: str | None) -> str:
        nickname = self._cut_at_hash(member.display_name)
        username = self._normalize_discord_username(member.name)

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

    async def _resolve_output_channel(self, guild: discord.Guild) -> discord.abc.Messageable | None:
        if OUTPUT_CHANNEL_ID is None:
            return guild.system_channel

        ch = self.bot.get_channel(OUTPUT_CHANNEL_ID)
        if ch is not None:
            return ch
        try:
            return await self.bot.fetch_channel(OUTPUT_CHANNEL_ID)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return guild.system_channel

    async def _ensure_roster_message(self, guild: discord.Guild) -> tuple[discord.abc.Messageable, discord.Message] | None:
        output_channel = await self._resolve_output_channel(guild)
        if output_channel is None:
            return None

        existing_id = self._get_output_message_id(guild.id)
        if existing_id is not None:
            try:
                existing = await output_channel.fetch_message(existing_id)  # type: ignore[attr-defined]
                return output_channel, existing
            except (discord.NotFound, discord.Forbidden, discord.HTTPException, AttributeError):
                pass

        placeholder = discord.Embed(
            title=ROSTER_OUTPUT_TITLE,
            description="Preparing roster…",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        sent = await output_channel.send(
            embeds=[placeholder],
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self._set_output_message_id(guild.id, sent.id)
        return output_channel, sent

    async def _build_roster_entries(self, guild: discord.Guild) -> list[str]:
        if ROLE_ID is None:
            return []
        role = guild.get_role(ROLE_ID)
        if role is None:
            return []

        entries_ranked: list[tuple[int, str]] = []
        seen: set[str] = set()

        http_lookups_done = 0

        for member in getattr(role, "members", []) or []:
            if member.bot:
                continue

            player_id: str | None = None
            if PLAYER_LOOKUP_ENABLED and http_lookups_done < PLAYER_LOOKUP_MAX_PER_UPDATE:
                remaining = PLAYER_LOOKUP_MAX_PER_UPDATE - http_lookups_done
                pid, used = await self.fetch_player_id_for_member(member, remaining)
                player_id = pid
                http_lookups_done += used

            line = self._format_member_line(member, player_id)
            if line in seen:
                continue
            seen.add(line)
            rank_idx = self._rank_index_from_display_name(member.display_name)
            entries_ranked.append((rank_idx, line))

        entries_ranked.sort(key=lambda t: (t[0], t[1].lower()))
        return [line for _, line in entries_ranked]

    def _build_roster_embeds(self, guild: discord.Guild, entries: list[str]) -> list[discord.Embed]:
        now = datetime.now(timezone.utc)
        locked = self._is_roster_locked(guild.id)
        lock_line = "\n\n**Status:** LOCKED" if locked else "\n\n**Status:** UNLOCKED"
        header = f"**{ROSTER_OUTPUT_TITLE} ({len(entries)})**{lock_line}\n\n"

        if not entries:
            pages = [header + "- None"]
        else:
            body = "\n\n".join(entries)
            pages = self._chunk_embed_descriptions(header + body)

        embeds: list[discord.Embed] = []
        total = len(pages)
        for idx, page in enumerate(pages, start=1):
            title = ROSTER_OUTPUT_TITLE if total == 1 else f"{ROSTER_OUTPUT_TITLE} ({idx}/{total})"
            e = discord.Embed(
                title=title,
                description=page,
                color=discord.Color.blurple(),
                timestamp=now,
            )
            e.set_footer(text=f"Updated • {guild.name}")
            embeds.append(e)
        return embeds

    async def _update_roster(self, guild: discord.Guild) -> None:
        async with self._update_lock:
            ensured = await self._ensure_roster_message(guild)
            if ensured is None:
                return
            output_channel, output_message = ensured

            entries = await self._build_roster_entries(guild)
            embeds = self._build_roster_embeds(guild, entries)

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

    def _schedule_update(self, guild_id: int) -> None:
        if self._update_task and not self._update_task.done():
            self._update_task.cancel()

        async def _runner() -> None:
            try:
                await asyncio.sleep(UPDATE_DEBOUNCE_SECONDS)
                guild = self.bot.get_guild(guild_id)
                if guild is None:
                    return
                await self._update_roster(guild)
            except asyncio.CancelledError:
                return
            except Exception as e:
                print(f"Roster update failed: {e}")

        self._update_task = asyncio.create_task(_runner())

    async def _dm_executor_if_manual_role_add_while_locked(self, guild: discord.Guild, target_member: discord.Member) -> None:
        if not self._is_roster_locked(guild.id):
            return

        me = guild.me
        if me is None or not getattr(me.guild_permissions, "view_audit_log", False):
            return

        try:
            cutoff_ts = datetime.now(timezone.utc).timestamp() - AUDITLOG_LOOKBACK_SECONDS
            async for entry in guild.audit_logs(limit=10, action=discord.AuditLogAction.member_role_update):
                created = getattr(entry, "created_at", None)
                if created is not None and created.replace(tzinfo=timezone.utc).timestamp() < cutoff_ts:
                    break

                if getattr(entry, "target", None) is None or getattr(entry.target, "id", None) != target_member.id:
                    continue

                before_roles = getattr(getattr(entry, "before", None), "roles", None)
                after_roles = getattr(getattr(entry, "after", None), "roles", None)
                if before_roles is None or after_roles is None:
                    continue

                before_ids = {r.id for r in before_roles}
                after_ids = {r.id for r in after_roles}
                if ROLE_ID in after_ids and ROLE_ID not in before_ids:
                    executor = getattr(entry, "user", None)
                    if executor is not None:
                        await self._maybe_dm_locked_notice(executor.id)
                    return
        except (discord.Forbidden, discord.HTTPException):
            return

    async def _try_remove_roster_role(self, guild: discord.Guild, member: discord.Member, *, reason: str) -> None:
        if ROLE_ID is None:
            return
        role = guild.get_role(ROLE_ID)
        if role is None:
            return
        if role not in member.roles:
            return
        try:
            await member.remove_roles(role, reason=reason)
        except discord.Forbidden:
            print(f"Missing permission to remove role {ROLE_ID} from user {member.id}")
        except discord.HTTPException as e:
            print(f"Failed to remove role {ROLE_ID} from user {member.id}: {e}")

    # ---------- commands ----------

    @app_commands.command(name="lockroster", description="Lock the roster (block manual role adds).")
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
            await self._update_roster(guild)

        await interaction.followup.send("Roster locked. Manual role additions will be reverted.", ephemeral=True)

    @lockroster.error
    async def lockroster_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return
        await interaction.response.send_message(f"Failed to lock roster: {error}", ephemeral=True)

    @app_commands.command(name="unlockroster", description="Unlock the roster (allow manual role adds).")
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
            await self._update_roster(guild)

        await interaction.followup.send("Roster unlocked. Manual role additions are allowed.", ephemeral=True)

    @unlockroster.error
    async def unlockroster_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return
        await interaction.response.send_message(f"Failed to unlock roster: {error}", ephemeral=True)

    # ---------- events ----------

    @commands.Cog.listener()
    async def on_ready(self):
        if self._ran_once:
            return
        self._ran_once = True

        try:
            await self.bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        except Exception:
            pass

        guild = self.bot.get_guild(GUILD_ID)
        if guild is not None:
            await self._update_roster(guild)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if ROLE_ID is None:
            return
        if after.guild is None:
            return

        before_has = any(r.id == ROLE_ID for r in getattr(before, "roles", []))
        after_has = any(r.id == ROLE_ID for r in getattr(after, "roles", []))

        # Only react to actual changes in membership.
        if before_has == after_has:
            return

        # Role was added.
        if not before_has and after_has:
            if self._is_roster_locked(after.guild.id):
                await self._dm_executor_if_manual_role_add_while_locked(after.guild, after)
                if BLOCK_MANUAL_ROLE_ADDS_WHEN_LOCKED:
                    await self._try_remove_roster_role(after.guild, after, reason="Roster locked - manual role add blocked")

        self._schedule_update(after.guild.id)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        # When someone leaves, they disappear from role.members; refresh roster promptly.
        try:
            if member.guild is None:
                return
            self._schedule_update(member.guild.id)
        except Exception:
            return


async def setup(bot: commands.Bot):
    await bot.add_cog(ManualRoster(bot))
