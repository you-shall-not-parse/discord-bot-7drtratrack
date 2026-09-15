import asyncio
import html
import io
import json
import logging
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Optional

import discord
from discord.ext import commands
from state_io import atomic_json_dump

from clan_t17_lookup import ClanT17Lookup
from config import MAIN_GUILD_ID
from data_paths import data_path
from hll_API_backend import HLLBackendError

GUILD_ID = MAIN_GUILD_ID
INDEX_CHANNEL_ID = 1549529105874165911
SYNC_NOTIFICATION_CHANNEL_ID = 1239548993751482438
STATE_FILE = data_path("t17_role_index_state.json")
INDEX_FILENAME = "t17_member_index.html"
INDEX_MESSAGE = "**T17 Member Index** — auto-updated HTML index"
SYNC_DEBOUNCE_SECONDS = 2.0
MEMBERSHIP_SYNC_COOLDOWN_SECONDS = 300
MEMBERSHIP_ADD_PACING_SECONDS = 1.0
TRACKED_ROLE_NAMES = [
    "Basic Trained",
]


class T17RoleIndex(commands.Cog, name="[API] T17RoleIndex"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger(__name__)
        self.lookup = ClanT17Lookup(logger=self.logger)
        self._sync_lock = asyncio.Lock()
        self._sync_task: asyncio.Task | None = None
        self._started = False
        self._state = self._load_state()
        self._membership_sync_warned = False
        self._pending_role_changes: dict[int, dict[str, str]] = {}

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
        atomic_json_dump(STATE_FILE, self._state)

    def _set_index_message_state(self, message: discord.Message) -> None:
        self._state["index_channel_id"] = message.channel.id
        self._state["index_message_id"] = message.id
        self._state["index_url"] = message.attachments[0].url if message.attachments else None
        # Discard state left by the old forum-thread implementation.
        self._state.pop("thread_id", None)
        self._state.pop("message_ids", None)
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

    def _trigger_membership_sync_cooldown(self, seconds: float | None = None) -> None:
        cooldown_seconds = MEMBERSHIP_SYNC_COOLDOWN_SECONDS if seconds is None else max(0.0, seconds)
        cooldown_until = asyncio.get_running_loop().time() + cooldown_seconds
        self._set_membership_sync_cooldown(cooldown_until)

    def _is_bifrost_high_error_rate_lockout(self, error: BaseException) -> bool:
        message = str(error or "").casefold()
        return "high error rate" in message and "restored automatically" in message

    def _tracked_role_names(self) -> set[str]:
        return set(TRACKED_ROLE_NAMES)

    def _member_tracked_roles(self, member: discord.Member) -> set[str]:
        tracked = self._tracked_role_names()
        return {role.name for role in member.roles if role.name in tracked}

    def _build_role_rows(self, guild: discord.Guild, role_name: str, mapping: dict[str, Any]) -> list[dict[str, str]]:
        role = discord.utils.get(guild.roles, name=role_name)
        if role is None:
            return []

        members = sorted(role.members, key=lambda item: item.display_name.casefold())
        rows: list[dict[str, str]] = []
        for member in members:
            key = self.lookup.resolved_member_key(guild.id, member.id, role_name)
            entry = mapping.get("resolved_members", {}).get(key)
            t17_id = ""
            if isinstance(entry, dict) and entry.get("t17_id"):
                t17_id = str(entry["t17_id"])
            rows.append(
                {
                    "username": self.lookup.normalize_discord_username(member.name) or member.name,
                    "nickname": self.lookup.cut_at_hash(member.display_name) or member.display_name or member.name,
                    "t17_id": t17_id,
                }
            )
        return rows

    @staticmethod
    def _render_index_html(role_rows: dict[str, list[dict[str, str]]]) -> str:
        sections: list[str] = []
        total_members = 0
        for role_name, rows in role_rows.items():
            total_members += len(rows)
            body_rows: list[str] = []
            for row in rows:
                t17_id = row["t17_id"]
                if t17_id:
                    player_id = urllib.parse.quote(t17_id, safe="")
                    t17_cell = (
                        f'<a href="https://www.hllrecords.com/profiles/{player_id}">'
                        f"{html.escape(t17_id)}</a>"
                    )
                else:
                    t17_cell = '<span class="unknown">Unknown</span>'
                body_rows.append(
                    "<tr>"
                    f"<td>{html.escape(row['username'])}</td>"
                    f"<td>{html.escape(row['nickname'])}</td>"
                    f"<td>{t17_cell}</td>"
                    "</tr>"
                )
            if not body_rows:
                body_rows.append('<tr><td colspan="3" class="empty">No members currently have this role.</td></tr>')
            sections.append(
                f"<section><h2>{html.escape(role_name)} <span>{len(rows)}</span></h2>"
                "<div class=\"table-wrap\"><table><thead><tr>"
                "<th>Discord name</th><th>Nickname</th><th>T17 ID</th>"
                f"</tr></thead><tbody>{''.join(body_rows)}</tbody></table></div></section>"
            )

        updated = datetime.now(timezone.utc).strftime("%d %B %Y at %H:%M UTC")
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>T17 Member Index</title>
  <style>
    :root {{ color-scheme: dark; --bg:#10120e; --panel:#191d16; --line:#343b2d; --text:#f2f4ed; --muted:#aeb6a4; --accent:#b7c98b; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif; }}
    main {{ width:min(1050px,calc(100% - 28px)); margin:42px auto; }}
    header {{ margin-bottom:28px; }}
    h1 {{ margin:0 0 6px; font-size:clamp(2rem,5vw,3.4rem); letter-spacing:-.04em; }}
    p {{ margin:0; color:var(--muted); }}
    section {{ margin-top:24px; padding:20px; background:var(--panel); border:1px solid var(--line); border-radius:14px; }}
    h2 {{ margin:0 0 15px; font-size:1.25rem; }}
    h2 span {{ margin-left:7px; padding:2px 8px; border-radius:999px; background:var(--line); color:var(--accent); font-size:.8rem; }}
    .table-wrap {{ overflow-x:auto; }}
    table {{ width:100%; border-collapse:collapse; }}
    th,td {{ padding:11px 13px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }}
    th {{ color:var(--muted); font-size:.75rem; letter-spacing:.08em; text-transform:uppercase; }}
    tbody tr:last-child td {{ border-bottom:0; }}
    a {{ color:var(--accent); }}
    .unknown,.empty {{ color:var(--muted); }}
    footer {{ margin-top:18px; color:var(--muted); font-size:.85rem; }}
  </style>
</head>
<body>
  <main>
    <header><h1>T17 Member Index</h1><p>{total_members} tracked members</p></header>
    {''.join(sections)}
    <footer>Automatically updated {html.escape(updated)}</footer>
  </main>
</body>
</html>"""

    def _preferred_player_name(self, target: dict[str, Any]) -> str:
        queries = target.get("queries")
        if isinstance(queries, list):
            for query in queries:
                value = str(query or "").strip()
                if value:
                    return value
        return str(target.get("display_name") or "").strip() or str(target.get("t17_id") or "").strip()

    async def _get_index_channel(self) -> Any | None:
        channel = self.bot.get_channel(INDEX_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(INDEX_CHANNEL_ID)
            except Exception:
                self.logger.exception("Failed to fetch T17 index channel")
                return None
        if not hasattr(channel, "send") or not hasattr(channel, "fetch_message"):
            self.logger.warning("T17 index channel %s is not messageable", INDEX_CHANNEL_ID)
            return None
        return channel

    async def _publish_index_html(self, document: str) -> Optional[discord.Message]:
        channel = await self._get_index_channel()
        if channel is None:
            return None

        message = None
        message_id = self._state.get("index_message_id")
        channel_id = self._state.get("index_channel_id")
        if isinstance(message_id, int) and channel_id == INDEX_CHANNEL_ID:
            try:
                message = await channel.fetch_message(message_id)
            except discord.NotFound:
                pass
            except Exception:
                self.logger.warning("Failed to fetch existing T17 index message", exc_info=True)

        attachment = discord.File(io.BytesIO(document.encode("utf-8")), filename=INDEX_FILENAME)
        if message is None:
            message = await channel.send(
                content=INDEX_MESSAGE,
                file=attachment,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            message = await message.edit(
                content=INDEX_MESSAGE,
                attachments=[attachment],
                allowed_mentions=discord.AllowedMentions.none(),
            )

        # Save immediately after the upload so a later content-edit failure does
        # not cause a duplicate index message on the next refresh.
        self._set_index_message_state(message)
        if message.attachments:
            linked_content = f"{INDEX_MESSAGE}\n{message.attachments[0].url}"
            if message.content != linked_content:
                message = await message.edit(
                    content=linked_content,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                self._set_index_message_state(message)
        return message

    async def _build_index_document(self, guild: discord.Guild) -> tuple[str, dict[int, dict[str, str]], set[int]]:
        role_rows: dict[str, list[dict[str, str]]] = {}
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
            role_rows[role_name] = self._build_role_rows(guild, role_name, mapping)

        return self._render_index_html(role_rows), current_members, active_member_ids

    async def _sync_guild_membership(
        self,
        current_members: dict[int, dict[str, str]],
        active_member_ids: set[int],
        *,
        reason: str,
    ) -> dict[int, tuple[bool, str]]:
        del reason

        previous_members = self._synced_members_state()
        next_state = dict(previous_members)
        results: dict[int, tuple[bool, str]] = {}
        members_to_upsert: list[tuple[int, dict[str, str], dict[str, str] | None]] = []
        members_to_remove: list[tuple[int, dict[str, str]]] = []

        for member_id in active_member_ids:
            current_entry = current_members.get(member_id)
            previous_entry = previous_members.get(member_id)
            if current_entry is None:
                results[member_id] = (False, "No T17 ID could be resolved.")
                continue
            if (
                previous_entry is not None
                and previous_entry.get("t17_id") == current_entry.get("t17_id")
                and previous_entry.get("player_name") == current_entry.get("player_name")
            ):
                results[member_id] = (
                    True,
                    f"Already synchronized as T17 `{current_entry['t17_id']}`.",
                )
                continue
            members_to_upsert.append((member_id, current_entry, previous_entry))

        for member_id, previous_entry in previous_members.items():
            if member_id not in active_member_ids:
                members_to_remove.append((member_id, previous_entry))

        if not members_to_upsert and not members_to_remove:
            return results

        try:
            backend = self.lookup.backend
        except HLLBackendError as exc:
            if not self._membership_sync_warned:
                self.logger.warning("Skipping T17 guild member sync: %s", exc)
                self._membership_sync_warned = True
            detail = f"Bifrost synchronization unavailable: {exc}"
            for member_id, _entry, _previous in members_to_upsert:
                results[member_id] = (False, detail)
            for member_id, _entry in members_to_remove:
                results[member_id] = (False, detail)
            return results

        if getattr(backend, "provider", "") != "bifrost":
            if not self._membership_sync_warned:
                self.logger.info("Skipping T17 guild member sync because the active backend is not Bifrost")
                self._membership_sync_warned = True
            detail = "Bifrost synchronization unavailable because the active backend is not Bifrost."
            for member_id, _entry, _previous in members_to_upsert:
                results[member_id] = (False, detail)
            for member_id, _entry in members_to_remove:
                results[member_id] = (False, detail)
            return results

        if self._membership_sync_is_cooling_down():
            self.logger.warning("Skipping T17 guild member sync because Bifrost is in a temporary error-rate cooldown")
            detail = "Bifrost synchronization is temporarily cooling down after an API error."
            for member_id, _entry, _previous in members_to_upsert:
                results[member_id] = (False, detail)
            for member_id, _entry in members_to_remove:
                results[member_id] = (False, detail)
            return results

        self._clear_membership_sync_cooldown()

        for index, (member_id, entry, previous_entry) in enumerate(members_to_upsert):
            if index > 0:
                await asyncio.sleep(MEMBERSHIP_ADD_PACING_SECONDS)
            try:
                if previous_entry is not None and previous_entry.get("t17_id") != entry.get("t17_id"):
                    await backend.remove_guild_member(previous_entry["t17_id"])
                await backend.add_guild_member(
                    entry["t17_id"],
                    entry["player_name"],
                    platform="Xbox",
                )
                self.logger.info(
                    "t17_role_index_member_added member_id=%s player_id=%s player_name=%r",
                    member_id,
                    entry["t17_id"],
                    entry["player_name"],
                )
                next_state[member_id] = entry
                results[member_id] = (
                    True,
                    f"Synchronized with Bifrost as T17 `{entry['t17_id']}`.",
                )
            except Exception as exc:
                self.logger.warning(
                    "t17_role_index_member_add_failed member_id=%s player_id=%s error=%s",
                    member_id,
                    entry["t17_id"],
                    exc,
                )
                results[member_id] = (False, f"Bifrost add/update failed: {exc}")
                retry_after = getattr(exc, "retry_after", None)
                if retry_after is not None:
                    self._trigger_membership_sync_cooldown(retry_after)
                    self.logger.warning(
                        "Pausing T17 guild member sync for %s seconds to honor the Bifrost retry window",
                        retry_after,
                    )
                    break
                if self._is_bifrost_high_error_rate_lockout(exc):
                    self._trigger_membership_sync_cooldown()
                    self.logger.warning(
                        "Pausing T17 guild member sync for %s seconds to let the Bifrost error-rate lockout clear",
                        MEMBERSHIP_SYNC_COOLDOWN_SECONDS,
                    )
                    break

        for member_id, entry in members_to_remove:
            try:
                await backend.remove_guild_member(entry["t17_id"])
                self.logger.info(
                    "t17_role_index_member_removed member_id=%s player_id=%s",
                    member_id,
                    entry["t17_id"],
                )
                next_state.pop(member_id, None)
                results[member_id] = (
                    True,
                    f"Removed T17 `{entry['t17_id']}` from Bifrost membership.",
                )
            except Exception as exc:
                self.logger.warning(
                    "t17_role_index_member_remove_failed member_id=%s player_id=%s error=%s",
                    member_id,
                    entry["t17_id"],
                    exc,
                )
                results[member_id] = (False, f"Bifrost removal failed: {exc}")

        for member_id, _entry, _previous in members_to_upsert:
            results.setdefault(
                member_id,
                (False, "Bifrost synchronization was not attempted because an earlier request failed."),
            )
        for member_id, _entry in members_to_remove:
            results.setdefault(
                member_id,
                (False, "Bifrost synchronization was not attempted because an earlier request failed."),
            )

        self._save_synced_members_state(next_state)
        return results

    async def _post_role_change_results(
        self,
        changes: dict[int, dict[str, str]],
        results: dict[int, tuple[bool, str]],
    ) -> None:
        if not changes:
            return

        channel = self.bot.get_channel(SYNC_NOTIFICATION_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(SYNC_NOTIFICATION_CHANNEL_ID)
            except Exception:
                self.logger.exception(
                    "Failed to fetch T17 sync notification channel %s",
                    SYNC_NOTIFICATION_CHANNEL_ID,
                )
                return
        if not hasattr(channel, "send"):
            self.logger.warning(
                "T17 sync notification channel %s is not messageable",
                SYNC_NOTIFICATION_CHANNEL_ID,
            )
            return

        for member_id, change in changes.items():
            action = change.get("action") or "changed"
            display_name = discord.utils.escape_markdown(change.get("display_name") or str(member_id))
            success, detail = results.get(
                member_id,
                (
                    action == "removed",
                    "Role removal indexed; no synchronized Bifrost membership was recorded."
                    if action == "removed"
                    else "The role change was indexed, but no synchronization result was produced.",
                ),
            )
            marker = "✅ SUCCESS" if success else "❌ FAIL"
            action_label = "added to" if action == "added" else "removed from"
            message = (
                f"{marker} — **Basic Trained {action}**\n"
                f"**{display_name}** (`{member_id}`) was {action_label} the Basic Trained role.\n"
                f"{detail}"
            )
            try:
                await channel.send(
                    message,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                self.logger.exception(
                    "Failed to post T17 sync result for member %s to channel %s",
                    member_id,
                    SYNC_NOTIFICATION_CHANNEL_ID,
                )

    async def _sync_index(self, *, reason: str) -> None:
        await self.bot.wait_until_ready()
        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
            self.logger.warning("T17 role index guild %s is not available", GUILD_ID)
            return

        async with self._sync_lock:
            self.logger.info("t17_role_index_sync_start reason=%s", reason)
            pending_changes = dict(self._pending_role_changes)
            self._pending_role_changes.clear()
            try:
                document, current_members, active_member_ids = await self._build_index_document(guild)
                sync_results = await self._sync_guild_membership(
                    current_members,
                    active_member_ids,
                    reason=reason,
                )
            except asyncio.CancelledError:
                self._pending_role_changes = pending_changes | self._pending_role_changes
                raise
            except Exception as exc:
                failure_results = {
                    member_id: (False, f"T17 index synchronization failed: {exc}")
                    for member_id in pending_changes
                }
                await self._post_role_change_results(pending_changes, failure_results)
                raise

            await self._post_role_change_results(pending_changes, sync_results)

            message = await self._publish_index_html(document)
            if message is None:
                self.logger.warning(
                    "T17 index channel %s is unavailable; membership sync still ran",
                    INDEX_CHANNEL_ID,
                )
                return
            self.logger.info("t17_role_index_sync_complete reason=%s message_id=%s", reason, message.id)

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

    async def refresh_member_override(self, member: discord.Member) -> None:
        if member.guild.id != GUILD_ID:
            return
        if "Basic Trained" not in self._member_tracked_roles(member):
            return
        await self._sync_index(reason="manual_override")

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
            if "Basic Trained" in after_roles - before_roles:
                self._pending_role_changes[after.id] = {
                    "action": "added",
                    "display_name": after.display_name,
                }
            elif "Basic Trained" in before_roles - after_roles:
                self._pending_role_changes[after.id] = {
                    "action": "removed",
                    "display_name": after.display_name,
                }
            self._schedule_sync(reason="tracked_role_change")
            return

        if (before_roles or after_roles) and names_changed:
            self._schedule_sync(reason="tracked_member_rename")


async def setup(bot: commands.Bot):
    await bot.add_cog(T17RoleIndex(bot))
