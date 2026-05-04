#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any, Optional

import discord
import requests
from bs4 import BeautifulSoup
from discord import app_commands
from discord.ext import commands, tasks
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from clan_t17_lookup import ClanT17Lookup, DEFAULT_RANK_ORDER
from config import MAIN_GUILD_ID, data_log_path
from data_paths import data_path

GUILD_ID = MAIN_GUILD_ID
POST_CHANNEL_ID = 1500946848779862218  # channel or thread for the leaderboard message
ROLE_NAME = "131st Infantry Brigade"
HELLOR_ADMIN_ROLE_ID = 1213495462632361994

STATE_FILE = data_path("hellor_leaderboard_state.json")
LOG_FILE = data_log_path("hellor_leaderboard.log")

UPDATE_INTERVAL_SECONDS = 7 * 24 * 3600
REQUEST_PACE_SECONDS = 4
SCORE_REFRESH_INTERVAL_SECONDS = 7 * 24 * 3600

BASE_HELLOR_URL = "https://hellor.pro/player/{}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Copilot-Chat-Scraper/1.0; +https://github.com/you-shall-not-parse)"
}

RANK_ORDER: list[tuple[str, list[str]]] = DEFAULT_RANK_ORDER
LEADERBOARD_LABELS = ["Overall", "Team", "Impact", "Fight"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def make_session(retries: int = 3, backoff_factor: float = 0.5) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(HEADERS)
    return session


def extract_label_info_from_text(text: str, label: str) -> Optional[tuple[str, str]]:
    pattern = re.compile(
        rf"{re.escape(label)}\D*?(\d{{1,7}})(?:[^\d%]*)?(?:Top[:\s]*([0-9]+(?:\.[0-9]+)?)%)?",
        re.IGNORECASE,
    )
    match = pattern.search(text)
    if not match:
        return None
    raw_score = match.group(1)
    top_value = match.group(2)
    return raw_score, f"{top_value}%" if top_value else "N/A"


def find_label_nearby(soup: BeautifulSoup, label: str) -> Optional[tuple[str, str]]:
    nodes = soup.find_all(string=re.compile(rf"\b{re.escape(label)}\b", re.IGNORECASE))
    for node in nodes:
        current = node.parent
        for _ in range(4):
            if current is None:
                break
            text = current.get_text(" ", strip=True)
            found = extract_label_info_from_text(text, label)
            if found:
                return found
            current = current.parent
    return None


def parse_scores(html: str) -> dict[str, dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    full_text = soup.get_text(" ", strip=True)
    results: dict[str, dict[str, str]] = {}

    for label in LEADERBOARD_LABELS:
        found = find_label_nearby(soup, label)
        if not found:
            found = extract_label_info_from_text(full_text, label)
        results[label] = {
            "score": found[0] if found else "N/A",
            "top": found[1] if found else "N/A",
        }
    return results


def score_as_int(value: str) -> int:
    return int(value) if value.isdigit() else -1


class HellorLeaderboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session = make_session()
        self._update_lock = asyncio.Lock()
        self._synced = False
        self._initial_posted = False
        self.logger = self._build_logger()
        self.lookup = ClanT17Lookup(logger=self.logger)
        self.leaderboard_message_id = self._load_leaderboard_message_id()

    def cog_unload(self):
        if self.post_leaderboard.is_running():
            self.post_leaderboard.cancel()
        self.session.close()

    def _build_logger(self) -> logging.Logger:
        logger = logging.getLogger("HellorLeaderboard")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        existing = [
            handler
            for handler in logger.handlers
            if isinstance(handler, RotatingFileHandler) and getattr(handler, "baseFilename", None) == LOG_FILE
        ]
        if not existing:
            handler = RotatingFileHandler(LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
            handler.setLevel(logging.DEBUG)
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)

        return logger

    def _load_json_file(self, path: str) -> dict[str, Any]:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_json_file(self, path: str, data: dict[str, Any]) -> None:
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
        os.replace(tmp_path, path)

    def _load_state(self) -> dict[str, Any]:
        return self._load_json_file(STATE_FILE)

    def _save_state(self, state: dict[str, Any]) -> None:
        self._save_json_file(STATE_FILE, state)

    def _load_leaderboard_message_id(self) -> Optional[int]:
        state = self._load_state()
        if state.get("channel_id") != POST_CHANNEL_ID:
            return None
        message_id = state.get("message_id")
        return message_id if isinstance(message_id, int) else None

    def _save_leaderboard_message_id(self, message_id: Optional[int]) -> None:
        self.leaderboard_message_id = message_id
        state = self._load_state()
        state.update(
            {
                "channel_id": POST_CHANNEL_ID,
                "message_id": message_id,
                "updated_at": utc_now_iso(),
            }
        )
        self._save_state(state)

    def _empty_scores(self) -> dict[str, dict[str, str]]:
        return {label: {"score": "N/A", "top": "N/A"} for label in LEADERBOARD_LABELS}

    def _load_cached_member_scores(self) -> tuple[list[dict[str, Any]], str | None]:
        state = self._load_state()
        raw_scores = state.get("member_scores")
        scores = raw_scores if isinstance(raw_scores, list) else []
        updated_at = state.get("scores_updated_at")
        return scores, updated_at if isinstance(updated_at, str) else None

    def _save_cached_member_scores(self, member_scores: list[dict[str, Any]]) -> None:
        state = self._load_state()
        state["member_scores"] = member_scores
        state["scores_updated_at"] = utc_now_iso()
        state["role_name"] = ROLE_NAME
        state["updated_at"] = utc_now_iso()
        self._save_state(state)

    def _upsert_cached_member_score(self, member_score: dict[str, Any]) -> None:
        state = self._load_state()
        raw_scores = state.get("member_scores")
        cached_scores = list(raw_scores) if isinstance(raw_scores, list) else []

        target_member_id = member_score.get("member_id")
        target_t17_id = member_score.get("t17_id")
        kept_scores: list[dict[str, Any]] = []

        for item in cached_scores:
            same_member = item.get("member_id") == target_member_id
            same_t17 = bool(target_t17_id) and item.get("t17_id") == target_t17_id
            if same_member or same_t17:
                continue
            kept_scores.append(item)

        kept_scores.append(member_score)
        state["member_scores"] = kept_scores
        state["role_name"] = ROLE_NAME
        state["updated_at"] = utc_now_iso()
        self._save_state(state)

    def _cached_scores_stale(self, updated_at: str | None) -> bool:
        if not updated_at:
            return True

        try:
            previous = datetime.fromisoformat(updated_at)
        except ValueError:
            return True

        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=timezone.utc)

        return (utc_now() - previous.astimezone(timezone.utc)).total_seconds() >= SCORE_REFRESH_INTERVAL_SECONDS

    def _member_scores_from_cache(
        self, targets: list[dict[str, Any]], cached_scores: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        cached_by_t17: dict[str, dict[str, Any]] = {}
        for item in cached_scores:
            t17_id = item.get("t17_id")
            if isinstance(t17_id, str) and t17_id:
                cached_by_t17[t17_id] = item

        member_scores: list[dict[str, Any]] = []
        for target in targets:
            cached = cached_by_t17.get(target["t17_id"], {})
            scores = cached.get("scores")
            member_scores.append({
                **target,
                "scores": scores if isinstance(scores, dict) else self._empty_scores(),
            })

        return member_scores

    async def _get_member_scores(
        self, guild: discord.Guild, *, force_refresh: bool = False, allow_stale_cache: bool = False
    ) -> list[dict[str, Any]]:
        targets = await self._collect_basic_trained_targets(guild)
        cached_scores, updated_at = self._load_cached_member_scores()

        if force_refresh or not cached_scores or (self._cached_scores_stale(updated_at) and not allow_stale_cache):
            member_scores = await self._fetch_member_scores(targets)
            self._save_cached_member_scores(member_scores)
            self.logger.info(
                "hellor_scores_refreshed guild_id=%s resolved_targets=%s parsed_profiles=%s",
                guild.id,
                len(targets),
                len(member_scores),
            )
            return member_scores

        member_scores = self._member_scores_from_cache(targets, cached_scores)
        self.logger.info(
            "hellor_scores_cached guild_id=%s resolved_targets=%s cached_profiles=%s cached_at=%s",
            guild.id,
            len(targets),
            len(member_scores),
            updated_at,
        )
        return member_scores

    async def _fetch_single_member_score(
        self, member: discord.Member, t17_id: str, *, source: str, queries: list[str]
    ) -> dict[str, Any] | None:
        self.logger.info(
            "hellor_single_fetch_start member_id=%s display_name=%r t17_id=%s",
            member.id,
            member.display_name,
            t17_id,
        )
        try:
            html = await asyncio.to_thread(self._fetch_hellor_html, t17_id)
            scores = parse_scores(html)
        except Exception as exc:
            self.logger.exception(
                "hellor_single_fetch_failed member_id=%s display_name=%r t17_id=%s error=%s",
                member.id,
                member.display_name,
                t17_id,
                exc,
            )
            return None

        result = {
            "member_id": member.id,
            "display_name": member.display_name,
            "t17_id": t17_id,
            "source": source,
            "queries": queries,
            "scores": scores,
        }
        self.logger.info(
            "hellor_single_fetch_result member_id=%s display_name=%r t17_id=%s scores=%s",
            member.id,
            member.display_name,
            t17_id,
            scores,
        )
        return result

    def _fetch_hellor_html(self, t17_id: str) -> str:
        response = self.session.get(BASE_HELLOR_URL.format(t17_id), timeout=10)
        response.raise_for_status()
        return response.text

    async def _get_post_channel(self) -> Optional[discord.abc.Messageable]:
        channel = self.bot.get_channel(POST_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(POST_CHANNEL_ID)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return None

        if isinstance(channel, discord.Thread):
            try:
                if channel.archived:
                    await channel.edit(archived=False, locked=False)
            except Exception:
                pass

        return channel if isinstance(channel, (discord.TextChannel, discord.Thread)) else None

    def _build_empty_embed(self, description: str) -> discord.Embed:
        embed = discord.Embed(title="hellor.pro Leaderboard", description=description, color=discord.Color.orange())
        embed.timestamp = utc_now()
        return embed

    async def _collect_basic_trained_targets(self, guild: discord.Guild) -> list[dict[str, Any]]:
        role = discord.utils.get(guild.roles, name=ROLE_NAME)
        if role is None:
            raise RuntimeError(f"Role '{ROLE_NAME}' not found")

        members = sorted(role.members, key=lambda member: member.display_name.lower())
        targets, _mapping, unresolved = await self.lookup.resolve_members_for_role(
            members,
            role_name=ROLE_NAME,
            include_username=False,
        )
        self.logger.info(
            "resolve_summary guild_id=%s role=%r members=%s resolved=%s unresolved=%s",
            guild.id,
            ROLE_NAME,
            len(members),
            len(targets),
            len(unresolved),
        )
        if unresolved:
            self.logger.info("resolve_unresolved_sample guild_id=%s sample=%s", guild.id, unresolved[:20])

        return targets

    async def _fetch_member_scores(self, targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []

        for index, target in enumerate(targets):
            if index:
                await asyncio.sleep(REQUEST_PACE_SECONDS)

            self.logger.info(
                "hellor_fetch_start member_id=%s display_name=%r t17_id=%s",
                target["member_id"],
                target["display_name"],
                target["t17_id"],
            )
            try:
                html = await asyncio.to_thread(self._fetch_hellor_html, target["t17_id"])
                scores = parse_scores(html)
            except Exception as exc:
                self.logger.exception(
                    "hellor_fetch_failed member_id=%s display_name=%r t17_id=%s error=%s",
                    target["member_id"],
                    target["display_name"],
                    target["t17_id"],
                    exc,
                )
                continue

            self.logger.info(
                "hellor_parse_result member_id=%s display_name=%r t17_id=%s scores=%s",
                target["member_id"],
                target["display_name"],
                target["t17_id"],
                scores,
            )
            results.append({**target, "scores": scores})

        return results

    def _build_leaderboard_embed(self, guild: discord.Guild, member_scores: list[dict[str, Any]]) -> discord.Embed:
        embed = discord.Embed(
            title=f"hellor.pro Leaderboard - {ROLE_NAME}",
            color=discord.Color.gold(),
            timestamp=utc_now(),
        )
        embed.set_footer(text=f"Updated for {guild.name}")

        if not member_scores:
            embed.description = "No Basic trained members could be resolved to a T17 ID."
            return embed

        for label in LEADERBOARD_LABELS:
            ranking = sorted(
                member_scores,
                key=lambda item: (
                    -score_as_int(item["scores"][label]["score"]),
                    item["display_name"].lower(),
                ),
            )[:10]

            lines: list[str] = []
            for index, item in enumerate(ranking, start=1):
                score_value = item["scores"][label]["score"]
                top_value = item["scores"][label]["top"]
                suffix = f" (Top {top_value})" if top_value != "N/A" else ""
                lines.append(f"{index}. {item['display_name']} - {score_value}{suffix}")

            embed.add_field(name=label, value="\n".join(lines) if lines else "None", inline=False)

        return embed

    def _resolved_members_for_guild(self, guild_id: int) -> list[dict[str, Any]]:
        return self.lookup.resolved_members_for_role(guild_id, ROLE_NAME)

    def _member_details_lines(self, resolved: dict[str, Any], *, include_contact_line: bool = True) -> list[str]:
        lines: list[str] = []
        display = resolved.get("display_name")
        username = resolved.get("username")
        global_name = resolved.get("global_name")
        t17_id = resolved.get("t17_id")

        if display:
            lines.append(f"Display name: {display}")
        if username:
            lines.append(f"Username: {username}")
        if global_name:
            lines.append(f"Global name: {global_name}")

        if t17_id:
            url = f"https://hellor.pro/player/{t17_id}"
            lines.append(f"T17 ID: {t17_id}")
            lines.append(f"Profile: {url}")
        else:
            lines.append("T17 ID: none")

        if include_contact_line:
            lines.append("")
            lines.append("Is this wrong? Contact an admin.")

        return lines

    def _admin_member_lines(self, guild_id: int) -> list[str]:
        lines: list[str] = []
        for resolved in self._resolved_members_for_guild(guild_id):
            display = resolved.get("display_name") or resolved.get("username") or "Unknown"
            t17_id = resolved.get("t17_id")
            if t17_id:
                lines.append(f"{display}: {t17_id} | https://hellor.pro/player/{t17_id}")
            else:
                lines.append(f"{display}: none")
        return lines

    async def _send_chunked_ephemeral(self, interaction: discord.Interaction, chunks: list[str]) -> None:
        if not chunks:
            chunks = ["No records found."]

        await interaction.response.send_message(chunks[0], ephemeral=True)
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, ephemeral=True)

    def _chunk_lines(self, lines: list[str], *, max_length: int = 1800) -> list[str]:
        if not lines:
            return []

        chunks: list[str] = []
        current: list[str] = []
        current_length = 0
        for line in lines:
            addition = len(line) + (1 if current else 0)
            if current and current_length + addition > max_length:
                chunks.append("\n".join(current))
                current = [line]
                current_length = len(line)
                continue

            current.append(line)
            current_length += addition

        if current:
            chunks.append("\n".join(current))
        return chunks

    def _make_details_view(self, guild: discord.Guild) -> discord.ui.View:
        view = discord.ui.View(timeout=None)

        button = discord.ui.Button(label="Show My T17 ID", style=discord.ButtonStyle.secondary)

        async def _on_button(interaction: discord.Interaction) -> None:
            resolved = self.lookup.get_resolved_member(guild.id, interaction.user.id, role_name=ROLE_NAME)
            if not isinstance(resolved, dict):
                await interaction.response.send_message(
                    "I do not have a stored hellor record for you right now. If this looks wrong, contact an admin.",
                    ephemeral=True,
                )
                return

            await interaction.response.send_message(
                "\n".join(self._member_details_lines(resolved)),
                ephemeral=True,
            )

        button.callback = _on_button
        view.add_item(button)
        return view

    async def build_embed(self, guild: discord.Guild) -> discord.Embed:
        try:
            member_scores = await self._get_member_scores(guild)
        except RuntimeError as exc:
            self.logger.error("build_embed_failed guild_id=%s error=%s", guild.id, exc)
            return self._build_empty_embed(str(exc))
        return self._build_leaderboard_embed(guild, member_scores)

    async def update_or_post_leaderboard(self, *, force_refresh: bool = False, allow_stale_cache: bool = False) -> None:
        async with self._update_lock:
            channel = await self._get_post_channel()
            if channel is None:
                self.logger.error("post_channel_missing channel_id=%s", POST_CHANNEL_ID)
                return

            self.logger.info(
                "update_start guild_id=%s channel_id=%s existing_message_id=%s",
                channel.guild.id,
                channel.id,
                self.leaderboard_message_id,
            )
            member_scores = await self._get_member_scores(
                channel.guild,
                force_refresh=force_refresh,
                allow_stale_cache=allow_stale_cache,
            )
            embed = self._build_leaderboard_embed(channel.guild, member_scores)
            view = self._make_details_view(channel.guild)

            if self.leaderboard_message_id:
                try:
                    message = await channel.fetch_message(self.leaderboard_message_id)
                    await message.edit(embed=embed, view=view)
                    self.logger.info("update_edit_success message_id=%s", self.leaderboard_message_id)
                    return
                except discord.NotFound:
                    self.logger.info("update_existing_message_missing message_id=%s", self.leaderboard_message_id)
                    self.leaderboard_message_id = None
                except discord.Forbidden:
                    self.logger.error("update_edit_forbidden message_id=%s", self.leaderboard_message_id)
                    return
                except discord.HTTPException as exc:
                    self.logger.error("update_edit_failed message_id=%s error=%s", self.leaderboard_message_id, exc)
                    return

            message = await channel.send(embed=embed, view=view)
            self._save_leaderboard_message_id(message.id)
            self.logger.info("update_post_success message_id=%s", message.id)

    def _can_manage_leaderboard(self, interaction: discord.Interaction) -> bool:
        user = interaction.user
        return isinstance(user, discord.Member) and any(role.id == HELLOR_ADMIN_ROLE_ID for role in user.roles)

    async def refresh_member_override(self, member: discord.Member) -> None:
        guild = member.guild
        role = discord.utils.get(guild.roles, name=ROLE_NAME)
        if role is None:
            return
        if role not in member.roles:
            await self.update_or_post_leaderboard(allow_stale_cache=True)
            return

        t17_id, source, queries = await self.lookup.resolve_member_for_role(
            member,
            role_name=ROLE_NAME,
            include_username=False,
        )
        if t17_id:
            refreshed_member_score = await self._fetch_single_member_score(
                member,
                t17_id,
                source=source,
                queries=queries,
            )
            if refreshed_member_score is not None:
                self._upsert_cached_member_score(refreshed_member_score)

        await self.update_or_post_leaderboard(allow_stale_cache=True)

    @app_commands.command(name="hellor_request", description="Force a live refresh of the hellor leaderboard")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def refresh_hellor_leaderboard(self, interaction: discord.Interaction):
        if not self._can_manage_leaderboard(interaction):
            await interaction.response.send_message("You need the configured hellor admin role to use this command.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.update_or_post_leaderboard(force_refresh=True)
        except Exception as exc:
            self.logger.exception("manual_refresh_failed error=%s", exc)
            await interaction.followup.send(f"Refresh failed: {exc}", ephemeral=True)
            return

        await interaction.followup.send("Leaderboard requested and refreshed from hellor.pro.", ephemeral=True)

    @app_commands.command(name="hellor_t17idadmin", description="Show all stored hellor names and T17 IDs for the current role")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def hellor_t17idadmin(self, interaction: discord.Interaction):
        if not self._can_manage_leaderboard(interaction):
            await interaction.response.send_message("You need the configured hellor admin role to use this command.", ephemeral=True)
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        lines = self._admin_member_lines(guild.id)
        if not lines:
            await interaction.response.send_message("No stored hellor records were found for the current role.", ephemeral=True)
            return

        chunks = self._chunk_lines(lines)
        await self._send_chunked_ephemeral(interaction, chunks)

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._synced:
            try:
                await self.bot.tree.sync(guild=discord.Object(id=GUILD_ID))
                self._synced = True
            except Exception as exc:
                self.logger.exception("command_sync_failed error=%s", exc)

        if not self._initial_posted:
            try:
                await self.update_or_post_leaderboard()
            except Exception as exc:
                self.logger.exception("initial_update_failed error=%s", exc)
            self._initial_posted = True

        if not self.post_leaderboard.is_running():
            self.post_leaderboard.start()

    @tasks.loop(seconds=UPDATE_INTERVAL_SECONDS)
    async def post_leaderboard(self):
        try:
            await self.update_or_post_leaderboard(force_refresh=True)
        except Exception as exc:
            self.logger.exception("scheduled_update_failed error=%s", exc)

    @post_leaderboard.before_loop
    async def before_post_leaderboard(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(HellorLeaderboard(bot))