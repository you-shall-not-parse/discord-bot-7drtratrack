from __future__ import annotations

import asyncio
import html
import io
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
HTML_FILENAME = "7dr_rank_directory.html"
MESSAGE_HEADING = "**Full 7DR rank directory (HTML):**"

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
    def _members_for_role(guild: discord.Guild, role_id: int) -> list[discord.Member]:
        role = guild.get_role(role_id)
        members = [] if role is None else [member for member in role.members if not member.bot]
        members.sort(key=lambda member: (member.display_name.casefold(), member.id))
        return members

    def _build_summary_embed(self, guild: discord.Guild) -> discord.Embed:
        unique_member_ids: set[int] = set()
        assignment_count = 0
        embed = discord.Embed(
            title="7DR Rank Directory",
            description="Full rank breakdown by current Discord role membership. Open the attached HTML directory for member names.",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        for group_name, ranks in RANK_GROUPS:
            lines: list[str] = []
            for label, role_id in ranks:
                members = self._members_for_role(guild, role_id)
                assignment_count += len(members)
                unique_member_ids.update(member.id for member in members)
                lines.append(f"**{label} ({len(members)})**")
            embed.add_field(name=group_name, value="\n".join(lines), inline=False)
        embed.set_footer(
            text=(
                f"{len(unique_member_ids)} unique members • {assignment_count} rank assignments • "
                "Updates automatically"
            )
        )
        return embed

    def _render_html(self, guild: discord.Guild) -> str:
        group_sections: list[str] = []
        unique_member_ids: set[int] = set()
        assignment_count = 0
        for group_name, ranks in RANK_GROUPS:
            rank_sections: list[str] = []
            for label, role_id in ranks:
                members = self._members_for_role(guild, role_id)
                assignment_count += len(members)
                unique_member_ids.update(member.id for member in members)
                member_items = "".join(
                    f"<li>{html.escape(' '.join(str(member.display_name or member.name).split()))}</li>"
                    for member in members
                ) or '<li class="empty">None</li>'
                rank_sections.append(
                    f'<section class="rank"><h3>{html.escape(label)} <span>{len(members)}</span></h3>'
                    f"<ul>{member_items}</ul></section>"
                )
            group_sections.append(
                f'<section class="group"><h2>{html.escape(group_name)}</h2>{"".join(rank_sections)}</section>'
            )

        updated = datetime.now(timezone.utc).strftime("%d %B %Y at %H:%M UTC")
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>7DR Rank Directory</title>
  <style>
    :root {{ color-scheme:dark; --bg:#10120e; --panel:#191d16; --line:#343b2d; --text:#f2f4ed; --muted:#aeb6a4; --accent:#b7c98b; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif; }}
    main {{ width:min(1100px,calc(100% - 28px)); margin:40px auto; }}
    header {{ margin-bottom:28px; }}
    h1 {{ margin:0 0 6px; font-size:clamp(2rem,5vw,3.4rem); letter-spacing:-.04em; }}
    header p,footer,.empty {{ color:var(--muted); }}
    .group {{ margin:24px 0; padding:20px; border:1px solid var(--line); border-radius:14px; background:var(--panel); }}
    h2 {{ margin:0 0 18px; color:var(--accent); }}
    .rank {{ padding:13px 0; border-top:1px solid var(--line); }}
    .rank:first-of-type {{ border-top:0; padding-top:0; }}
    h3 {{ margin:0 0 8px; font-size:1rem; }}
    h3 span {{ margin-left:7px; padding:2px 8px; border-radius:999px; background:var(--line); color:var(--accent); font-size:.78rem; }}
    ul {{ columns:3 220px; margin:0; padding-left:20px; }}
    li {{ padding:2px 12px 2px 0; break-inside:avoid; }}
    footer {{ margin-top:18px; font-size:.85rem; }}
  </style>
</head>
<body>
  <main>
    <header><h1>7DR Rank Directory</h1><p>{len(unique_member_ids)} unique members across {assignment_count} rank assignments</p></header>
    {''.join(group_sections)}
    <footer>Automatically updated {html.escape(updated)}</footer>
  </main>
</body>
</html>"""

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

            embed = self._build_summary_embed(guild)
            attachment = discord.File(io.BytesIO(self._render_html(guild).encode("utf-8")), filename=HTML_FILENAME)
            existing: list[discord.Message] = []
            for message_id in self._message_ids():
                try:
                    existing.append(await channel.fetch_message(message_id))
                except discord.NotFound:
                    continue
                except (discord.Forbidden, discord.HTTPException):
                    LOGGER.warning("Could not fetch rank directory message %s", message_id, exc_info=True)

            if existing:
                message = await existing[0].edit(
                    content=MESSAGE_HEADING,
                    embed=embed,
                    attachments=[attachment],
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                message = await channel.send(
                    content=MESSAGE_HEADING,
                    embed=embed,
                    file=attachment,
                    allowed_mentions=discord.AllowedMentions.none(),
                )

            # Save immediately after the file upload to avoid duplicates if the
            # optional link-content edit fails.
            self._save_message_ids([message.id])
            if message.attachments:
                linked_content = f"{MESSAGE_HEADING}\n{message.attachments[0].url}"
                if message.content != linked_content:
                    message = await message.edit(
                        content=linked_content,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )

            for stale in existing[1:]:
                try:
                    await stale.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    LOGGER.warning("Could not delete stale rank directory message %s", stale.id, exc_info=True)

            self._save_message_ids([message.id])
            LOGGER.info("Updated rank directory summary and HTML attachment")
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
