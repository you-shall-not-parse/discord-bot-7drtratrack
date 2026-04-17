import asyncio
import json
import os
import urllib.parse
from datetime import datetime, timezone

import discord
from discord.ext import commands

from clan_t17_lookup import ClanT17Lookup, DEFAULT_RANK_ORDER
from data_paths import data_path

GUILD_ID = 1097913605082579024
OUTPUT_CHANNEL_ID = 1459904650831724806
STATE_FILE = data_path("rosterizer_state.json")
UPDATE_DEBOUNCE_SECONDS = 2.0
INCLUDE_HLLRECORDS_LINK = False

ROSTER_DEFINITIONS = [
    {
        "key": "hell_eu_s4",
        "title": "Hell EU S4",
        "role_id": 1364639604564688917,
    }
]

RANK_ORDER: list[tuple[str, list[str]]] = DEFAULT_RANK_ORDER


class Rosterizer(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.lookup = ClanT17Lookup()
        self._ran_once = False
        self._update_task: asyncio.Task | None = None
        self._update_lock = asyncio.Lock()
        self._state = self._load_state()

    def _load_state(self) -> dict:
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_state(self) -> None:
        os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as handle:
            json.dump(self._state, handle, indent=2)

    def _guild_state(self, guild_id: int) -> dict:
        return self._state.setdefault(str(guild_id), {}).setdefault("rosters", {})

    def _roster_state(self, guild_id: int, roster_key: str) -> dict:
        return self._guild_state(guild_id).setdefault(roster_key, {})

    def _get_roster_message_ids(self, guild_id: int, roster_key: str) -> list[int]:
        state = self._roster_state(guild_id, roster_key)
        raw_ids = state.get("message_ids", [])
        return [value for value in raw_ids if isinstance(value, int)]

    def _set_roster_message_ids(self, guild_id: int, roster_key: str, message_ids: list[int]) -> None:
        state = self._roster_state(guild_id, roster_key)
        state["message_ids"] = message_ids
        state["channel_id"] = OUTPUT_CHANNEL_ID
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._save_state()

    async def _resolve_output_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        channel = guild.get_channel(OUTPUT_CHANNEL_ID) or self.bot.get_channel(OUTPUT_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(OUTPUT_CHANNEL_ID)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return None
        return channel if isinstance(channel, discord.TextChannel) else None

    def _rank_index_from_display_name(self, display_name: str) -> int:
        if not display_name:
            return 10_000

        text = display_name.strip()
        if "#" in text:
            text = text.split("#", 1)[0].strip()
        text = " ".join(text.split())
        text_lower = text.lower()

        best_order: int | None = None
        best_len = -1
        for order_idx, (_code, variants) in enumerate(RANK_ORDER):
            for prefix in variants:
                lowered = prefix.lower().strip()
                if not lowered:
                    continue
                if text_lower == lowered or text_lower.startswith(lowered + " ") or text_lower.startswith(lowered + "."):
                    if len(lowered) > best_len:
                        best_len = len(lowered)
                        best_order = order_idx

        return best_order if best_order is not None else 10_000

    def _escape_for_embed(self, text: str) -> str:
        escaped = discord.utils.escape_mentions(text)
        escaped = discord.utils.escape_markdown(escaped, as_needed=False)
        escaped = escaped.replace("[", "\\[").replace("]", "\\]")
        escaped = escaped.replace("(", "\\(").replace(")", "\\)")
        return escaped

    def _format_member_line(self, member: discord.Member, t17_id: str | None) -> str:
        nickname = self.lookup.cut_at_hash(member.display_name)
        username = self.lookup.normalize_discord_username(member.name)

        nickname = self._escape_for_embed(nickname)
        username = self._escape_for_embed(username)

        if t17_id:
            if INCLUDE_HLLRECORDS_LINK:
                player_id = urllib.parse.quote(t17_id, safe="")
                url = f"https://www.hllrecords.com/profiles/{player_id}"
                return f"[{nickname}]({url}) ({username}) [{t17_id}]"
            return f"{nickname} ({username}) [{t17_id}]"
        return f"{nickname} ({username})"

    def _chunk_embed_descriptions(self, text: str, max_len: int = 3900) -> list[str]:
        if len(text) <= max_len:
            return [text]

        parts: list[str] = []
        current = ""
        for line in text.split("\n"):
            proposed = f"{current}\n{line}" if current else line
            if len(proposed) > max_len:
                if current:
                    parts.append(current)
                current = line
                continue
            current = proposed

        if current:
            parts.append(current)
        return parts

    def _build_roster_embeds(self, guild: discord.Guild, roster_title: str, entries: list[str]) -> list[discord.Embed]:
        header = f"**{roster_title} ({len(entries)})**\n\n"
        body = "\n\n".join(entries) if entries else "None"
        pages = self._chunk_embed_descriptions(header + body)

        embeds: list[discord.Embed] = []
        total = len(pages)
        now = datetime.now(timezone.utc)
        for index, page in enumerate(pages, start=1):
            title = roster_title if total == 1 else f"{roster_title} ({index}/{total})"
            embed = discord.Embed(title=title, description=page, color=discord.Color.blurple(), timestamp=now)
            embed.set_footer(text=f"Updated • {guild.name}")
            embeds.append(embed)
        return embeds

    async def _build_roster_entries(
        self, guild: discord.Guild, roster: dict[str, int | str], *, force_resolve: bool = False
    ) -> list[str]:
        role = guild.get_role(int(roster["role_id"]))
        if role is None:
            return [f"Role {roster['role_id']} not found."]

        members = sorted(role.members, key=lambda member: member.display_name.lower())
        resolved_members = self.lookup.resolved_members_for_role(guild.id, str(roster["key"]))
        t17_by_user_id = {
            entry["user_id"]: entry.get("t17_id")
            for entry in resolved_members
            if isinstance(entry, dict) and isinstance(entry.get("user_id"), int)
        }

        missing_ids = [member.id for member in members if member.id not in t17_by_user_id]
        if force_resolve or missing_ids:
            targets, _mapping, _unresolved = await self.lookup.resolve_members_for_role(
                members,
                role_name=str(roster["key"]),
                include_username=True,
            )
            t17_by_user_id = {item["member_id"]: item["t17_id"] for item in targets}

        ranked_entries: list[tuple[int, str]] = []
        for member in members:
            line = self._format_member_line(member, t17_by_user_id.get(member.id))
            rank_idx = self._rank_index_from_display_name(member.display_name)
            ranked_entries.append((rank_idx, line))

        ranked_entries.sort(key=lambda item: (item[0], item[1].lower()))
        return [line for _, line in ranked_entries]

    async def _sync_roster_messages(self, guild: discord.Guild, roster: dict[str, int | str], embeds: list[discord.Embed]) -> None:
        channel = await self._resolve_output_channel(guild)
        if channel is None:
            return

        existing_messages: list[discord.Message] = []
        for message_id in self._get_roster_message_ids(guild.id, str(roster["key"])):
            try:
                existing_messages.append(await channel.fetch_message(message_id))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                continue

        saved_ids: list[int] = []
        for index, embed in enumerate(embeds):
            if index < len(existing_messages):
                message = existing_messages[index]
                await message.edit(embed=embed, content=None, allowed_mentions=discord.AllowedMentions.none())
            else:
                message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            saved_ids.append(message.id)

        for message in existing_messages[len(embeds):]:
            try:
                await message.delete()
            except (discord.Forbidden, discord.HTTPException):
                continue

        self._set_roster_message_ids(guild.id, str(roster["key"]), saved_ids)

    async def update_all_rosters(self, *, force_resolve: bool = False) -> None:
        async with self._update_lock:
            guild = self.bot.get_guild(GUILD_ID)
            if guild is None:
                return

            for roster in ROSTER_DEFINITIONS:
                entries = await self._build_roster_entries(guild, roster, force_resolve=force_resolve)
                embeds = self._build_roster_embeds(guild, str(roster["title"]), entries)
                await self._sync_roster_messages(guild, roster, embeds)

    async def refresh_member_override(self, member: discord.Member) -> None:
        if member.guild.id != GUILD_ID:
            return

        tracked_rosters = [
            roster for roster in ROSTER_DEFINITIONS if any(role.id == int(roster["role_id"]) for role in member.roles)
        ]
        for roster in tracked_rosters:
            await self.lookup.resolve_member_for_role(
                member,
                role_name=str(roster["key"]),
                include_username=True,
            )

        await self.update_all_rosters(force_resolve=False)

    def _schedule_update(self) -> None:
        if self._update_task and not self._update_task.done():
            self._update_task.cancel()

        async def runner() -> None:
            try:
                await asyncio.sleep(UPDATE_DEBOUNCE_SECONDS)
                await self.update_all_rosters(force_resolve=False)
            except asyncio.CancelledError:
                return

        self._update_task = asyncio.create_task(runner())

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._ran_once:
            return
        self._ran_once = True
        await self.update_all_rosters(force_resolve=True)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        tracked_role_ids = {int(roster["role_id"]) for roster in ROSTER_DEFINITIONS}
        before_has = any(role.id in tracked_role_ids for role in before.roles)
        after_has = any(role.id in tracked_role_ids for role in after.roles)
        name_changed = (
            before.display_name != after.display_name
            or before.name != after.name
            or getattr(before, "global_name", None) != getattr(after, "global_name", None)
        )

        if before_has != after_has or (after_has and name_changed):
            self._schedule_update()

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        tracked_role_ids = {int(roster["role_id"]) for roster in ROSTER_DEFINITIONS}
        if any(role.id in tracked_role_ids for role in member.roles):
            self._schedule_update()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Rosterizer(bot))