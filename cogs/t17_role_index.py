import asyncio
import json
import logging
import os
import urllib.parse
from typing import Any, Optional

import discord
from discord.ext import commands

from clan_t17_lookup import ClanT17Lookup
from config import MAIN_GUILD_ID
from data_paths import data_path
from hll_API_backend import HLLBackendError, get_hll_backend_client

GUILD_ID = MAIN_GUILD_ID
FORUM_CHANNEL_ID = 1388644379211862096
STATE_FILE = data_path("t17_role_index_state.json")
THREAD_NAME = "T17 Member Index"
THREAD_INTRO = "Auto-updated index of tracked members, Discord names, nicknames, and T17 IDs."
SYNC_DEBOUNCE_SECONDS = 2.0
MEMBERSHIP_SYNC_COOLDOWN_SECONDS = 300
TRACKED_ROLE_NAMES = [
    "Basic Trained",
]


class T17RoleIndex(commands.Cog, name="[API] T17RoleIndex"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger(__name__)
        self.backend = get_hll_backend_client()
        self.lookup = ClanT17Lookup(self.backend, logger=self.logger)
        self._sync_lock = asyncio.Lock()
        self._sync_task: asyncio.Task | None = None
        self._started = False
        self._state = self._load_state()
        self._membership_sync_warned = False

    def cog_unload(self) -> None:
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()

    def _load_state(self) -> dict[str, Any]:
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as handle:
                data = json.load(handle)
                return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_state(self) -> None:
        os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as handle:
            json.dump(self._state, handle, indent=2)

    def _set_state(self, *, thread_id: int | None, message_ids: list[int]) -> None:
        self._state["thread_id"] = thread_id
        self._state["message_ids"] = message_ids
        self._save_state()

    def _synced_members_state(self) -> dict[int, dict[str, str]]:
        raw = self._state.get("synced_members")
        if not isinstance(raw, dict):
            return {}

        normalized: dict[int, dict[str, str]] = {}
        for key, value in raw.items():
            try:
                member_id = int(key)
            except (TypeError, ValueError):
                continue
            if not isinstance(value, dict):
                continue
            t17_id = str(value.get("t17_id") or "").strip()
            if not t17_id:
                continue
            player_name = str(value.get("player_name") or "").strip() or t17_id
            normalized[member_id] = {
                "t17_id": t17_id,
                "player_name": player_name,
            }
        return normalized

    def _save_synced_members_state(self, synced_members: dict[int, dict[str, str]]) -> None:
        self._state["synced_members"] = {
            str(member_id): {
                "t17_id": str(entry.get("t17_id") or "").strip(),
                "player_name": str(entry.get("player_name") or "").strip(),
            }
            for member_id, entry in synced_members.items()
            if str(entry.get("t17_id") or "").strip()
        }
        self._save_state()

    def _membership_sync_cooldown_until(self) -> float:
        raw_value = self._state.get("membership_sync_cooldown_until")
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            return 0.0

    def _set_membership_sync_cooldown(self, seconds: float) -> None:
        self._state["membership_sync_cooldown_until"] = max(0.0, seconds)
        self._save_state()

    def _clear_membership_sync_cooldown(self) -> None:
        if "membership_sync_cooldown_until" in self._state:
            self._state.pop("membership_sync_cooldown_until", None)
            self._save_state()

    def _membership_sync_is_cooling_down(self) -> bool:
        cooldown_until = self._membership_sync_cooldown_until()
        if cooldown_until <= 0.0:
            return False
        now = asyncio.get_running_loop().time()
        if now >= cooldown_until:
            self._clear_membership_sync_cooldown()
            return False
        return True

    def _trigger_membership_sync_cooldown(self) -> None:
        cooldown_until = asyncio.get_running_loop().time() + MEMBERSHIP_SYNC_COOLDOWN_SECONDS
        self._set_membership_sync_cooldown(cooldown_until)

    def _is_bifrost_high_error_rate_lockout(self, error: BaseException) -> bool:
        message = str(error or "").casefold()
        return "high error rate" in message and "restored automatically" in message

    def _tracked_role_names(self) -> set[str]:
        return set(TRACKED_ROLE_NAMES)

    def _member_tracked_roles(self, member: discord.Member) -> set[str]:
        tracked = self._tracked_role_names()
        return {role.name for role in member.roles if role.name in tracked}

    def _escape_for_embed(self, text: str) -> str:
        escaped = discord.utils.escape_mentions(text or "")
        escaped = discord.utils.escape_markdown(escaped, as_needed=False)
        escaped = escaped.replace("[", "\\[").replace("]", "\\]")
        escaped = escaped.replace("(", "\\(").replace(")", "\\)")
        return escaped

    def _format_member_line(self, member: discord.Member, t17_id: str | None) -> str:
        username = self._escape_for_embed(self.lookup.normalize_discord_username(member.name) or member.name)
        nickname = self._escape_for_embed(self.lookup.cut_at_hash(member.display_name) or member.display_name or member.name)
        if t17_id:
            player_id = urllib.parse.quote(t17_id, safe="")
            url = f"https://www.hllrecords.com/profiles/{player_id}"
            escaped_t17 = self._escape_for_embed(t17_id)
            return f"- Discord name: {username} | Nickname: {nickname} | T17: [{escaped_t17}]({url})"
        return f"- Discord name: {username} | Nickname: {nickname} | T17: Unknown"

    def _chunk_lines(self, lines: list[str], *, max_len: int = 3900) -> list[str]:
        if not lines:
            return ["No members currently have this role."]

        parts: list[str] = []
        current = ""
        for line in lines:
            proposed = f"{current}\n{line}" if current else line
            if len(proposed) > max_len:
                if current:
                    parts.append(current)
                current = line
            else:
                current = proposed
        if current:
            parts.append(current)
        return parts or ["No members currently have this role."]

    def _build_role_embeds(self, guild: discord.Guild, role_name: str, mapping: dict[str, Any]) -> list[discord.Embed]:
        role = discord.utils.get(guild.roles, name=role_name)
        if role is None:
            embed = discord.Embed(
                title=role_name,
                description="Role not found in this guild.",
                color=discord.Color.red(),
            )
            return [embed]

        members = sorted(role.members, key=lambda item: item.display_name.casefold())
        lines: list[str] = []
        for member in members:
            key = self.lookup.resolved_member_key(guild.id, member.id, role_name)
            entry = mapping.get("resolved_members", {}).get(key)
            t17_id = None
            if isinstance(entry, dict) and entry.get("t17_id"):
                t17_id = str(entry["t17_id"])
            lines.append(self._format_member_line(member, t17_id))

        sections = self._chunk_lines(lines)
        color = role.color if role.color.value else discord.Color.blurple()
        embeds: list[discord.Embed] = []
        for index, description in enumerate(sections, start=1):
            suffix = "" if len(sections) == 1 else f" (Part {index}/{len(sections)})"
            embed = discord.Embed(
                title=f"{role_name} ({len(members)}){suffix}",
                description=description,
                color=color,
            )
            embeds.append(embed)
        return embeds

    def _group_embeds(self, embeds: list[discord.Embed]) -> list[list[discord.Embed]]:
        return [[embed] for embed in embeds] or [[]]

    def _preferred_player_name(self, target: dict[str, Any]) -> str:
        queries = target.get("queries")
        if isinstance(queries, list):
            for query in queries:
                value = str(query or "").strip()
                if value:
                    return value
        return str(target.get("display_name") or "").strip() or str(target.get("t17_id") or "").strip()

    async def _get_forum_channel(self) -> Optional[discord.ForumChannel]:
        channel = self.bot.get_channel(FORUM_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(FORUM_CHANNEL_ID)
            except Exception:
                self.logger.exception("Failed to fetch T17 index forum channel")
                return None
        return channel if isinstance(channel, discord.ForumChannel) else None

    async def _get_thread(self, thread_id: int | None) -> Optional[discord.Thread]:
        if not thread_id:
            return None
        channel = self.bot.get_channel(thread_id)
        if isinstance(channel, discord.Thread):
            return channel
        try:
            fetched = await self.bot.fetch_channel(thread_id)
        except Exception:
            return None
        return fetched if isinstance(fetched, discord.Thread) else None

    def _extract_created_post(self, created: Any) -> tuple[Optional[discord.Thread], Optional[discord.Message]]:
        thread = getattr(created, "thread", None)
        message = getattr(created, "message", None)
        if isinstance(thread, discord.Thread):
            return thread, message if isinstance(message, discord.Message) else None
        if isinstance(created, tuple) and len(created) == 2:
            maybe_thread, maybe_message = created
            return (
                maybe_thread if isinstance(maybe_thread, discord.Thread) else None,
                maybe_message if isinstance(maybe_message, discord.Message) else None,
            )
        if isinstance(created, discord.Thread):
            return created, None
        return None, None

    async def _recover_messages(self, thread: discord.Thread) -> list[discord.Message]:
        bot_user = self.bot.user
        if bot_user is None:
            return []

        recovered: list[discord.Message] = []
        async for message in thread.history(limit=25, oldest_first=True):
            if message.author.id == bot_user.id:
                recovered.append(message)
        return recovered

    def _is_header_message(self, message: discord.Message) -> bool:
        return (message.content or "").strip() == THREAD_INTRO.strip()

    async def _normalize_header_message(self, thread: discord.Thread) -> None:
        try:
            starter = await thread.fetch_message(thread.id)
        except Exception:
            return

        if self._is_header_message(starter) and starter.embeds:
            try:
                await starter.edit(content=THREAD_INTRO, embeds=[])
            except Exception:
                self.logger.warning("Failed to normalize T17 index header message", exc_info=True)

    async def _ensure_thread(self, forum: discord.ForumChannel, first_batch: list[discord.Embed]) -> tuple[Optional[discord.Thread], list[discord.Message]]:
        thread = await self._get_thread(self._state.get("thread_id"))
        if thread is None or thread.parent_id != forum.id:
            create_kwargs: dict[str, Any] = {
                "name": THREAD_NAME,
                "content": THREAD_INTRO,
            }

            created = await forum.create_thread(**create_kwargs)
            thread, message = self._extract_created_post(created)
            if thread is None:
                return None, []
            if message is not None and self._is_header_message(message) and message.embeds:
                try:
                    await message.edit(content=THREAD_INTRO, embeds=[])
                except Exception:
                    self.logger.warning("Failed to normalize new T17 index header message", exc_info=True)
            self._set_state(thread_id=thread.id, message_ids=[])
            return thread, []

        if thread.archived:
            try:
                await thread.edit(archived=False)
            except Exception:
                self.logger.warning("Failed to unarchive T17 index thread", exc_info=True)

        await self._normalize_header_message(thread)

        message_ids = [int(item) for item in self._state.get("message_ids", []) if isinstance(item, int)]
        messages: list[discord.Message] = []
        for message_id in message_ids:
            try:
                message = await thread.fetch_message(message_id)
                if self._is_header_message(message):
                    continue
                messages.append(message)
            except Exception:
                self.logger.info("T17 index message %s no longer exists", message_id)

        if not messages:
            messages = [message for message in await self._recover_messages(thread) if not self._is_header_message(message)]
            self._set_state(thread_id=thread.id, message_ids=[item.id for item in messages])

        return thread, messages

    async def _sync_thread_messages(self, thread: discord.Thread, messages: list[discord.Message], batches: list[list[discord.Embed]]) -> None:
        current_messages = list(messages)
        updated_ids: list[int] = []

        for index, embeds in enumerate(batches):
            if index < len(current_messages):
                message = current_messages[index]
                await message.edit(content=None, embeds=embeds)
            else:
                message = await thread.send(embeds=embeds)
                current_messages.append(message)
            updated_ids.append(message.id)

        for message in current_messages[len(batches):]:
            try:
                await message.delete()
            except Exception:
                self.logger.warning("Failed to delete stale T17 index message %s", message.id, exc_info=True)

        self._set_state(thread_id=thread.id, message_ids=updated_ids)

    async def _build_embed_batches(self, guild: discord.Guild) -> tuple[list[list[discord.Embed]], dict[int, dict[str, str]], set[int]]:
        embeds: list[discord.Embed] = []
        current_members: dict[int, dict[str, str]] = {}
        active_member_ids: set[int] = set()
        for role_name in TRACKED_ROLE_NAMES:
            role = discord.utils.get(guild.roles, name=role_name)
            mapping: dict[str, Any] = self.lookup.empty_mapping()
            if role is not None and role.members:
                active_member_ids.update(member.id for member in role.members)
                targets, mapping, _unresolved = await self.lookup.resolve_members_for_role(role.members, role_name=role_name)
                for target in targets:
                    member_id = target.get("member_id")
                    if not isinstance(member_id, int):
                        continue
                    t17_id = str(target.get("t17_id") or "").strip()
                    if not t17_id:
                        continue
                    current_members[member_id] = {
                        "t17_id": t17_id,
                        "player_name": self._preferred_player_name(target),
                    }
            embeds.extend(self._build_role_embeds(guild, role_name, mapping))

        return self._group_embeds(embeds), current_members, active_member_ids

    async def _sync_guild_membership(
        self,
        current_members: dict[int, dict[str, str]],
        active_member_ids: set[int],
    ) -> None:
        if getattr(self.backend, "provider", "") != "bifrost":
            if not self._membership_sync_warned:
                self.logger.info("Skipping T17 guild member sync because the active backend is not Bifrost")
                self._membership_sync_warned = True
            return

        if self._membership_sync_is_cooling_down():
            self.logger.warning("Skipping T17 guild member sync because Bifrost is in a temporary error-rate cooldown")
            return

        self._clear_membership_sync_cooldown()

        previous_members = self._synced_members_state()
        confirmed_members: dict[int, dict[str, str]] = {}

        for member_id, entry in current_members.items():
            try:
                await self.backend.add_guild_member(
                    entry["t17_id"],
                    entry["player_name"],
                    platform="Xbox",
                    membership_type="community",
                )
                self.logger.info(
                    "t17_role_index_member_added member_id=%s player_id=%s player_name=%r",
                    member_id,
                    entry["t17_id"],
                    entry["player_name"],
                )
                confirmed_members[member_id] = entry
            except HLLBackendError as exc:
                self.logger.warning(
                    "t17_role_index_member_add_failed member_id=%s player_id=%s error=%s",
                    member_id,
                    entry["t17_id"],
                    exc,
                )
                if self._is_bifrost_high_error_rate_lockout(exc):
                    self._trigger_membership_sync_cooldown()
                    self.logger.warning(
                        "Pausing T17 guild member sync for %s seconds to let the Bifrost error-rate lockout clear",
                        MEMBERSHIP_SYNC_COOLDOWN_SECONDS,
                    )
                    break

        for member_id, entry in previous_members.items():
            if member_id in active_member_ids:
                continue
            try:
                await self.backend.remove_guild_member(entry["t17_id"])
                self.logger.info(
                    "t17_role_index_member_removed member_id=%s player_id=%s",
                    member_id,
                    entry["t17_id"],
                )
            except HLLBackendError as exc:
                self.logger.warning(
                    "t17_role_index_member_remove_failed member_id=%s player_id=%s error=%s",
                    member_id,
                    entry["t17_id"],
                    exc,
                )

        next_state: dict[int, dict[str, str]] = {}
        for member_id in active_member_ids:
            confirmed_entry = confirmed_members.get(member_id)
            if confirmed_entry is not None:
                next_state[member_id] = confirmed_entry
                continue
            previous_entry = previous_members.get(member_id)
            if previous_entry is not None:
                next_state[member_id] = previous_entry

        self._save_synced_members_state(next_state)

    async def _sync_index(self, *, reason: str) -> None:
        await self.bot.wait_until_ready()
        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
            self.logger.warning("T17 role index guild %s is not available", GUILD_ID)
            return

        async with self._sync_lock:
            self.logger.info("t17_role_index_sync_start reason=%s", reason)
            batches, current_members, active_member_ids = await self._build_embed_batches(guild)
            await self._sync_guild_membership(current_members, active_member_ids)

            forum = await self._get_forum_channel()
            if forum is None:
                self.logger.warning(
                    "T17 role index forum channel %s is unavailable or not a forum; membership sync still ran",
                    FORUM_CHANNEL_ID,
                )
                return

            first_batch = batches[0] if batches else []
            thread, messages = await self._ensure_thread(forum, first_batch)
            if thread is None:
                self.logger.warning("Failed to create or resolve T17 index thread")
                return
            await self._sync_thread_messages(thread, messages, batches)
            self.logger.info("t17_role_index_sync_complete reason=%s thread_id=%s", reason, thread.id)

    async def _delayed_sync(self, *, reason: str, delay: float) -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            await self._sync_index(reason=reason)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception("T17 role index sync failed")

    def _schedule_sync(self, *, reason: str, delay: float = SYNC_DEBOUNCE_SECONDS) -> None:
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()
        self._sync_task = asyncio.create_task(self._delayed_sync(reason=reason, delay=delay))

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._started:
            return
        self._started = True
        self._schedule_sync(reason="ready", delay=0.0)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.guild.id != GUILD_ID:
            return
        if self._member_tracked_roles(member):
            self._schedule_sync(reason="member_join")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.guild.id != GUILD_ID:
            return
        if self._member_tracked_roles(member):
            self._schedule_sync(reason="member_remove")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if after.guild.id != GUILD_ID:
            return

        before_roles = self._member_tracked_roles(before)
        after_roles = self._member_tracked_roles(after)
        names_changed = (
            before.name != after.name
            or before.display_name != after.display_name
            or getattr(before, "global_name", None) != getattr(after, "global_name", None)
        )

        if before_roles != after_roles:
            self._schedule_sync(reason="tracked_role_change")
            return

        if (before_roles or after_roles) and names_changed:
            self._schedule_sync(reason="tracked_member_rename")


async def setup(bot: commands.Bot):
    await bot.add_cog(T17RoleIndex(bot))