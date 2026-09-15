from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

import discord
from discord.ext import commands

from config import MAIN_GUILD_ID
from data_paths import data_path
from state_io import atomic_json_dump


LOGGER = logging.getLogger(__name__)

GUILD_ID = MAIN_GUILD_ID
OUTPUT_CHANNEL_ID = 1098316982459314279
STATE_FILE = data_path("rank_directory_state.json")
UPDATE_DEBOUNCE_SECONDS = 2.0
PAGE_DESCRIPTION_LIMIT = 3900

# Highest rank first. Role IDs are authoritative, so renamed Discord roles still
# appear in the intended position and under the intended label.
RANK_GROUPS: tuple[tuple[str, tuple[tuple[str, int], ...]], ...] = (
    (
        "General Staff",
        (
            ("O11 Field Marshal (FM)", 1098651308212359289),
            ("O10 General (Gen)", 1098651039395233793),
            ("O9 Lieutenant General (Lt.Gen)", 1098650833614274654),
            ("O8 Major General (Maj.Gen)", 1098650676705378334),
            ("O7 Brigadier (Brig)", 1098650396685254787),
        ),
    ),
    (
        "Command Staff",
        (
            ("O6 Colonel (Col)", 1098649919197286620),
            ("O5 Lieutenant Colonel (Lt. Col.)", 1098649604616110101),
            ("O4 Major (Maj)", 1098649360859926529),
            ("O3 Captain (Cpt)", 1098649240428892291),
            ("O2 Lieutenant (Lt.)", 1098649098158096454),
            ("O1 2nd Lieutenant (2Lt.)", 1098649022727733348),
        ),
    ),
    (
        "SNCO",
        (
            ("E9 Regimental Sergeant Major (RSM)", 1113172775117520936),
            ("E8 Warrant Officer 1st Class (WO1)", 1098648713410383902),
            ("E7 Warrant Officer 2nd Class (WO2)", 1098648591846879312),
        ),
    ),
    (
        "NCO",
        (
            ("E6 Sergeant Major (SGM)", 1098647943017410641),
            ("E5 Staff Sergeant (SSG)", 1098647873731706921),
        ),
    ),
    (
        "Junior Enlisted Personnel",
        (
            ("E4 Sergeant (Sgt)", 1098647785332543679),
            ("E3 Corporal (Cpl)", 1098647513399054427),
            ("E2 Lance Corporal (L.Cpl)", 1098647441013751878),
            ("E1 Private (Pte)", 1098647326882541609),
        ),
    ),
)


class RankDirectory(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._state = self._load_state()
        self._update_lock = asyncio.Lock()
        self._update_task: asyncio.Task[None] | None = None
        self._started = False

    def cog_unload(self) -> None:
        if self._update_task is not None and not self._update_task.done():
            self._update_task.cancel()

    @staticmethod
    def _tracked_role_ids() -> set[int]:
        return {role_id for _group, ranks in RANK_GROUPS for _label, role_id in ranks}

    def _load_state(self) -> dict[str, Any]:
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as handle:
                state = json.load(handle)
            return state if isinstance(state, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_message_ids(self, message_ids: list[int]) -> None:
        self._state = {
            "channel_id": OUTPUT_CHANNEL_ID,
            "message_ids": message_ids,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        atomic_json_dump(STATE_FILE, self._state)

    def _message_ids(self) -> list[int]:
        if self._state.get("channel_id") != OUTPUT_CHANNEL_ID:
            return []
        raw = self._state.get("message_ids")
        return [value for value in raw if isinstance(value, int)] if isinstance(raw, list) else []

    @staticmethod
    def _safe_display_name(member: discord.Member) -> str:
        name = " ".join(str(member.display_name or member.name).split())
        name = discord.utils.escape_mentions(name)
        return discord.utils.escape_markdown(name, as_needed=False)

    def _rank_sections(self, guild: discord.Guild) -> list[str]:
        sections: list[str] = []
        for group_name, ranks in RANK_GROUPS:
            sections.append(f"## {group_name}")
            for label, role_id in ranks:
                role = guild.get_role(role_id)
                members = [] if role is None else [member for member in role.members if not member.bot]
                members.sort(key=lambda member: (member.display_name.casefold(), member.id))
                names = "\n".join(f"- {self._safe_display_name(member)}" for member in members) or "- None"
                sections.append(f"**{label}** ({len(members)})\n{names}")
        return sections

    @staticmethod
    def _paginate_sections(sections: list[str], limit: int = PAGE_DESCRIPTION_LIMIT) -> list[str]:
        pages: list[str] = []
        current = ""
        for section in sections:
            candidate = section if not current else f"{current}\n\n{section}"
            if len(candidate) <= limit:
                current = candidate
                continue
            if current:
                pages.append(current)
            if len(section) <= limit:
                current = section
                continue

            # A single exceptionally large rank is split by member lines.
            lines = section.splitlines()
            current = ""
            for line in lines:
                candidate = line if not current else f"{current}\n{line}"
                if len(candidate) > limit and current:
                    pages.append(current)
                    current = line
                else:
                    current = candidate
        if current:
            pages.append(current)
        return pages or ["No configured ranks were found."]

    def _build_embeds(self, guild: discord.Guild) -> list[discord.Embed]:
        pages = self._paginate_sections(self._rank_sections(guild))
        now = datetime.now(timezone.utc)
        embeds: list[discord.Embed] = []
        for index, description in enumerate(pages, start=1):
            title = "7DR Rank Directory"
            if len(pages) > 1:
                title += f" ({index}/{len(pages)})"
            embed = discord.Embed(title=title, description=description, color=discord.Color.blurple(), timestamp=now)
            embed.set_footer(text="Names update automatically from Discord rank roles")
            embeds.append(embed)
        return embeds

    async def _resolve_channel(self) -> discord.TextChannel | None:
        channel = self.bot.get_channel(OUTPUT_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(OUTPUT_CHANNEL_ID)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                LOGGER.exception("Could not fetch rank directory channel %s", OUTPUT_CHANNEL_ID)
                return None
        if not isinstance(channel, discord.TextChannel):
            LOGGER.error("Rank directory channel %s is not a text channel", OUTPUT_CHANNEL_ID)
            return None
        return channel

    async def update_directory(self) -> bool:
        async with self._update_lock:
            guild = self.bot.get_guild(GUILD_ID)
            channel = await self._resolve_channel()
            if guild is None or channel is None or channel.guild.id != guild.id:
                return False

            embeds = self._build_embeds(guild)
            existing: list[discord.Message] = []
            for message_id in self._message_ids():
                try:
                    existing.append(await channel.fetch_message(message_id))
                except discord.NotFound:
                    continue
                except (discord.Forbidden, discord.HTTPException):
                    LOGGER.warning("Could not fetch rank directory message %s", message_id, exc_info=True)

            published: list[discord.Message] = []
            for index, embed in enumerate(embeds):
                if index < len(existing):
                    message = await existing[index].edit(
                        content=None,
                        embed=embed,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                else:
                    message = await channel.send(
                        embed=embed,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                published.append(message)

            for stale in existing[len(embeds):]:
                try:
                    await stale.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    LOGGER.warning("Could not delete stale rank directory message %s", stale.id, exc_info=True)

            self._save_message_ids([message.id for message in published])
            LOGGER.info("Updated rank directory with %s page(s)", len(published))
            return True

    def _schedule_update(self) -> None:
        if self._update_task is not None and not self._update_task.done():
            self._update_task.cancel()

        async def runner() -> None:
            try:
                await asyncio.sleep(UPDATE_DEBOUNCE_SECONDS)
                await self.update_directory()
            except asyncio.CancelledError:
                return
            except Exception:
                LOGGER.exception("Rank directory update failed")

        self._update_task = asyncio.create_task(runner())

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._started:
            return
        self._started = True
        await self.update_directory()

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if after.guild.id != GUILD_ID:
            return
        tracked = self._tracked_role_ids()
        before_roles = {role.id for role in before.roles if role.id in tracked}
        after_roles = {role.id for role in after.roles if role.id in tracked}
        name_changed = before.display_name != after.display_name or before.name != after.name
        if before_roles != after_roles or (after_roles and name_changed):
            self._schedule_update()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.guild.id == GUILD_ID and any(role.id in self._tracked_role_ids() for role in member.roles):
            self._schedule_update()

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.guild.id == GUILD_ID and any(role.id in self._tracked_role_ids() for role in member.roles):
            self._schedule_update()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RankDirectory(bot))
