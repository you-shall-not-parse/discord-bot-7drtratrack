from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands

from clan_t17_lookup import ClanT17Lookup
from data_paths import data_path
from hll_API_backend import HLLBackendError, get_hll_backend_client
from state_io import atomic_json_dump


LOGGER = logging.getLogger("EventMapRequests")

REQUEST_CHANNEL_ID = 1530939155067174933
APPROVAL_CHANNEL_ID = 1279831955935854712
MAP_APPROVER_ROLE_ID = 1279832920479109160
EVENTS_BACKEND_NAME = "events"
ADMIN_CAM_SERVER_OPTIONS = {
    "main": "Events",
    "server_2": "Public",
}
T17_ROLE_NAME = "131st Infantry Brigade"
MAP_CACHE_MAX_AGE = timedelta(hours=4)
SELECT_PAGE_SIZE = 25
PANEL_HISTORY_LIMIT = 5

PANEL_STATE_PATH = Path(data_path("event_map_request_panel.json"))
REQUEST_STATE_PATH = Path(data_path("event_map_requests.json"))
MAP_CACHE_PATH = Path(data_path("event_map_catalogue.json"))


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        LOGGER.warning("Could not load %s", path, exc_info=True)
        return {}
    return data if isinstance(data, dict) else {}


def _clean_map(raw_map: dict[str, Any]) -> dict[str, str] | None:
    rcon_name = str(raw_map.get("mapRconName") or "").strip()
    friendly_name = str(raw_map.get("mapFriendlyName") or "").strip()
    if not rcon_name or not friendly_name:
        return None
    return {
        "friendly_name": friendly_name,
        "rcon_name": rcon_name,
        "game_mode": str(raw_map.get("gameMode") or "").strip(),
        "time_of_day": str(raw_map.get("timeOfDay") or "").strip(),
        "faction_1": str(raw_map.get("faction1") or "").strip(),
        "faction_2": str(raw_map.get("faction2") or "").strip(),
        "image_url": str(raw_map.get("mapImageUrl") or "").strip(),
    }


def _variant_label(map_data: dict[str, str]) -> str:
    parts = [
        value
        for value in (map_data.get("game_mode"), map_data.get("time_of_day"))
        if value
    ]
    return " • ".join(parts) or map_data["rcon_name"]


class EventMapPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Request a Map",
        emoji="🗺️",
        style=discord.ButtonStyle.primary,
        custom_id="event_map:open_request",
    )
    async def request_map(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        cog = interaction.client.get_cog("EventMapRequests")
        if not isinstance(cog, EventMapRequests):
            await interaction.response.send_message(
                "The event map request tool is unavailable.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await cog.open_map_picker(interaction)

    @discord.ui.button(
        label="Request Admin Cam Access",
        style=discord.ButtonStyle.primary,
        custom_id="event_map:request_admin_cam",
    )
    async def request_admin_cam(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_message(
            "Choose which server you need admin cam access on:",
            view=AdminCamServerView(),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Show My T17 ID",
        style=discord.ButtonStyle.secondary,
        custom_id="event_map:show_t17_id",
    )
    async def show_t17_id(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        cog = interaction.client.get_cog("EventMapRequests")
        if not isinstance(cog, EventMapRequests):
            await interaction.response.send_message(
                "The event map request tool is unavailable.",
                ephemeral=True,
            )
            return
        await cog.show_t17_id(interaction)


class AdminCamServerSelect(discord.ui.Select):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Choose Events or Public…",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=label, value=server_name)
                for server_name, label in ADMIN_CAM_SERVER_OPTIONS.items()
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        server_name = self.values[0]
        server_label = ADMIN_CAM_SERVER_OPTIONS[server_name]
        await interaction.response.send_modal(
            AdminCamRequestModal(
                server_name=server_name,
                server_label=server_label,
            )
        )


class AdminCamServerView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=180)
        self.add_item(AdminCamServerSelect())


class AdminCamRequestModal(discord.ui.Modal, title="Request Admin Cam Access"):
    duration_hours = discord.ui.TextInput(
        label="Duration in hours (1–168)",
        placeholder="24",
        default="24",
        min_length=1,
        max_length=3,
        required=True,
    )

    def __init__(self, *, server_name: str, server_label: str) -> None:
        super().__init__()
        self.server_name = server_name
        self.server_label = server_label

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("EventMapRequests")
        if not isinstance(cog, EventMapRequests):
            await interaction.response.send_message(
                "The event map request tool is unavailable.",
                ephemeral=True,
            )
            return
        try:
            duration_hours = int(str(self.duration_hours.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "Duration must be a whole number from 1 to 168 hours.",
                ephemeral=True,
            )
            return
        if not 1 <= duration_hours <= 168:
            await interaction.response.send_message(
                "Duration must be between 1 and 168 hours.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        await cog.create_admin_cam_request(
            interaction,
            duration_hours=duration_hours,
            server_name=self.server_name,
            server_label=self.server_label,
        )


class BaseMapSelect(discord.ui.Select):
    def __init__(
        self,
        cog: "EventMapRequests",
        maps_by_name: dict[str, list[dict[str, str]]],
        page: int,
    ) -> None:
        self.cog = cog
        self.maps_by_name = maps_by_name
        self.base_names = sorted(maps_by_name, key=str.casefold)
        page_start = page * SELECT_PAGE_SIZE
        page_end = page_start + SELECT_PAGE_SIZE
        options = [
            discord.SelectOption(
                label=name[:100],
                value=str(index),
                description=f"{len(maps_by_name[name])} available variant(s)"[:100],
            )
            for index, name in enumerate(
                self.base_names[page_start:page_end],
                start=page_start,
            )
        ]
        super().__init__(
            placeholder="Choose a map…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        index = int(self.values[0])
        base_name = self.base_names[index]
        variants = self.maps_by_name[base_name]
        await interaction.response.edit_message(
            content=MapVariantView.prompt(base_name, variants, page=0),
            view=MapVariantView(self.cog, base_name, variants, page=0),
        )


class BaseMapView(discord.ui.View):
    def __init__(
        self,
        cog: "EventMapRequests",
        maps_by_name: dict[str, list[dict[str, str]]],
        *,
        page: int = 0,
    ) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.maps_by_name = maps_by_name
        self.page_count = max(
            1,
            (len(maps_by_name) + SELECT_PAGE_SIZE - 1) // SELECT_PAGE_SIZE,
        )
        self.page = max(0, min(page, self.page_count - 1))
        self.add_item(BaseMapSelect(cog, maps_by_name, self.page))
        self.previous_page.disabled = self.page == 0
        self.next_page.disabled = self.page >= self.page_count - 1

    @staticmethod
    def prompt(maps_by_name: dict[str, list[dict[str, str]]], page: int) -> str:
        page_count = max(
            1,
            (len(maps_by_name) + SELECT_PAGE_SIZE - 1) // SELECT_PAGE_SIZE,
        )
        return f"Choose the base map you would like to request — page {page + 1}/{page_count}:"

    @discord.ui.button(
        label="Previous",
        emoji="⬅️",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def previous_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        page = max(0, self.page - 1)
        await interaction.response.edit_message(
            content=self.prompt(self.maps_by_name, page),
            view=BaseMapView(self.cog, self.maps_by_name, page=page),
        )

    @discord.ui.button(
        label="Next",
        emoji="➡️",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def next_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        page = min(self.page_count - 1, self.page + 1)
        await interaction.response.edit_message(
            content=self.prompt(self.maps_by_name, page),
            view=BaseMapView(self.cog, self.maps_by_name, page=page),
        )


class MapVariantSelect(discord.ui.Select):
    def __init__(
        self,
        cog: "EventMapRequests",
        variants: list[dict[str, str]],
        page: int,
    ) -> None:
        self.cog = cog
        self.variants = variants
        page_start = page * SELECT_PAGE_SIZE
        page_end = page_start + SELECT_PAGE_SIZE
        options = []
        for index, map_data in enumerate(
            variants[page_start:page_end],
            start=page_start,
        ):
            label = _variant_label(map_data)
            description = map_data["rcon_name"]
            factions = " vs ".join(
                value
                for value in (map_data.get("faction_1"), map_data.get("faction_2"))
                if value
            )
            if factions:
                description = f"{description} • {factions}"
            options.append(
                discord.SelectOption(
                    label=label[:100],
                    value=str(index),
                    description=description[:100],
                )
            )
        super().__init__(
            placeholder="Choose the map mode and time…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.cog.create_request(interaction, self.variants[int(self.values[0])])


class MapVariantView(discord.ui.View):
    def __init__(
        self,
        cog: "EventMapRequests",
        base_name: str,
        variants: list[dict[str, str]],
        *,
        page: int = 0,
    ) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.base_name = base_name
        self.variants = variants
        self.page_count = max(
            1,
            (len(variants) + SELECT_PAGE_SIZE - 1) // SELECT_PAGE_SIZE,
        )
        self.page = max(0, min(page, self.page_count - 1))
        self.add_item(MapVariantSelect(cog, variants, self.page))
        self.previous_page.disabled = self.page == 0
        self.next_page.disabled = self.page >= self.page_count - 1

    @staticmethod
    def prompt(base_name: str, variants: list[dict[str, str]], page: int) -> str:
        page_count = max(
            1,
            (len(variants) + SELECT_PAGE_SIZE - 1) // SELECT_PAGE_SIZE,
        )
        escaped_name = discord.utils.escape_markdown(base_name)
        return (
            f"Choose the exact **{escaped_name}** variant "
            f"— page {page + 1}/{page_count}:"
        )

    @discord.ui.button(
        label="Previous",
        emoji="⬅️",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def previous_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        page = max(0, self.page - 1)
        await interaction.response.edit_message(
            content=self.prompt(self.base_name, self.variants, page),
            view=MapVariantView(
                self.cog,
                self.base_name,
                self.variants,
                page=page,
            ),
        )

    @discord.ui.button(
        label="Next",
        emoji="➡️",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def next_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        page = min(self.page_count - 1, self.page + 1)
        await interaction.response.edit_message(
            content=self.prompt(self.base_name, self.variants, page),
            view=MapVariantView(
                self.cog,
                self.base_name,
                self.variants,
                page=page,
            ),
        )


class RequestApprovalView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Approve Request",
        emoji="✅",
        style=discord.ButtonStyle.success,
        custom_id="event_map:approve",
    )
    async def approve(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        cog = interaction.client.get_cog("EventMapRequests")
        if not isinstance(cog, EventMapRequests):
            await interaction.response.send_message("The request tool is unavailable.", ephemeral=True)
            return
        await cog.resolve_request(interaction, approved=True)

    @discord.ui.button(
        label="Deny",
        emoji="❌",
        style=discord.ButtonStyle.danger,
        custom_id="event_map:deny",
    )
    async def deny(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        cog = interaction.client.get_cog("EventMapRequests")
        if not isinstance(cog, EventMapRequests):
            await interaction.response.send_message("The request tool is unavailable.", ephemeral=True)
            return
        await cog.resolve_request(interaction, approved=False)


class EventMapRequests(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._panel_state = _read_json(PANEL_STATE_PATH)
        self._requests = _read_json(REQUEST_STATE_PATH)
        recovered_request = False
        for request in self._requests.values():
            if isinstance(request, dict) and request.get("status") == "processing":
                request["status"] = "pending"
                recovered_request = True
        if recovered_request:
            atomic_json_dump(
                REQUEST_STATE_PATH,
                self._requests,
                indent=2,
                ensure_ascii=False,
            )
        self._request_lock = asyncio.Lock()
        self._map_lock = asyncio.Lock()
        self._t17_lookup = ClanT17Lookup(logger=LOGGER)
        self._panel_view = EventMapPanelView()
        self._approval_view = RequestApprovalView()
        self.bot.add_view(self._panel_view)
        self.bot.add_view(self._approval_view)
        self._panel_task = self.bot.loop.create_task(self._ensure_panel())

    def cog_unload(self) -> None:
        self._panel_task.cancel()

    @staticmethod
    def _resolved_timestamp(request: dict[str, Any]) -> datetime:
        raw_timestamp = str(request.get("resolved_at") or request.get("created_at") or "")
        try:
            timestamp = datetime.fromisoformat(raw_timestamp)
        except ValueError:
            return datetime(1970, 1, 1, tzinfo=timezone.utc)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(timezone.utc)

    def _recent_decision_lines(self) -> list[str]:
        resolved_requests = [
            request
            for request in self._requests.values()
            if isinstance(request, dict)
            and str(request.get("status")) in {"approved", "denied"}
        ]
        resolved_requests.sort(key=self._resolved_timestamp, reverse=True)

        lines: list[str] = []
        for request in resolved_requests[:PANEL_HISTORY_LIMIT]:
            requester_id = int(request.get("requester_id") or 0)
            resolver_id = int(request.get("resolved_by") or 0)
            timestamp = int(self._resolved_timestamp(request).timestamp())
            status = str(request.get("status"))
            if str(request.get("request_type") or "map") == "admin_cam":
                duration = int(request.get("duration_hours") or 0)
                server_label = discord.utils.escape_markdown(
                    str(request.get("server_label") or "Events")
                )
                if status == "approved":
                    expires_at = int(float(request.get("expires_at") or 0))
                    expiry = f" Access expires <t:{expires_at}:R>." if expires_at else ""
                    decision = (
                        f"✅ <@{requester_id}> your **{server_label}** admin cam request for "
                        f"**{duration} hour(s)** was approved.{expiry}"
                    )
                else:
                    decision = (
                        f"❌ <@{requester_id}> your **{server_label}** admin cam request for "
                        f"**{duration} hour(s)** was denied."
                    )
            else:
                friendly_name = discord.utils.escape_markdown(
                    str(request.get("friendly_name") or "Unknown map")
                )
                variant = discord.utils.escape_markdown(_variant_label(request))
                if status == "approved":
                    decision = (
                        f"✅ <@{requester_id}> your events server map request was approved. "
                        f"Changing now to **{friendly_name} — {variant}**."
                    )
                else:
                    decision = (
                        f"❌ <@{requester_id}> your events server map request for "
                        f"**{friendly_name} — {variant}** was denied."
                    )
            lines.append(f"{decision} By <@{resolver_id}> • <t:{timestamp}:R>")
        return lines

    def build_panel_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Request an Events Server Map",
            colour=discord.Colour.blue(),
            description=(
                "Request a map for the 7DR events server. Choose the map, mode, and time-of-day "
                "variant, or request temporary admin cam access, using the buttons below.\n\n"
                "Your request is sent to staff for approval. If approved, the events server changes "
                "to that map immediately or grants temporary Spectator access."
            ),
        )
        decision_lines = self._recent_decision_lines()
        embed.add_field(
            name="Last 5 approvals/rejections",
            value=(
                "\n\n".join(decision_lines)[:1024]
                if decision_lines
                else "No requests have been resolved yet."
            ),
            inline=False,
        )
        embed.set_footer(
            text="One pending map request and one admin cam request per server, per member."
        )
        return embed

    async def show_t17_id(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This button can only be used in a server.",
                ephemeral=True,
            )
            return

        resolved = self._t17_lookup.get_resolved_member(
            guild.id,
            interaction.user.id,
            role_name=T17_ROLE_NAME,
        )
        if not isinstance(resolved, dict):
            await interaction.response.send_message(
                "I do not have a stored hellor record for you right now. If this looks wrong, contact an admin.",
                ephemeral=True,
            )
            return

        lines: list[str] = []
        display = resolved.get("display_name")
        username = resolved.get("username")
        global_name = resolved.get("global_name")
        t17_id = str(resolved.get("t17_id") or "").strip()
        if display:
            lines.append(f"Display name: {display}")
        if username:
            lines.append(f"Username: {username}")
        if global_name:
            lines.append(f"Global name: {global_name}")
        if t17_id:
            lines.extend(
                [
                    f"T17 ID: {t17_id}",
                    f"Profile: https://hellor.pro/player/{t17_id}",
                    f"HLL Records: https://hllrecords.com/profiles/{t17_id}",
                ]
            )
        else:
            lines.append("T17 ID: none")
        lines.extend(["", "Is this wrong? Contact an admin."])
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @staticmethod
    def _can_approve(member: discord.abc.User) -> bool:
        return isinstance(member, discord.Member) and (
            member.guild_permissions.administrator
            or member.guild_permissions.manage_guild
            or any(role.id == MAP_APPROVER_ROLE_ID for role in member.roles)
        )

    @staticmethod
    def _request_embed(request: dict[str, Any]) -> discord.Embed:
        status = str(request.get("status") or "pending")
        request_type = str(request.get("request_type") or "map")
        colours = {
            "pending": discord.Colour.orange(),
            "processing": discord.Colour.gold(),
            "approved": discord.Colour.green(),
            "denied": discord.Colour.red(),
        }
        if request_type == "admin_cam":
            titles = {
                "pending": "🎥 Admin Cam Access Request",
                "processing": "⏳ Admin Cam Access Request",
                "approved": "✅ Admin Cam Access Granted",
                "denied": "❌ Admin Cam Access Request Denied",
            }
        else:
            titles = {
                "pending": "🗺️ Events Map Change Request",
                "processing": "⏳ Events Map Change Request",
                "approved": "✅ Events Map Changed",
                "denied": "❌ Events Map Request Denied",
            }
        embed = discord.Embed(
            title=titles.get(status, titles["pending"]),
            colour=colours.get(status, colours["pending"]),
            timestamp=datetime.fromisoformat(str(request["created_at"])),
        )
        embed.add_field(name="Requested by", value=f"<@{int(request['requester_id'])}>", inline=True)
        if request_type == "admin_cam":
            embed.add_field(
                name="Duration",
                value=f"{int(request.get('duration_hours') or 0)} hour(s)",
                inline=True,
            )
            embed.add_field(
                name="Server",
                value=str(request.get("server_label") or "Events"),
                inline=True,
            )
            embed.add_field(
                name="T17 ID",
                value=f"`{str(request.get('player_id') or 'unresolved')}`",
                inline=False,
            )
            expires_at = int(float(request.get("expires_at") or 0))
            if status == "approved" and expires_at:
                embed.add_field(
                    name="Access expires",
                    value=f"<t:{expires_at}:F> (<t:{expires_at}:R>)",
                    inline=False,
                )
        else:
            embed.add_field(name="Map", value=str(request["friendly_name"]), inline=True)
            embed.add_field(name="Variant", value=_variant_label(request), inline=True)
            embed.add_field(name="RCON name", value=f"`{request['rcon_name']}`", inline=False)
        if status in {"approved", "denied"}:
            embed.add_field(
                name="Resolved by",
                value=f"<@{int(request['resolved_by'])}>",
                inline=True,
            )
        backend_message = str(request.get("backend_message") or "").strip()
        if backend_message:
            embed.add_field(name="Bifrost", value=backend_message[:1024], inline=False)
        backend_error = str(request.get("backend_error") or "").strip()
        if backend_error:
            embed.add_field(
                name="Last Bifrost error",
                value=f"`{backend_error[:1000]}`",
                inline=False,
            )
        image_url = str(request.get("image_url") or "").strip()
        if image_url.startswith("https://"):
            embed.set_thumbnail(url=image_url)
        return embed

    def _backend(self):
        return get_hll_backend_client(EVENTS_BACKEND_NAME)

    async def _get_channel(
        self,
        channel_id: int,
    ) -> discord.TextChannel | discord.Thread | None:
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return None
        return channel if isinstance(channel, (discord.TextChannel, discord.Thread)) else None

    async def _ensure_panel(self) -> None:
        await self.bot.wait_until_ready()
        channel = await self._get_channel(REQUEST_CHANNEL_ID)
        if channel is None:
            LOGGER.error("Could not access event map request channel %s", REQUEST_CHANNEL_ID)
            return

        message: discord.Message | None = None
        message_id = self._panel_state.get("message_id")
        if isinstance(message_id, int):
            try:
                message = await channel.fetch_message(message_id)
            except discord.NotFound:
                message = None
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.exception("Could not fetch the event map request panel")
                return
        try:
            if message is None:
                message = await channel.send(
                    embed=self.build_panel_embed(),
                    view=self._panel_view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                await message.edit(
                    embed=self.build_panel_embed(),
                    view=self._panel_view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.exception("Could not publish the event map request panel")
            return

        self._panel_state = {"channel_id": channel.id, "message_id": message.id}
        atomic_json_dump(PANEL_STATE_PATH, self._panel_state, indent=2)

    async def _refresh_panel_embed(self) -> None:
        channel_id = int(self._panel_state.get("channel_id") or REQUEST_CHANNEL_ID)
        message_id = self._panel_state.get("message_id")
        if not isinstance(message_id, int):
            LOGGER.warning("Could not refresh event map panel because its message ID is missing")
            return
        channel = await self._get_channel(channel_id)
        if channel is None:
            LOGGER.warning("Could not refresh event map panel because channel %s is unavailable", channel_id)
            return
        try:
            message = await channel.fetch_message(message_id)
            await message.edit(
                embed=self.build_panel_embed(),
                view=self._panel_view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            LOGGER.exception("Could not refresh event map request panel %s", message_id)

    async def _map_catalogue(self) -> list[dict[str, str]]:
        async with self._map_lock:
            cached = _read_json(MAP_CACHE_PATH)
            cached_maps = cached.get("maps")
            fetched_at_raw = str(cached.get("fetched_at") or "")
            fresh = False
            try:
                fetched_at = datetime.fromisoformat(fetched_at_raw)
                if fetched_at.tzinfo is None:
                    fetched_at = fetched_at.replace(tzinfo=timezone.utc)
                fresh = datetime.now(timezone.utc) - fetched_at < MAP_CACHE_MAX_AGE
            except ValueError:
                pass

            if fresh and isinstance(cached_maps, list):
                return [item for item in cached_maps if isinstance(item, dict)]

            try:
                raw_maps = await self._backend().get_available_maps()
            except HLLBackendError:
                if isinstance(cached_maps, list) and cached_maps:
                    LOGGER.warning("Using stale Bifrost map catalogue", exc_info=True)
                    return [item for item in cached_maps if isinstance(item, dict)]
                raise

            maps = []
            for raw_map in raw_maps:
                cleaned = _clean_map(raw_map)
                if cleaned is not None:
                    maps.append(cleaned)
            maps.sort(
                key=lambda item: (
                    item["friendly_name"].casefold(),
                    item["game_mode"].casefold(),
                    item["time_of_day"].casefold(),
                    item["rcon_name"].casefold(),
                )
            )
            if not maps:
                raise HLLBackendError("Bifrost returned no valid HLL maps")
            atomic_json_dump(
                MAP_CACHE_PATH,
                {
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "maps": maps,
                },
                indent=2,
                ensure_ascii=False,
            )
            return maps

    async def open_map_picker(self, interaction: discord.Interaction) -> None:
        try:
            maps = await self._map_catalogue()
        except (HLLBackendError, KeyError) as exc:
            LOGGER.warning("Could not open event map catalogue: %s", exc)
            await interaction.followup.send(
                "The events server map catalogue is unavailable. Please contact staff.",
                ephemeral=True,
            )
            return

        maps_by_name: dict[str, list[dict[str, str]]] = {}
        for map_data in maps:
            maps_by_name.setdefault(map_data["friendly_name"], []).append(map_data)
        await interaction.followup.send(
            BaseMapView.prompt(maps_by_name, page=0),
            view=BaseMapView(self, maps_by_name, page=0),
            ephemeral=True,
        )

    async def create_request(
        self,
        interaction: discord.Interaction,
        map_data: dict[str, str],
    ) -> None:
        approval_channel = await self._get_channel(APPROVAL_CHANNEL_ID)
        if approval_channel is None:
            await interaction.followup.send(
                "The staff approval channel is unavailable.",
                ephemeral=True,
            )
            return

        async with self._request_lock:
            if any(
                str(request.get("status")) in {"pending", "processing"}
                and int(request.get("requester_id") or 0) == interaction.user.id
                and str(request.get("request_type") or "map") == "map"
                for request in self._requests.values()
                if isinstance(request, dict)
            ):
                await interaction.followup.send(
                    "You already have a pending map request.",
                    ephemeral=True,
                )
                return

            request: dict[str, Any] = {
                **map_data,
                "request_type": "map",
                "requester_id": interaction.user.id,
                "request_channel_id": interaction.channel_id,
                "status": "pending",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                approval_message = await approval_channel.send(
                    embed=self._request_embed(request),
                    view=self._approval_view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.exception("Could not create event map approval request")
                await interaction.followup.send(
                    "Discord could not send your request to staff.",
                    ephemeral=True,
                )
                return

            request["approval_channel_id"] = approval_channel.id
            request["approval_message_id"] = approval_message.id
            self._requests[str(approval_message.id)] = request
            atomic_json_dump(REQUEST_STATE_PATH, self._requests, indent=2, ensure_ascii=False)

        await interaction.followup.send(
            f"Your request for **{map_data['friendly_name']} — {_variant_label(map_data)}** "
            "has been sent to staff.",
            ephemeral=True,
        )

    async def create_admin_cam_request(
        self,
        interaction: discord.Interaction,
        *,
        duration_hours: int,
        server_name: str,
        server_label: str,
    ) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.followup.send(
                "Admin cam access can only be requested from inside the server.",
                ephemeral=True,
            )
            return
        if ADMIN_CAM_SERVER_OPTIONS.get(server_name) != server_label:
            await interaction.followup.send(
                "That admin cam server selection is invalid. Please start the request again.",
                ephemeral=True,
            )
            return

        approval_channel = await self._get_channel(APPROVAL_CHANNEL_ID)
        if approval_channel is None:
            await interaction.followup.send(
                "The staff approval channel is unavailable.",
                ephemeral=True,
            )
            return

        try:
            t17_id, source, queries = await self._t17_lookup.resolve_member_for_role(
                member,
                role_name="t17serveradmin",
            )
        except Exception as exc:
            LOGGER.exception(
                "Could not resolve T17 ID for admin cam request member_id=%s: %s",
                member.id,
                exc,
            )
            await interaction.followup.send(
                "The T17 lookup is temporarily unavailable. Please try again later.",
                ephemeral=True,
            )
            return
        if not t17_id:
            await interaction.followup.send(
                "I could not resolve your T17 ID. Use **Show My T17 ID** to check your stored record, "
                "or contact an admin to correct it.",
                ephemeral=True,
            )
            return

        description = (
            queries[0]
            if queries
            else self._t17_lookup.normalize_discord_username(
                member.display_name,
                strip_rank_prefix=True,
            )
            or member.display_name
        )

        async with self._request_lock:
            if any(
                str(request.get("status")) in {"pending", "processing"}
                and int(request.get("requester_id") or 0) == member.id
                and str(request.get("request_type") or "map") == "admin_cam"
                and str(request.get("server_name") or "main") == server_name
                for request in self._requests.values()
                if isinstance(request, dict)
            ):
                await interaction.followup.send(
                    f"You already have a pending admin cam request for **{server_label}**.",
                    ephemeral=True,
                )
                return

            request: dict[str, Any] = {
                "request_type": "admin_cam",
                "guild_id": guild.id,
                "requester_id": member.id,
                "requester_display_name": member.display_name,
                "request_channel_id": interaction.channel_id,
                "status": "pending",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "player_id": t17_id,
                "description": description,
                "source": source,
                "queries": queries,
                "duration_hours": int(duration_hours),
                "server_name": server_name,
                "server_label": server_label,
            }
            try:
                approval_message = await approval_channel.send(
                    embed=self._request_embed(request),
                    view=self._approval_view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.exception("Could not create admin cam approval request")
                await interaction.followup.send(
                    "Discord could not send your admin cam request to staff.",
                    ephemeral=True,
                )
                return

            request["approval_channel_id"] = approval_channel.id
            request["approval_message_id"] = approval_message.id
            self._requests[str(approval_message.id)] = request
            atomic_json_dump(REQUEST_STATE_PATH, self._requests, indent=2, ensure_ascii=False)

        await interaction.followup.send(
            f"Your **{server_label}** admin cam request for **{duration_hours} hour(s)** "
            "has been sent to staff.",
            ephemeral=True,
        )

    async def _grant_admin_cam_request(
        self,
        request: dict[str, Any],
        approver: discord.abc.User,
    ) -> dict[str, Any]:
        admin_cog = self.bot.get_cog("[API] T17ServerAdmin")
        grant_method = getattr(admin_cog, "grant_temporary_admin_cam", None)
        if not callable(grant_method):
            raise HLLBackendError("The temporary admin cam service is unavailable")

        grant = await grant_method(
            guild_id=int(request["guild_id"]),
            user_id=int(request["requester_id"]),
            member_display_name=str(request.get("requester_display_name") or request["requester_id"]),
            player_id=str(request["player_id"]),
            description=str(request.get("description") or request["player_id"]),
            server_name=str(request.get("server_name") or "main"),
            server_label=str(request.get("server_label") or "Events"),
            source=str(request.get("source") or "stored_mapping"),
            queries=[
                str(query)
                for query in request.get("queries", [])
                if str(query).strip()
            ],
            duration_hours=int(request["duration_hours"]),
            granted_by_id=int(approver.id),
            granted_by_name=str(getattr(approver, "display_name", approver)),
        )
        if not isinstance(grant, dict):
            raise HLLBackendError("The temporary admin cam service returned an invalid result")
        return grant

    async def resolve_request(
        self,
        interaction: discord.Interaction,
        *,
        approved: bool,
    ) -> None:
        if not self._can_approve(interaction.user):
            await interaction.response.send_message(
                "You do not have permission to resolve requests.",
                ephemeral=True,
            )
            return
        if interaction.message is None:
            await interaction.response.send_message(
                "I could not identify this request.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=approved)
        message_key = str(interaction.message.id)
        async with self._request_lock:
            request = self._requests.get(message_key)
            if not isinstance(request, dict):
                await interaction.followup.send("This request is no longer active.", ephemeral=True)
                return
            if str(request.get("status")) != "pending":
                await interaction.followup.send(
                    f"This request is already **{request.get('status')}**.",
                    ephemeral=True,
                )
                return
            request["status"] = "processing" if approved else "denied"
            request.pop("backend_error", None)
            request.pop("backend_error_at", None)
            if not approved:
                request["resolved_by"] = interaction.user.id
                request["resolved_at"] = datetime.now(timezone.utc).isoformat()
            atomic_json_dump(REQUEST_STATE_PATH, self._requests, indent=2, ensure_ascii=False)

        if approved:
            request_type = str(request.get("request_type") or "map")
            try:
                if request_type == "admin_cam":
                    result = await self._grant_admin_cam_request(request, interaction.user)
                else:
                    result = await self._backend().change_map(str(request["rcon_name"]))
            except Exception as exc:
                error_message = str(exc) or type(exc).__name__
                async with self._request_lock:
                    current = self._requests.get(message_key)
                    if isinstance(current, dict) and current.get("status") == "processing":
                        current["status"] = "pending"
                        current["backend_error"] = error_message
                        current["backend_error_at"] = datetime.now(timezone.utc).isoformat()
                        atomic_json_dump(
                            REQUEST_STATE_PATH,
                            self._requests,
                            indent=2,
                            ensure_ascii=False,
                        )
                LOGGER.exception(
                    "Events request approval failed request_id=%s request_type=%s target=%s: %s",
                    message_key,
                    request_type,
                    request.get("rcon_name") or request.get("player_id"),
                    error_message,
                )
                try:
                    await interaction.message.edit(
                        embed=self._request_embed(request),
                        view=self._approval_view,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    LOGGER.exception(
                        "Could not display backend failure on event request %s",
                        message_key,
                    )
                failure_label = (
                    "Bifrost did not grant admin cam access"
                    if request_type == "admin_cam"
                    else "Bifrost did not change the map"
                )
                await interaction.followup.send(
                    f"{failure_label}: `{error_message[:1500]}`",
                    ephemeral=True,
                )
                return

            async with self._request_lock:
                request["status"] = "approved"
                request["resolved_by"] = interaction.user.id
                request["resolved_at"] = datetime.now(timezone.utc).isoformat()
                if request_type == "admin_cam":
                    request["expires_at"] = float(result["expires_at"])
                    request["backend_message"] = "Temporary Spectator admin cam access granted."
                else:
                    request["backend_message"] = str(result.get("message") or "Map change initiated.")
                atomic_json_dump(
                    REQUEST_STATE_PATH,
                    self._requests,
                    indent=2,
                    ensure_ascii=False,
                )

        try:
            await interaction.message.edit(
                embed=self._request_embed(request),
                view=None,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            LOGGER.exception("Could not update resolved event request %s", message_key)

        await self._refresh_panel_embed()
        if not approved:
            action = "denied"
        elif str(request.get("request_type") or "map") == "admin_cam":
            action = "approved and temporary admin cam access was granted"
        else:
            action = "approved and the map change was initiated"
        await interaction.followup.send(f"Request {action}.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EventMapRequests(bot))
