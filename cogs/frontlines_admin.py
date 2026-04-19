from __future__ import annotations

import logging
import os
from typing import Iterable

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from config import MAIN_GUILD_ID


GUILD_ID = MAIN_GUILD_ID
FRONTLINES_ADMIN_ROLE_ID = 1213495462632361994
FRONTLINES_API_URL = os.getenv("LIBERATION_FRONTLINES_API_URL", "http://127.0.0.1:8080").rstrip("/")
FRONTLINES_ADMIN_TOKEN = os.getenv("FRONTLINES_ADMIN_TOKEN", "").strip()
FRONTLINES_MAP_NAMES: tuple[str, ...] = tuple(
    sorted(
        {
            "Carentan Warfare",
            "Carentan Warfare (Night)",
            "Driel Warfare",
            "El Alamein Warfare",
            "Elsenborn Ridge Warfare (Dawn)",
            "Foy Warfare",
            "Hill 400 Warfare",
            "Hurtgen Forest Warfare",
            "Kharkov Warfare",
            "Kursk Warfare",
            "Mortain Warfare (Dusk)",
            "Omaha Beach Warfare",
            "Purple Heart Lane Warfare (Rain)",
            "Remagen Warfare",
            "Smolensk Warfare (Dusk)",
            "St. Marie Du Mont Warfare",
            "St. Mere Eglise Warfare",
            "Stalingrad Warfare",
            "Tobruk Warfare (Dawn)",
            "Utah Beach Warfare",
        }
    )
)


def _can_manage_frontlines(interaction: discord.Interaction) -> bool:
    user = interaction.user
    return isinstance(user, discord.Member) and any(role.id == FRONTLINES_ADMIN_ROLE_ID for role in user.roles)


class FrontlinesAdmin(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.logger = logging.getLogger("FrontlinesAdmin")
        self.session: aiohttp.ClientSession | None = None

    async def cog_load(self) -> None:
        timeout = aiohttp.ClientTimeout(total=10)
        self.session = aiohttp.ClientSession(timeout=timeout)

    async def cog_unload(self) -> None:
        if self.session is not None and not self.session.closed:
            await self.session.close()
        self.session = None

    async def _fetch_live_map_names(self) -> list[str]:
        if self.session is None:
            return []

        url = f"{FRONTLINES_API_URL}/api/maps"
        try:
            async with self.session.get(url) as response:
                if response.status >= 400:
                    return []
                payload = await response.json()
        except Exception:
            return []

        maps = payload.get("maps") if isinstance(payload, dict) else None
        if not isinstance(maps, list):
            return []

        names: list[str] = []
        for item in maps:
            if not isinstance(item, dict):
                continue
            map_name = str(item.get("map_name") or "").strip()
            if map_name:
                names.append(map_name)
        return sorted(set(names))

    async def _candidate_map_names(self) -> list[str]:
        names = set(FRONTLINES_MAP_NAMES)
        names.update(await self._fetch_live_map_names())
        return sorted(names)

    @staticmethod
    def _filter_map_names(names: Iterable[str], query: str) -> list[str]:
        lowered = query.casefold().strip()
        if not lowered:
            return list(names)[:25]
        matches = [name for name in names if lowered in name.casefold()]
        return matches[:25]

    async def frontlines_map_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        names = await self._candidate_map_names()
        return [app_commands.Choice(name=name, value=name) for name in self._filter_map_names(names, current)]

    @app_commands.command(name="frontlines_reset", description="Set a frontlines campaign liberation value.")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.guild_only()
    @app_commands.check(_can_manage_frontlines)
    @app_commands.describe(
        map_name="Campaign map to update",
        progress_percent="Liberation control between -100 and 100",
    )
    @app_commands.autocomplete(map_name=frontlines_map_autocomplete)
    async def frontlines_reset(
        self,
        interaction: discord.Interaction,
        map_name: str,
        progress_percent: app_commands.Range[float, -100.0, 100.0],
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if not FRONTLINES_ADMIN_TOKEN:
            await interaction.response.send_message(
                "FRONTLINES_ADMIN_TOKEN is not configured for the bot.",
                ephemeral=True,
            )
            return

        if self.session is None:
            await interaction.response.send_message("Frontlines admin client is not ready yet.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        url = f"{FRONTLINES_API_URL}/api/admin/frontlines/reset"
        payload = {
            "map_name": map_name,
            "control_value": float(progress_percent),
        }
        headers = {
            "Authorization": f"Bearer {FRONTLINES_ADMIN_TOKEN}",
            "Content-Type": "application/json",
        }

        try:
            async with self.session.post(url, json=payload, headers=headers) as response:
                response_payload = await response.json(content_type=None)
        except Exception as exc:
            self.logger.exception("frontlines_reset_request_failed map=%s error=%s", map_name, exc)
            await interaction.followup.send(f"Frontlines reset request failed: {exc}", ephemeral=True)
            return

        if not isinstance(response_payload, dict):
            await interaction.followup.send("Frontlines reset returned an unexpected response.", ephemeral=True)
            return

        if response.status >= 400:
            message = str(response_payload.get("message") or response_payload.get("error") or "Unknown error")
            await interaction.followup.send(f"Frontlines reset failed: {message}", ephemeral=True)
            return

        resolved_name = str(response_payload.get("map_name") or map_name)
        control_value = float(response_payload.get("control_value", progress_percent))
        occupied_faction = response_payload.get("occupied_faction")
        occupation_copy = f" Occupied faction: {occupied_faction}." if occupied_faction else ""
        await interaction.followup.send(
            f"Set {resolved_name} liberation control to {control_value:.2f}%.{occupation_copy}",
            ephemeral=True,
        )

    @frontlines_reset.error
    async def frontlines_reset_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CheckFailure):
            if interaction.response.is_done():
                await interaction.followup.send("You need the configured Admin role to use this command.", ephemeral=True)
            else:
                await interaction.response.send_message("You need the configured Admin role to use this command.", ephemeral=True)
            return

        self.logger.exception("frontlines_reset_command_failed error=%s", error)
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FrontlinesAdmin(bot))