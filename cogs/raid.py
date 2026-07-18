from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import socket
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import discord
import requests
from discord.ext import commands

from data_paths import data_path
from hll_API_backend import BifrostBackendClient, HLLBackendError


LOGGER = logging.getLogger("Raid")
STATE_PATH = Path(data_path("raid_posts.json"))
PANEL_STATE_PATH = Path(data_path("raid_panel.json"))
RAID_CHANNEL_ID = 1528077898177839244
MAX_VISIBLE_RAIDERS = 40
LIVE_REFRESH_SECONDS = 60
LIVE_REFRESH_MAX_AGE_SECONDS = 8 * 60 * 60
MAX_STATS_RESPONSE_BYTES = 2 * 1024 * 1024
BIFROST_SERVER_PATTERN = re.compile(r"/servers/([A-Za-z0-9-]+)", re.IGNORECASE)


def _safe_text(value: str, *, markdown: bool = False) -> str:
    value = discord.utils.escape_mentions(value.strip())
    return discord.utils.escape_markdown(value) if markdown else value


def _valid_stats_url(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


class RaidModal(discord.ui.Modal, title="Initiate Raid"):
    clan_name = discord.ui.TextInput(
        label="Clan name",
        placeholder="7DR",
        min_length=1,
        max_length=80,
    )
    announcement = discord.ui.TextInput(
        label="Raid message",
        placeholder="7DR is raiding! Join us!",
        style=discord.TextStyle.paragraph,
        min_length=1,
        max_length=500,
    )
    stats_link = discord.ui.TextInput(
        label="CRCON or Bifrost server stats link",
        placeholder="https://...",
        min_length=8,
        max_length=400,
    )

    def __init__(self, cog: "Raid") -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "Raid posts can only be created in a server channel.",
                ephemeral=True,
            )
            return

        stats_url = self.stats_link.value.strip()
        if not _valid_stats_url(stats_url):
            await interaction.response.send_message(
                "Please enter a complete CRCON or Bifrost `http://` or `https://` link.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        await self.cog.create_post(
            interaction,
            clan_name=self.clan_name.value,
            announcement=self.announcement.value,
            stats_url=stats_url,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        LOGGER.exception("Raid modal failed", exc_info=error)
        message = "Something went wrong while creating the raid post."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class InitiateRaidButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Initiate Raid",
            style=discord.ButtonStyle.danger,
            emoji="⚔️",
            custom_id="raid:initiate",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("Raid")
        if not isinstance(cog, Raid):
            await interaction.response.send_message("The raid tool is unavailable.", ephemeral=True)
            return
        await interaction.response.send_modal(RaidModal(cog))


class RaidLauncherView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(InitiateRaidButton())


class RaidSignupView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Join Raid",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="raid:join",
    )
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = interaction.client.get_cog("Raid")
        if not isinstance(cog, Raid):
            await interaction.response.send_message("The raid tool is unavailable.", ephemeral=True)
            return
        await cog.update_signup(interaction, joining=True)

    @discord.ui.button(
        label="Leave",
        style=discord.ButtonStyle.secondary,
        emoji="↩️",
        custom_id="raid:leave",
    )
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = interaction.client.get_cog("Raid")
        if not isinstance(cog, Raid):
            await interaction.response.send_message("The raid tool is unavailable.", ephemeral=True)
            return
        await cog.update_signup(interaction, joining=False)

    @discord.ui.button(
        label="Initiate Raid",
        style=discord.ButtonStyle.danger,
        emoji="⚔️",
        custom_id="raid:initiate_from_post",
    )
    async def initiate(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = interaction.client.get_cog("Raid")
        if not isinstance(cog, Raid):
            await interaction.response.send_message("The raid tool is unavailable.", ephemeral=True)
            return
        await interaction.response.send_modal(RaidModal(cog))


class Raid(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._lock = asyncio.Lock()
        self._bifrost_lock = asyncio.Lock()
        self._bifrost_client: BifrostBackendClient | None = None
        self._posts = self._load_posts()
        bot.add_view(RaidLauncherView())
        bot.add_view(RaidSignupView())
        self._panel_task = bot.loop.create_task(self._ensure_panel())
        self._live_refresh_task = bot.loop.create_task(self._live_refresh_loop())

    def cog_unload(self) -> None:
        self._panel_task.cancel()
        self._live_refresh_task.cancel()

    def _load_posts(self) -> dict[str, dict[str, object]]:
        if not STATE_PATH.exists():
            return {}
        try:
            with STATE_PATH.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            LOGGER.exception("Could not load %s; starting with empty raid state", STATE_PATH)
            return {}

    def _save_posts(self) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = STATE_PATH.with_suffix(".tmp")
        with temporary_path.open("w", encoding="utf-8") as handle:
            json.dump(self._posts, handle, indent=2, ensure_ascii=False)
        temporary_path.replace(STATE_PATH)

    def _load_panel_message_id(self) -> int | None:
        if not PANEL_STATE_PATH.exists():
            return None
        try:
            with PANEL_STATE_PATH.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            return int(data["message_id"])
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            LOGGER.warning("Could not load the saved raid panel message ID")
            return None

    def _save_panel_message_id(self, message_id: int) -> None:
        PANEL_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = PANEL_STATE_PATH.with_suffix(".tmp")
        with temporary_path.open("w", encoding="utf-8") as handle:
            json.dump({"channel_id": RAID_CHANNEL_ID, "message_id": message_id}, handle, indent=2)
        temporary_path.replace(PANEL_STATE_PATH)

    async def _ensure_panel(self) -> None:
        await self.bot.wait_until_ready()
        channel = self.bot.get_channel(RAID_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(RAID_CHANNEL_ID)
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.exception("Could not access raid channel %s", RAID_CHANNEL_ID)
                return

        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            LOGGER.error("Raid channel %s is not a text channel or thread", RAID_CHANNEL_ID)
            return

        message_id = self._load_panel_message_id()
        if message_id is not None:
            try:
                message = await channel.fetch_message(message_id)
                await message.edit(embed=self.build_launcher_embed(), view=RaidLauncherView())
                return
            except discord.NotFound:
                pass
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.exception("Could not refresh raid panel message %s", message_id)
                return

        try:
            message = await channel.send(embed=self.build_launcher_embed(), view=RaidLauncherView())
            self._save_panel_message_id(message.id)
        except (OSError, discord.Forbidden, discord.HTTPException):
            LOGGER.exception("Could not create the raid panel in channel %s", RAID_CHANNEL_ID)

    @staticmethod
    def _first_nested_value(payload: object, keys: tuple[str, ...]) -> object | None:
        if isinstance(payload, dict):
            for key in keys:
                value = payload.get(key)
                if value not in (None, "", [], {}):
                    return value
            for value in payload.values():
                found = Raid._first_nested_value(value, keys)
                if found not in (None, "", [], {}):
                    return found
        return None

    @staticmethod
    def _map_name(value: object) -> str | None:
        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, dict):
            for key in ("pretty_name", "name", "shortname", "id"):
                text = str(value.get(key) or "").strip()
                if text:
                    return text
            nested = value.get("map")
            if nested is not value:
                return Raid._map_name(nested)
        return None

    @staticmethod
    def _integer(value: object) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    async def _url_resolves_publicly(url: str) -> bool:
        hostname = urlparse(url).hostname
        if not hostname:
            return False

        def resolve() -> list[tuple[object, ...]]:
            return socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)

        try:
            addresses = await asyncio.to_thread(resolve)
        except socket.gaierror:
            return False

        for address in addresses:
            try:
                ip = ipaddress.ip_address(address[4][0])
            except (ValueError, IndexError):
                return False
            if not ip.is_global:
                return False
        return bool(addresses)

    async def _fetch_public_json(self, url: str) -> dict[str, object] | None:
        if not await self._url_resolves_publicly(url):
            LOGGER.warning("Refusing non-public or unresolvable raid stats URL: %s", url)
            return None

        def request() -> dict[str, object] | None:
            response = requests.get(
                url,
                headers={"Accept": "application/json", "User-Agent": "7DR-RaidBot/1.0"},
                timeout=12,
                allow_redirects=False,
            )
            if response.status_code != 200:
                return None
            content_length = self._integer(response.headers.get("Content-Length"))
            if content_length is not None and content_length > MAX_STATS_RESPONSE_BYTES:
                return None
            if len(response.content) > MAX_STATS_RESPONSE_BYTES:
                return None
            payload = response.json()
            return payload if isinstance(payload, dict) else None

        try:
            return await asyncio.to_thread(request)
        except (requests.RequestException, json.JSONDecodeError, ValueError):
            LOGGER.info("Could not read public raid stats from %s", url, exc_info=True)
            return None

    def _parse_crcon_live_state(
        self,
        game_payload: dict[str, object],
        scoreboard_payload: dict[str, object] | None,
    ) -> dict[str, object] | None:
        game_data: object = game_payload.get("result", game_payload)
        scoreboard_data: object = (
            scoreboard_payload.get("result", scoreboard_payload) if scoreboard_payload else None
        )

        map_value = self._first_nested_value(
            game_data,
            ("current_map", "currentMap", "map_name", "mapName", "map"),
        )
        map_name = self._map_name(map_value)

        players = self._integer(
            self._first_nested_value(
                game_data,
                ("player_count", "playerCount", "num_players", "current_players"),
            )
        )
        allied = self._integer(
            self._first_nested_value(game_data, ("num_allied_players", "allied_players"))
        )
        axis = self._integer(
            self._first_nested_value(game_data, ("num_axis_players", "axis_players"))
        )
        if players is None and allied is not None and axis is not None:
            players = allied + axis

        if players is None and scoreboard_data is not None:
            players = self._integer(
                self._first_nested_value(
                    scoreboard_data,
                    ("player_count", "playerCount", "num_players", "current_players"),
                )
            )
            if players is None:
                player_list = self._first_nested_value(scoreboard_data, ("players", "player_stats"))
                if isinstance(player_list, list):
                    players = len(player_list)

        max_players = self._integer(
            self._first_nested_value(
                game_data,
                ("max_players", "maxPlayers", "player_slots", "slots"),
            )
        )
        if map_name is None and players is None:
            return None
        return {
            "available": True,
            "source": "CRCON",
            "map": map_name or "Unknown",
            "players": players,
            "max_players": max_players,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _fetch_crcon_live_state(self, stats_url: str) -> dict[str, object] | None:
        parsed = urlparse(stats_url)
        path = parsed.path or ""
        api_index = path.lower().find("/api/")
        prefix = path[:api_index] if api_index >= 0 else ""
        origin = f"{parsed.scheme}://{parsed.netloc}{prefix.rstrip('/')}"
        game_url = f"{origin}/api/get_live_game_stats"
        scoreboard_url = f"{origin}/api/get_live_scoreboard"

        game_payload = await self._fetch_public_json(game_url)
        if game_payload is None:
            return None
        state = self._parse_crcon_live_state(game_payload, None)
        if state is not None and state.get("players") is not None:
            return state
        scoreboard_payload = await self._fetch_public_json(scoreboard_url)
        return self._parse_crcon_live_state(game_payload, scoreboard_payload)

    async def _fetch_bifrost_live_state(self, stats_url: str) -> dict[str, object] | None:
        match = BIFROST_SERVER_PATTERN.search(urlparse(stats_url).path)
        if match is None:
            return None
        server_id = match.group(1)
        try:
            async with self._bifrost_lock:
                if self._bifrost_client is None:
                    self._bifrost_client = BifrostBackendClient(
                        {"bifrost": {"server_id": server_id, "game_type": "HLL"}}
                    )
                else:
                    self._bifrost_client.server_id = server_id
                data = await self._bifrost_client.get_mapvote_game_state()
        except HLLBackendError:
            LOGGER.info("Bifrost live state is unavailable for server %s", server_id)
            return None
        if not isinstance(data, dict):
            return None

        raw_payload = data.get("data")
        if isinstance(raw_payload, str):
            try:
                raw_payload = json.loads(raw_payload)
            except json.JSONDecodeError:
                raw_payload = {}
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        team1 = data.get("team1") if isinstance(data.get("team1"), dict) else {}
        team2 = data.get("team2") if isinstance(data.get("team2"), dict) else {}
        player_count = self._integer(payload.get("playerCount"))
        if player_count is None:
            player_count = (self._integer(team1.get("playerCount")) or 0) + (
                self._integer(team2.get("playerCount")) or 0
            )
        map_name = self._map_name(
            payload.get("currentMap") or payload.get("current_map") or payload.get("map")
        )
        return {
            "available": True,
            "source": "Bifrost",
            "map": map_name or "Unknown",
            "players": player_count,
            "max_players": self._integer(payload.get("maxPlayers")),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _fetch_live_state(self, stats_url: str) -> dict[str, object]:
        hostname = (urlparse(stats_url).hostname or "").lower()
        if hostname == "frostbite.bifrostgaming.com" or hostname.endswith(".bifrostgaming.com"):
            state = await self._fetch_bifrost_live_state(stats_url)
        else:
            state = await self._fetch_crcon_live_state(stats_url)
        return state or {
            "available": False,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _refresh_post_message(self, message_id: int, post: dict[str, object]) -> None:
        channel_id = self._integer(post.get("channel_id"))
        if channel_id is None:
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.Forbidden, discord.HTTPException):
                return
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        try:
            message = await channel.fetch_message(message_id)
            await message.edit(embed=self.build_post_embed(post), view=RaidSignupView())
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            LOGGER.info("Could not refresh raid message %s", message_id)

    async def _live_refresh_loop(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            await asyncio.sleep(LIVE_REFRESH_SECONDS)
            now = datetime.now(timezone.utc)
            snapshot = list(self._posts.items())
            for message_id, post in snapshot:
                try:
                    created_at = datetime.fromisoformat(str(post.get("created_at", "")))
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if (now - created_at).total_seconds() > LIVE_REFRESH_MAX_AGE_SECONDS:
                    continue
                stats_url = str(post.get("stats_url") or "")
                if not stats_url:
                    continue
                try:
                    live_state = await self._fetch_live_state(stats_url)
                    async with self._lock:
                        current = self._posts.get(message_id)
                        if current is None:
                            continue
                        current["live_state"] = live_state
                        self._save_posts()
                        refreshed_post = dict(current)
                    await self._refresh_post_message(int(message_id), refreshed_post)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    LOGGER.exception("Unexpected error refreshing raid message %s", message_id)

    def build_launcher_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Server Raiding",
            description=(
                "Start a new server raid call below. You will be asked for the clan, "
                "an announcement, and the CRCON or Bifrost server stats link."
            ),
            colour=discord.Colour.red(),
        )
        embed.set_footer(text="Use the button to create a new signup post.")
        return embed

    def build_post_embed(self, post: dict[str, object]) -> discord.Embed:
        clan_name = _safe_text(str(post.get("clan_name", "Unknown clan")), markdown=True)
        announcement = _safe_text(str(post.get("announcement", "")))
        stats_url = str(post.get("stats_url", ""))
        initiator_id = int(post.get("initiator_id", 0))
        participant_ids = [int(user_id) for user_id in post.get("participants", [])]

        embed = discord.Embed(
            title=f"⚔️ {clan_name} Raid Call",
            description=announcement,
            colour=discord.Colour.orange(),
            timestamp=datetime.fromisoformat(str(post["created_at"])),
        )
        embed.add_field(name="Initiated by", value=f"<@{initiator_id}>", inline=True)
        embed.add_field(name="Server stats", value=f"[Open CRCON / Bifrost]({stats_url})", inline=True)

        live_state = post.get("live_state")
        if isinstance(live_state, dict) and live_state.get("available"):
            embed.add_field(
                name="Current Map",
                value=str(live_state.get("map") or "Unknown"),
                inline=True,
            )
            players = live_state.get("players")
            max_players = live_state.get("max_players")
            if players is None:
                player_text = "Unknown"
            elif max_players is None:
                player_text = str(players)
            else:
                player_text = f"{players}/{max_players}"
            embed.add_field(name="Players", value=player_text, inline=True)
            embed.add_field(
                name="Live Data Source",
                value=str(live_state.get("source") or "Server stats"),
                inline=True,
            )
        elif isinstance(live_state, dict):
            embed.add_field(
                name="Live Server Data",
                value="Unavailable from this stats provider.",
                inline=False,
            )

        visible = participant_ids[:MAX_VISIBLE_RAIDERS]
        raider_lines = [f"<@{user_id}>" for user_id in visible]
        hidden_count = len(participant_ids) - len(visible)
        if hidden_count:
            raider_lines.append(f"…and {hidden_count} more")
        embed.add_field(
            name=f"Raiders ({len(participant_ids)})",
            value="\n".join(raider_lines) or "No one has joined yet.",
            inline=False,
        )
        embed.set_footer(text="Click Join Raid to add your name.")
        return embed

    async def create_post(
        self,
        interaction: discord.Interaction,
        *,
        clan_name: str,
        announcement: str,
        stats_url: str,
    ) -> None:
        created_at = datetime.now(timezone.utc).isoformat()
        post: dict[str, object] = {
            "guild_id": interaction.guild_id,
            "channel_id": interaction.channel_id,
            "initiator_id": interaction.user.id,
            "clan_name": clan_name.strip(),
            "announcement": announcement.strip(),
            "stats_url": stats_url,
            "participants": [interaction.user.id],
            "created_at": created_at,
        }
        post["live_state"] = await self._fetch_live_state(stats_url)
        message = await interaction.followup.send(
            embed=self.build_post_embed(post),
            view=RaidSignupView(),
            allowed_mentions=discord.AllowedMentions.none(),
            wait=True,
        )
        post["message_id"] = message.id
        async with self._lock:
            self._posts[str(message.id)] = post
            self._save_posts()

    async def update_signup(self, interaction: discord.Interaction, *, joining: bool) -> None:
        if interaction.message is None:
            await interaction.response.send_message("I could not identify this signup post.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        message_id = str(interaction.message.id)
        changed = False

        async with self._lock:
            post = self._posts.get(message_id)
            if post is None:
                await interaction.followup.send("This raid post is no longer active.", ephemeral=True)
                return

            participants = [int(user_id) for user_id in post.get("participants", [])]
            if joining and interaction.user.id not in participants:
                participants.append(interaction.user.id)
                changed = True
            elif not joining and interaction.user.id in participants:
                participants.remove(interaction.user.id)
                changed = True

            post["participants"] = participants
            if changed:
                self._save_posts()
            embed = self.build_post_embed(post)

        if changed:
            try:
                await interaction.message.edit(embed=embed, view=RaidSignupView())
            except discord.HTTPException:
                LOGGER.exception("Could not update raid message %s", message_id)
                await interaction.followup.send("Your signup was saved, but I could not refresh the embed.", ephemeral=True)
                return

        if joining:
            response = "You have joined this raid." if changed else "You are already on this raid."
        else:
            response = "You have left this raid." if changed else "You were not signed up for this raid."
        await interaction.followup.send(response, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Raid(bot))
