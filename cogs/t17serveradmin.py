from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from state_io import atomic_json_dump

from clan_t17_lookup import ClanT17Lookup
from config import MAIN_GUILD_ID
from config.hll_API_config import get_hll_backend_status
from data_paths import data_path
from hll_API_backend import HLLBackendError, get_hll_backend_client


GUILD_ID = MAIN_GUILD_ID
ADMIN_ROLE_IDS = {
    1279832920479109160
}
ADMIN_CAM_ROLE = "Spectator"
STATE_FILE = data_path("t17_admin_cam_grants.json")
REMOVAL_RETRY_SECONDS = 300
ADMIN_CAM_SERVER_CHOICES = [
    app_commands.Choice(name="Events", value="main"),
    app_commands.Choice(name="Public", value="server_2"),
]


def _can_manage_t17_server_admin(interaction: discord.Interaction) -> bool:
    user = interaction.user
    return isinstance(user, discord.Member) and any(role.id in ADMIN_ROLE_IDS for role in user.roles)


class T17ServerAdmin(commands.Cog, name="[API] T17ServerAdmin"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.logger = logging.getLogger("T17ServerAdmin")
        self.backend = get_hll_backend_client()
        self.lookup = ClanT17Lookup(self.backend, logger=self.logger)
        self._removal_tasks: dict[str, asyncio.Task[None]] = {}

    async def cog_load(self) -> None:
        await self._restore_pending_grants()

    async def cog_unload(self) -> None:
        for task in self._removal_tasks.values():
            task.cancel()
        self._removal_tasks.clear()

    def _grant_key(self, guild_id: int, user_id: int, server_name: str = "main") -> str:
        return f"{guild_id}:{user_id}:{server_name}"

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
        atomic_json_dump(STATE_FILE, state, sort_keys=True)

    def _upsert_grant(self, grant: dict[str, Any]) -> None:
        state = self._load_state()
        state.setdefault("grants", {})[
            self._grant_key(
                int(grant["guild_id"]),
                int(grant["user_id"]),
                str(grant.get("server_name") or "main"),
            )
        ] = grant
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

    async def _add_admin_cam(self, server_name: str, player_id: str, description: str) -> None:
        await get_hll_backend_client(server_name).grant_admin_cam(player_id, description)

    async def _remove_admin_cam(self, grant: dict[str, Any]) -> None:
        player_id = str(grant["player_id"])
        server_name = str(grant.get("server_name") or "main")
        await get_hll_backend_client(server_name).revoke_admin_cam(player_id)

        self.logger.info(
            "t17admincam_removed server=%s member=%s player_id=%s granted_by=%s",
            server_name,
            grant.get("member_display_name"),
            player_id,
            grant.get("granted_by_name"),
        )

    def _description_for_member(self, member: discord.Member, queries: list[str]) -> str:
        if queries:
            return queries[0]
        normalized = self.lookup.normalize_discord_username(member.display_name, strip_rank_prefix=True)
        return normalized or member.display_name

    @app_commands.command(name="hll_backend_status", description="Show the active HLL backend configuration status.")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.guild_only()
    @app_commands.check(_can_manage_t17_server_admin)
    async def hll_backend_status(self, interaction: discord.Interaction) -> None:
        status = get_hll_backend_status()

        embed = discord.Embed(title="HLL Backend Status", colour=discord.Colour.blurple())
        embed.add_field(name="Provider", value=str(status.get("provider") or "unknown"), inline=False)
        embed.add_field(name="Server Alias", value=str(status.get("server_name") or "unknown"), inline=False)

        provider = str(status.get("provider") or "").lower()
        if provider == "crcon":
            embed.add_field(name="Panel URL", value=str(status.get("panel_url") or "not configured"), inline=False)
            embed.add_field(name="API Key Env", value=str(status.get("api_key_env") or "CRCON_API_KEY"), inline=True)
            embed.add_field(
                name="API Key Present",
                value="yes" if status.get("api_key_present") else "no",
                inline=True,
            )
        elif provider == "bifrost":
            embed.add_field(name="OAuth URL", value=str(status.get("oauth_url") or "not configured"), inline=False)
            embed.add_field(name="GraphQL URL", value=str(status.get("graphql_url") or "not configured"), inline=False)
            embed.add_field(name="Server ID", value=str(status.get("server_id") or "not configured"), inline=False)
            embed.add_field(name="Game Type", value=str(status.get("game_type") or "HLL"), inline=True)
            embed.add_field(
                name="Client ID Present",
                value="yes" if status.get("client_id_present") else "no",
                inline=True,
            )
            embed.add_field(
                name="Client Secret Present",
                value="yes" if status.get("client_secret_present") else "no",
                inline=True,
            )

        if status.get("error"):
            embed.add_field(name="Error", value=str(status["error"]), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="t17admincam", description="Grant temporary Spectator admin cam using a member's T17 ID.")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.guild_only()
    @app_commands.check(_can_manage_t17_server_admin)
    @app_commands.describe(
        member="Discord member to grant Spectator admin cam",
        server="Bifrost server to grant access on",
        duration_hours="How long to grant access for, in hours",
    )
    @app_commands.choices(server=ADMIN_CAM_SERVER_CHOICES)
    async def t17admincam(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        server: app_commands.Choice[str],
        duration_hours: app_commands.Range[int, 1, 168],
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        t17_id, source, queries = await self.lookup.resolve_member_for_role(member, role_name="t17serveradmin")
        if not t17_id:
            await interaction.followup.send(
                f"No T17 ID could be resolved for {member.mention}. Add or correct it in the shared T17 lookup first.",
                ephemeral=True,
            )
            return

        selected_server = server.value
        description = self._description_for_member(member, queries)

        try:
            await self._add_admin_cam(selected_server, t17_id, description)
        except HLLBackendError as exc:
            self.logger.exception(
                "t17admincam_add_failed server=%s member_id=%s t17_id=%s error=%s",
                selected_server,
                member.id,
                t17_id,
                exc,
            )
            await interaction.followup.send(f"Failed to grant Spectator admin cam: {exc}", ephemeral=True)
            return
        except Exception as exc:
            self.logger.exception(
                "t17admincam_add_failed server=%s member_id=%s t17_id=%s error=%s",
                selected_server,
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
            "server_name": selected_server,
            "server_label": server.name,
            "source": source,
            "queries": queries,
            "duration_hours": int(duration_hours),
            "created_at": time.time(),
            "expires_at": expires_at,
            "granted_by_id": interaction.user.id,
            "granted_by_name": getattr(interaction.user, "display_name", str(interaction.user)),
        }
        grant_key = self._grant_key(interaction.guild.id, member.id, selected_server)
        self._upsert_grant(grant)
        self._schedule_removal(grant_key)

        await interaction.followup.send(
            f"Granted {ADMIN_CAM_ROLE} admin cam to {member.mention}.\n"
            f"Server: **{server.name}**\n"
            f"T17 ID: `{t17_id}`\n"
            f"Description sent to backend: `{description}`\n"
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

    @hll_backend_status.error
    async def hll_backend_status_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CheckFailure):
            if interaction.response.is_done():
                await interaction.followup.send("You need one of the configured admin roles to use this command.", ephemeral=True)
            else:
                await interaction.response.send_message(
                    "You need one of the configured admin roles to use this command.",
                    ephemeral=True,
                )
            return

        self.logger.exception("hll_backend_status_command_failed error=%s", error)
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(T17ServerAdmin(bot))
