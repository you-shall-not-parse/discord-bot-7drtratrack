from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands

from data_paths import data_path
from hll_API_backend import HLLBackendError, get_hll_backend_client
from state_io import atomic_json_dump


LOGGER = logging.getLogger("EventMapRequests")

REQUEST_CHANNEL_ID = 1530939155067174933
APPROVAL_CHANNEL_ID = 1279831955935854712
MAP_APPROVER_ROLE_ID = 1279832920479109160
EVENTS_BACKEND_NAME = "events"
MAP_CACHE_MAX_AGE = timedelta(hours=4)

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


class BaseMapSelect(discord.ui.Select):
    def __init__(
        self,
        cog: "EventMapRequests",
        maps_by_name: dict[str, list[dict[str, str]]],
    ) -> None:
        self.cog = cog
        self.maps_by_name = maps_by_name
        self.base_names = sorted(maps_by_name, key=str.casefold)
        options = [
            discord.SelectOption(
                label=name[:100],
                value=str(index),
                description=f"{len(maps_by_name[name])} available variant(s)"[:100],
            )
            for index, name in enumerate(self.base_names[:25])
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
            content=f"Choose the exact **{discord.utils.escape_markdown(base_name)}** variant:",
            view=MapVariantView(self.cog, variants),
        )


class BaseMapView(discord.ui.View):
    def __init__(
        self,
        cog: "EventMapRequests",
        maps_by_name: dict[str, list[dict[str, str]]],
    ) -> None:
        super().__init__(timeout=180)
        self.add_item(BaseMapSelect(cog, maps_by_name))


class MapVariantSelect(discord.ui.Select):
    def __init__(
        self,
        cog: "EventMapRequests",
        variants: list[dict[str, str]],
    ) -> None:
        self.cog = cog
        self.variants = variants
        options = []
        for index, map_data in enumerate(variants[:25]):
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
        variants: list[dict[str, str]],
    ) -> None:
        super().__init__(timeout=180)
        self.add_item(MapVariantSelect(cog, variants))


class MapApprovalView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Approve & Change Map",
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
        self._panel_view = EventMapPanelView()
        self._approval_view = MapApprovalView()
        self.bot.add_view(self._panel_view)
        self.bot.add_view(self._approval_view)
        self._panel_task = self.bot.loop.create_task(self._ensure_panel())

    def cog_unload(self) -> None:
        self._panel_task.cancel()

    @staticmethod
    def build_panel_embed() -> discord.Embed:
        embed = discord.Embed(
            title="Request an Events Server Map",
            colour=discord.Colour.blue(),
            description=(
                "Request a map for the 7DR events server. Choose the map, mode, and time-of-day "
                "variant using the button below.\n\n"
                "Your request is sent to staff for approval. If approved, the events server changes "
                "to that map immediately."
            ),
        )
        embed.set_footer(text="One pending request per member. Staff approval is required.")
        return embed

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
        colours = {
            "pending": discord.Colour.orange(),
            "processing": discord.Colour.gold(),
            "approved": discord.Colour.green(),
            "denied": discord.Colour.red(),
        }
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
        if len(maps_by_name) > 25:
            await interaction.followup.send(
                "The map catalogue currently exceeds Discord's selector limit. Please contact staff.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            "Choose the base map you would like to request:",
            view=BaseMapView(self, maps_by_name),
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

    async def resolve_request(
        self,
        interaction: discord.Interaction,
        *,
        approved: bool,
    ) -> None:
        if not self._can_approve(interaction.user):
            await interaction.response.send_message(
                "You do not have permission to resolve map requests.",
                ephemeral=True,
            )
            return
        if interaction.message is None:
            await interaction.response.send_message(
                "I could not identify this map request.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=approved)
        message_key = str(interaction.message.id)
        async with self._request_lock:
            request = self._requests.get(message_key)
            if not isinstance(request, dict):
                await interaction.followup.send("This map request is no longer active.", ephemeral=True)
                return
            if str(request.get("status")) != "pending":
                await interaction.followup.send(
                    f"This request is already **{request.get('status')}**.",
                    ephemeral=True,
                )
                return
            request["status"] = "processing" if approved else "denied"
            if not approved:
                request["resolved_by"] = interaction.user.id
                request["resolved_at"] = datetime.now(timezone.utc).isoformat()
            atomic_json_dump(REQUEST_STATE_PATH, self._requests, indent=2, ensure_ascii=False)

        if approved:
            try:
                result = await self._backend().change_map(str(request["rcon_name"]))
            except (HLLBackendError, KeyError) as exc:
                async with self._request_lock:
                    current = self._requests.get(message_key)
                    if isinstance(current, dict) and current.get("status") == "processing":
                        current["status"] = "pending"
                        atomic_json_dump(
                            REQUEST_STATE_PATH,
                            self._requests,
                            indent=2,
                            ensure_ascii=False,
                        )
                LOGGER.warning("Events server map change failed: %s", exc)
                await interaction.followup.send(
                    f"Bifrost did not change the map: `{str(exc)[:1500]}`",
                    ephemeral=True,
                )
                return

            async with self._request_lock:
                request["status"] = "approved"
                request["resolved_by"] = interaction.user.id
                request["resolved_at"] = datetime.now(timezone.utc).isoformat()
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
            LOGGER.exception("Could not update resolved event map request %s", message_key)

        await self._notify_requester(request)
        action = "approved and the map change was initiated" if approved else "denied"
        await interaction.followup.send(f"Request {action}.", ephemeral=True)

    async def _notify_requester(self, request: dict[str, Any]) -> None:
        channel_id = int(request.get("request_channel_id") or REQUEST_CHANNEL_ID)
        channel = await self._get_channel(channel_id)
        if channel is None:
            return
        requester_id = int(request["requester_id"])
        if request.get("status") == "approved":
            text = (
                f"<@{requester_id}> your events server map request was approved. "
                f"Changing now to **{request['friendly_name']} — {_variant_label(request)}**."
            )
        else:
            text = (
                f"<@{requester_id}> your events server map request for "
                f"**{request['friendly_name']} — {_variant_label(request)}** was denied."
            )
        try:
            await channel.send(
                text,
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    roles=False,
                    everyone=False,
                    replied_user=False,
                ),
            )
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.warning("Could not notify requester %s", requester_id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EventMapRequests(bot))
