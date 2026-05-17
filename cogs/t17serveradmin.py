from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.parse
from typing import Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from clan_t17_lookup import ClanT17Lookup
from config import CRCON_PANEL_URL, MAIN_GUILD_ID
from data_paths import data_path


GUILD_ID = MAIN_GUILD_ID
ADMIN_ROLE_IDS = {
    1279832920479109160
}
CRCON_API_KEY = os.getenv("CRCON_API_KEY")
ADMIN_CAM_ROLE = "Spectator"
STATE_FILE = data_path("t17_admin_cam_grants.json")
REMOVAL_RETRY_SECONDS = 300


def _can_manage_t17_server_admin(interaction: discord.Interaction) -> bool:
    user = interaction.user
    return isinstance(user, discord.Member) and any(role.id in ADMIN_ROLE_IDS for role in user.roles)


class T17ServerAdmin(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.logger = logging.getLogger("T17ServerAdmin")
        self.lookup = ClanT17Lookup(logger=self.logger)
        self.session: aiohttp.ClientSession | None = None
        self._removal_tasks: dict[str, asyncio.Task[None]] = {}

    async def cog_load(self) -> None:
        timeout = aiohttp.ClientTimeout(total=15)
        self.session = aiohttp.ClientSession(timeout=timeout)
        await self._restore_pending_grants()

    async def cog_unload(self) -> None:
        for task in self._removal_tasks.values():
            task.cancel()
        self._removal_tasks.clear()

        if self.session is not None and not self.session.closed:
            await self.session.close()
        self.session = None

    def _grant_key(self, guild_id: int, user_id: int) -> str:
        return f"{guild_id}:{user_id}"

    def _load_state(self) -> dict[str, Any]:
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"grants": {}}

        if not isinstance(payload, dict):
            return {"grants": {}}

        grants = payload.get("grants")
        if not isinstance(grants, dict):
            return {"grants": {}}

        return {"grants": grants}

    def _save_state(self, state: dict[str, Any]) -> None:
        tmp_path = f"{STATE_FILE}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
        os.replace(tmp_path, STATE_FILE)

    def _upsert_grant(self, grant: dict[str, Any]) -> None:
        state = self._load_state()
        state.setdefault("grants", {})[self._grant_key(int(grant["guild_id"]), int(grant["user_id"]))] = grant
        self._save_state(state)

    def _remove_grant_record(self, grant_key: str) -> None:
        state = self._load_state()
        grants = state.setdefault("grants", {})
        if grant_key in grants:
            del grants[grant_key]
            self._save_state(state)

    def _get_grant(self, grant_key: str) -> dict[str, Any] | None:
        state = self._load_state()
        grant = state.get("grants", {}).get(grant_key)
        return grant if isinstance(grant, dict) else None

    def _cancel_removal_task(self, grant_key: str) -> None:
        task = self._removal_tasks.pop(grant_key, None)
        if task is not None:
            task.cancel()

    def _schedule_removal(self, grant_key: str) -> None:
        self._cancel_removal_task(grant_key)
        self._removal_tasks[grant_key] = asyncio.create_task(self._run_removal(grant_key))

    async def _restore_pending_grants(self) -> None:
        state = self._load_state()
        for grant_key, grant in state.get("grants", {}).items():
            if not isinstance(grant, dict):
                continue
            self._schedule_removal(grant_key)

    async def _run_removal(self, grant_key: str) -> None:
        try:
            while True:
                grant = self._get_grant(grant_key)
                if grant is None:
                    return

                expires_at = float(grant.get("expires_at", 0))
                delay_seconds = max(0.0, expires_at - time.time())
                if delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)

                try:
                    await self._remove_admin_cam(grant)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.logger.exception(
                        "t17admincam_remove_failed grant_key=%s player_id=%s error=%s",
                        grant_key,
                        grant.get("player_id"),
                        exc,
                    )
                    await asyncio.sleep(REMOVAL_RETRY_SECONDS)
                    continue

                self._remove_grant_record(grant_key)
                return
        except asyncio.CancelledError:
            raise
        finally:
            current = self._removal_tasks.get(grant_key)
            if current is asyncio.current_task():
                self._removal_tasks.pop(grant_key, None)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {CRCON_API_KEY}",
            "Content-Type": "application/json",
        }

    async def _post_crcon(self, url: str, payload: dict[str, Any]) -> tuple[int, Any]:
        if self.session is None:
            raise RuntimeError("CRCON client is not ready yet.")

        async with self.session.post(url, json=payload, headers=self._headers()) as response:
            body = await response.text()

        if not body.strip():
            return response.status, None

        try:
            return response.status, json.loads(body)
        except json.JSONDecodeError:
            return response.status, body

    def _extract_error_message(self, payload: Any) -> str:
        if isinstance(payload, dict):
            for key in ("message", "error", "detail"):
                value = payload.get(key)
                if value:
                    return str(value)
        if isinstance(payload, str) and payload.strip():
            return payload.strip()
        return "Unknown error"

    async def _add_admin_cam(self, player_id: str, description: str) -> None:
        quoted_role = urllib.parse.quote(ADMIN_CAM_ROLE, safe="")
        url = f"{CRCON_PANEL_URL}add_admin?role={quoted_role}"
        payload = {
            "player_id": player_id,
            "role": ADMIN_CAM_ROLE,
            "description": description,
        }

        status, response_payload = await self._post_crcon(url, payload)
        if status >= 400:
            raise RuntimeError(self._extract_error_message(response_payload))

    async def _remove_admin_cam(self, grant: dict[str, Any]) -> None:
        player_id = str(grant["player_id"])
        url = f"{CRCON_PANEL_URL}remove_admin?player_id={urllib.parse.quote(player_id, safe='')}"
        payload = {"player_id": player_id}

        status, response_payload = await self._post_crcon(url, payload)
        if status >= 400:
            raise RuntimeError(self._extract_error_message(response_payload))

        self.logger.info(
            "t17admincam_removed member=%s player_id=%s granted_by=%s",
            grant.get("member_display_name"),
            player_id,
            grant.get("granted_by_name"),
        )

    def _description_for_member(self, member: discord.Member, queries: list[str]) -> str:
        if queries:
            return queries[0]
        normalized = self.lookup.normalize_discord_username(member.display_name, strip_rank_prefix=True)
        return normalized or member.display_name

    @app_commands.command(name="t17admincam", description="Grant temporary Spectator admin cam using a member's T17 ID.")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.guild_only()
    @app_commands.check(_can_manage_t17_server_admin)
    @app_commands.describe(member="Discord member to grant Spectator admin cam", duration_hours="How long to grant access for, in hours")
    async def t17admincam(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        duration_hours: app_commands.Range[int, 1, 168],
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if not CRCON_API_KEY:
            await interaction.response.send_message("CRCON_API_KEY is not configured for the bot.", ephemeral=True)
            return

        if self.session is None:
            await interaction.response.send_message("CRCON client is not ready yet.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        t17_id, source, queries = await self.lookup.resolve_member_for_role(member, role_name="t17serveradmin")
        if not t17_id:
            await interaction.followup.send(
                f"No T17 ID could be resolved for {member.mention}. Add or correct it in the shared T17 lookup first.",
                ephemeral=True,
            )
            return

        description = self._description_for_member(member, queries)

        try:
            await self._add_admin_cam(t17_id, description)
        except Exception as exc:
            self.logger.exception(
                "t17admincam_add_failed member_id=%s t17_id=%s error=%s",
                member.id,
                t17_id,
                exc,
            )
            await interaction.followup.send(f"Failed to grant Spectator admin cam: {exc}", ephemeral=True)
            return

        expires_at = time.time() + (int(duration_hours) * 3600)
        grant = {
            "guild_id": interaction.guild.id,
            "user_id": member.id,
            "member_display_name": member.display_name,
            "player_id": t17_id,
            "role": ADMIN_CAM_ROLE,
            "description": description,
            "source": source,
            "queries": queries,
            "duration_hours": int(duration_hours),
            "created_at": time.time(),
            "expires_at": expires_at,
            "granted_by_id": interaction.user.id,
            "granted_by_name": getattr(interaction.user, "display_name", str(interaction.user)),
        }
        grant_key = self._grant_key(interaction.guild.id, member.id)
        self._upsert_grant(grant)
        self._schedule_removal(grant_key)

        await interaction.followup.send(
            f"Granted {ADMIN_CAM_ROLE} admin cam to {member.mention}.\n"
            f"T17 ID: `{t17_id}`\n"
            f"Description sent to CRCON: `{description}`\n"
            f"Resolved via: `{source}`\n"
            f"Expires: <t:{int(expires_at)}:F> (<t:{int(expires_at)}:R>)",
        )

    @t17admincam.error
    async def t17admincam_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CheckFailure):
            if interaction.response.is_done():
                await interaction.followup.send("You need one of the configured admin roles to use this command.", ephemeral=True)
            else:
                await interaction.response.send_message(
                    "You need one of the configured admin roles to use this command.",
                    ephemeral=True,
                )
            return

        self.logger.exception("t17admincam_command_failed error=%s", error)
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(T17ServerAdmin(bot))