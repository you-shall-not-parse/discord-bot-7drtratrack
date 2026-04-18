from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import DOCS_FORUM_CHANNEL_ID, DOCS_FORUM_TAG_NAME, MAIN_GUILD_ID
from data_paths import data_path


GUILD_ID = MAIN_GUILD_ID
DOCS_ADMIN_ROLE_ID = 1213495462632361994
THREAD_NAME = "Ratbot Guide"
STATE_FILE = data_path("ratbot_guide_state.json")
README_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "README.md"))
HOWTO_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "COG_HOWTO.md"))
SYNC_INTERVAL_MINUTES = 5


def _can_manage_docs(interaction: discord.Interaction) -> bool:
    user = interaction.user
    return isinstance(user, discord.Member) and any(role.id == DOCS_ADMIN_ROLE_ID for role in user.roles)


@dataclass
class ManagedSection:
    key: str
    title: str
    content: str


class DocSync(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("RatbotGuideSync")
        self._sync_lock = asyncio.Lock()
        self._state = self._load_state()
        self._initial_sync_done = False

    def cog_unload(self) -> None:
        if self.watch_docs.is_running():
            self.watch_docs.cancel()

    def _load_state(self) -> dict:
        try:
            if not os.path.exists(STATE_FILE):
                return {}
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            self.logger.exception("Failed to load ratbot guide state")
            return {}

    def _save_state(self) -> None:
        tmp_path = STATE_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self._state, f, indent=2)
        os.replace(tmp_path, STATE_FILE)

    def _read_text(self, path: str) -> str:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()

    def _file_hash(self, path: str) -> str:
        return hashlib.sha256(self._read_text(path).encode("utf-8")).hexdigest()

    def _normalize_title(self, title: str) -> str:
        return " ".join((title or "").split())

    def _parse_markdown_sections(self, text: str, *, prefix: str, default_title: str) -> list[ManagedSection]:
        sections: list[ManagedSection] = []
        current_title = default_title
        current_lines: list[str] = []

        for line in text.splitlines():
            if line.startswith("## "):
                body = "\n".join(current_lines).strip()
                if body:
                    key = f"{prefix}:{current_title.lower().replace(' ', '_').replace('`', '')}"
                    sections.append(ManagedSection(key=key, title=current_title, content=body))
                current_title = line[3:].strip()
                current_lines = []
                continue
            current_lines.append(line)

        body = "\n".join(current_lines).strip()
        if body:
            key = f"{prefix}:{current_title.lower().replace(' ', '_').replace('`', '')}"
            sections.append(ManagedSection(key=key, title=current_title, content=body))
        return sections

    def _chunk_section(self, section: ManagedSection, limit: int = 1800) -> list[ManagedSection]:
        text = section.content.strip()
        if len(text) <= limit:
            return [section]

        parts: list[str] = []
        paragraphs = text.split("\n\n")
        current = ""
        for paragraph in paragraphs:
            candidate = paragraph if not current else f"{current}\n\n{paragraph}"
            if len(candidate) <= limit:
                current = candidate
                continue
            if current:
                parts.append(current)
            if len(paragraph) <= limit:
                current = paragraph
                continue
            start = 0
            while start < len(paragraph):
                end = min(start + limit, len(paragraph))
                parts.append(paragraph[start:end])
                start = end
            current = ""
        if current:
            parts.append(current)

        if len(parts) <= 1:
            return [section]

        chunked: list[ManagedSection] = []
        total = len(parts)
        for index, part in enumerate(parts, start=1):
            chunked.append(
                ManagedSection(
                    key=f"{section.key}:part{index}",
                    title=f"{section.title} ({index}/{total})",
                    content=part,
                )
            )
        return chunked

    def _build_sections(self) -> list[ManagedSection]:
        readme_sections = self._parse_markdown_sections(self._read_text(README_PATH), prefix="readme", default_title="Overview")
        howto_sections = self._parse_markdown_sections(self._read_text(HOWTO_PATH), prefix="howto", default_title="How-To")

        sections: list[ManagedSection] = []
        for section in readme_sections:
            sections.extend(self._chunk_section(section))
        for section in howto_sections:
            sections.extend(self._chunk_section(section))
        return sections

    def _render_section_message(self, section: ManagedSection) -> str:
        return f"**{section.title}**\n\n{section.content}".strip()

    async def _get_forum_channel(self) -> Optional[discord.ForumChannel]:
        channel = self.bot.get_channel(DOCS_FORUM_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(DOCS_FORUM_CHANNEL_ID)
            except Exception:
                self.logger.exception("Failed to fetch docs forum channel")
                return None
        return channel if isinstance(channel, discord.ForumChannel) else None

    async def _get_thread(self, thread_id: int) -> Optional[discord.Thread]:
        channel = self.bot.get_channel(thread_id)
        if isinstance(channel, discord.Thread):
            return channel
        try:
            fetched = await self.bot.fetch_channel(thread_id)
        except Exception:
            return None
        return fetched if isinstance(fetched, discord.Thread) else None

    def _normalize_tag_name(self, value: str) -> str:
        return " ".join((value or "").split()).casefold()

    def _find_forum_tag(self, forum: discord.ForumChannel, *, tag_name: str) -> Optional[discord.ForumTag]:
        target = self._normalize_tag_name(tag_name)
        if not target:
            return None

        for tag in forum.available_tags:
            if self._normalize_tag_name(tag.name) == target:
                return tag
        return None

    def _can_create_forum_tags(self, forum: discord.ForumChannel) -> bool:
        bot_user = self.bot.user
        if bot_user is None:
            return False
        member = forum.guild.get_member(bot_user.id)
        if member is None:
            return False
        return forum.permissions_for(member).manage_channels

    async def _resolve_docs_tag(self, forum: discord.ForumChannel) -> list[discord.ForumTag]:
        existing = self._find_forum_tag(forum, tag_name=DOCS_FORUM_TAG_NAME)
        if existing is not None:
            return [existing]

        if self._can_create_forum_tags(forum):
            try:
                created = await forum.create_tag(name=DOCS_FORUM_TAG_NAME)
                return [created]
            except Exception:
                self.logger.warning("Failed to create docs forum tag '%s'", DOCS_FORUM_TAG_NAME, exc_info=True)
                existing = self._find_forum_tag(forum, tag_name=DOCS_FORUM_TAG_NAME)
                if existing is not None:
                    return [existing]

        if forum.available_tags:
            return [forum.available_tags[0]]
        return []

    def _extract_created_post(self, created) -> tuple[Optional[discord.Thread], Optional[discord.Message]]:
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

    async def _ensure_thread(self, starter_content: str) -> tuple[Optional[discord.Thread], Optional[discord.Message]]:
        forum = await self._get_forum_channel()
        if forum is None:
            return None, None

        thread_id = self._state.get("thread_id")
        starter_message_id = self._state.get("starter_message_id")

        if isinstance(thread_id, int):
            thread = await self._get_thread(thread_id)
            if thread is not None:
                try:
                    if thread.archived:
                        await thread.edit(archived=False, locked=False)
                except Exception:
                    pass

                starter_message = None
                if isinstance(starter_message_id, int):
                    try:
                        starter_message = await thread.fetch_message(starter_message_id)
                    except Exception:
                        starter_message = None
                return thread, starter_message

        try:
            applied_tags = await self._resolve_docs_tag(forum)
            created = await forum.create_thread(
                name=THREAD_NAME,
                content=starter_content,
                applied_tags=applied_tags,
                auto_archive_duration=10080,
            )
        except Exception:
            self.logger.exception("Failed to create Ratbot Guide forum thread")
            return None, None

        thread, message = self._extract_created_post(created)
        if thread is None:
            return None, None
        self._state["thread_id"] = thread.id
        if message is not None:
            self._state["starter_message_id"] = message.id
        self._save_state()
        return thread, message

    async def sync_docs(self, *, force: bool = False) -> tuple[bool, str]:
        async with self._sync_lock:
            try:
                readme_hash = self._file_hash(README_PATH)
                howto_hash = self._file_hash(HOWTO_PATH)
            except Exception as exc:
                self.logger.exception("Failed to hash docs files")
                return False, f"Failed to read docs: {exc}"

            last_hashes = self._state.get("source_hashes", {})
            if not force and last_hashes.get("readme") == readme_hash and last_hashes.get("howto") == howto_hash:
                return True, "Ratbot Guide already up to date."

            sections = self._build_sections()
            if not sections:
                return False, "No documentation sections found to sync."

            starter_content = self._render_section_message(sections[0])
            thread, starter_message = await self._ensure_thread(starter_content)
            if thread is None:
                return False, "Could not access or create the docs forum thread."

            if starter_message is None:
                starter_message_id = self._state.get("starter_message_id")
                if isinstance(starter_message_id, int):
                    try:
                        starter_message = await thread.fetch_message(starter_message_id)
                    except Exception:
                        starter_message = None

            if starter_message is not None:
                try:
                    await starter_message.edit(content=starter_content)
                except Exception:
                    self.logger.exception("Failed to edit starter message for Ratbot Guide")

            message_ids = self._state.get("message_ids", {})
            if not isinstance(message_ids, dict):
                message_ids = {}

            # Rebuild managed follow-up posts in order when source docs change.
            for message_id in list(message_ids.values()):
                if not isinstance(message_id, int):
                    continue
                try:
                    message = await thread.fetch_message(message_id)
                    await message.delete()
                except Exception:
                    pass

            message_ids = {}
            for section in sections[1:]:
                message = await thread.send(self._render_section_message(section))
                message_ids[section.key] = message.id

            self._state["message_ids"] = message_ids
            self._state["source_hashes"] = {"readme": readme_hash, "howto": howto_hash}
            self._save_state()
            return True, f"Ratbot Guide synced to {thread.jump_url}"

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self._initial_sync_done:
            ok, message = await self.sync_docs(force=True)
            if ok:
                self.logger.info(message)
            else:
                self.logger.warning(message)
            self._initial_sync_done = True

        if not self.watch_docs.is_running():
            self.watch_docs.start()

    @tasks.loop(minutes=SYNC_INTERVAL_MINUTES)
    async def watch_docs(self) -> None:
        ok, message = await self.sync_docs(force=False)
        if not ok:
            self.logger.warning(message)

    @watch_docs.before_loop
    async def before_watch_docs(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="sync_ratbotguide", description="Sync the Ratbot Guide forum thread from README and COG_HOWTO")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.check(_can_manage_docs)
    async def sync_ratbotguide(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok, message = await self.sync_docs(force=True)
        await interaction.followup.send(message, ephemeral=True)

    @sync_ratbotguide.error
    async def sync_ratbotguide_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CheckFailure):
            if interaction.response.is_done():
                await interaction.followup.send("You need the configured docs admin role to use this command.", ephemeral=True)
            else:
                await interaction.response.send_message("You need the configured docs admin role to use this command.", ephemeral=True)
            return
        self.logger.exception("sync_ratbotguide failed: %s", error)
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DocSync(bot))