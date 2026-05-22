from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.parse
from typing import Any, Protocol

import requests

from config.hll_API_config import (
    BIFROST_CLIENT_ID_ENV,
    BIFROST_CLIENT_SECRET_ENV,
    BIFROST_GRAPHQL_URL,
    BIFROST_OAUTH_URL,
    get_hll_backend_provider,
    get_hll_backend_server_config,
)


logger = logging.getLogger("HLLBackend")
ADMIN_CAM_ROLE = "Spectator"


class HLLBackendError(RuntimeError):
    pass


class HLLBackendConfigError(HLLBackendError):
    pass


class HLLBackendClient(Protocol):
    provider: str

    async def resolve_player_id_by_name(self, player_name: str) -> str | None:
        ...

    async def add_guild_member(
        self,
        player_id: str,
        player_name: str,
        *,
        platform: str = "PC",
        membership_type: str = "community",
    ) -> None:
        ...

    async def remove_guild_member(self, player_id: str) -> None:
        ...

    async def grant_admin_cam(self, player_id: str, player_name: str) -> None:
        ...

    async def revoke_admin_cam(self, player_id: str) -> None:
        ...


def _extract_first_player_id(data: Any) -> str | None:
    if isinstance(data, dict):
        if "player_id" in data and data["player_id"] is not None:
            return str(data["player_id"])
        for value in data.values():
            found = _extract_first_player_id(value)
            if found:
                return found
        return None
    if isinstance(data, list):
        for item in data:
            found = _extract_first_player_id(item)
            if found:
                return found
    return None


def _extract_error_message(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("message", "error", "detail"):
            value = payload.get(key)
            if value:
                return str(value)
        errors = payload.get("errors")
        if isinstance(errors, list):
            messages = []
            for item in errors:
                if isinstance(item, dict):
                    message = item.get("message") or item.get("detail") or item.get("error")
                    if message:
                        messages.append(str(message))
                elif item:
                    messages.append(str(item))
            if messages:
                return "; ".join(messages)
    if isinstance(payload, list):
        messages = []
        for item in payload:
            if isinstance(item, dict):
                message = item.get("message") or item.get("detail") or item.get("error")
                if message:
                    messages.append(str(message))
            elif item:
                messages.append(str(item))
        if messages:
            return "; ".join(messages)
    if isinstance(payload, str) and payload.strip():
        return payload.strip()
    return "Unknown error"


def _extract_retry_after_seconds(payload: Any) -> float | None:
    if not isinstance(payload, dict):
        return None

    errors = payload.get("errors")
    if not isinstance(errors, list):
        return None

    for item in errors:
        if not isinstance(item, dict):
            continue
        extensions = item.get("extensions")
        if not isinstance(extensions, dict):
            continue
        retry_after = extensions.get("retryAfter")
        if retry_after is None:
            continue
        try:
            return max(0.0, float(retry_after))
        except (TypeError, ValueError):
            continue

    return None


def _parse_response_payload(response: requests.Response) -> Any:
    body = response.text or ""
    if not body.strip():
        return None
    try:
        return response.json()
    except json.JSONDecodeError:
        return body


class CRCONBackendClient:
    provider = "crcon"

    def __init__(self, server_config: dict[str, Any]) -> None:
        crcon_config = server_config.get("crcon") or {}
        panel_url = str(crcon_config.get("panel_url") or "").strip()
        self.panel_url = panel_url if panel_url.endswith("/") else f"{panel_url}/"
        self.api_key_env = str(crcon_config.get("api_key_env") or "CRCON_API_KEY")

    def _api_key(self) -> str:
        return os.getenv(self.api_key_env, "").strip()

    def _auth_headers(self) -> dict[str, str]:
        api_key = self._api_key()
        if not api_key:
            raise HLLBackendConfigError(f"{self.api_key_env} is not configured for the selected HLL backend")
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, endpoint: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
        headers = self._auth_headers()
        url = self.panel_url + endpoint

        def do_request() -> tuple[int, Any]:
            response = requests.request(method, url, headers=headers, json=payload, timeout=15)
            body = response.text or ""
            if not body.strip():
                return response.status_code, None
            try:
                return response.status_code, response.json()
            except json.JSONDecodeError:
                return response.status_code, body

        try:
            return await asyncio.to_thread(do_request)
        except requests.RequestException as exc:
            raise HLLBackendError(str(exc)) from exc

    async def resolve_player_id_by_name(self, player_name: str) -> str | None:
        endpoint = f"get_players_history?player_name={urllib.parse.quote(player_name, safe='')}&page_size=1"
        status, payload = await self._request("GET", endpoint)
        if status >= 400:
            raise HLLBackendError(_extract_error_message(payload))
        if not isinstance(payload, dict) or payload.get("failed") or payload.get("error"):
            return None
        return _extract_first_player_id(payload.get("result", payload))

    async def add_guild_member(
        self,
        player_id: str,
        player_name: str,
        *,
        platform: str = "PC",
        membership_type: str = "community",
    ) -> None:
        raise HLLBackendConfigError("Guild member sync is only supported by the Bifrost backend")

    async def remove_guild_member(self, player_id: str) -> None:
        raise HLLBackendConfigError("Guild member sync is only supported by the Bifrost backend")

    async def grant_admin_cam(self, player_id: str, player_name: str) -> None:
        quoted_role = urllib.parse.quote(ADMIN_CAM_ROLE, safe="")
        endpoint = f"add_admin?role={quoted_role}"
        payload = {
            "player_id": player_id,
            "role": ADMIN_CAM_ROLE,
            "description": player_name,
        }
        status, response_payload = await self._request("POST", endpoint, payload)
        if status >= 400:
            raise HLLBackendError(_extract_error_message(response_payload))

    async def revoke_admin_cam(self, player_id: str) -> None:
        endpoint = f"remove_admin?player_id={urllib.parse.quote(player_id, safe='')}"
        status, response_payload = await self._request("POST", endpoint, {"player_id": player_id})
        if status >= 400:
            raise HLLBackendError(_extract_error_message(response_payload))


class BifrostBackendClient:
    provider = "bifrost"
    max_rate_limit_retries = 3

    def __init__(self, server_config: dict[str, Any]) -> None:
        bifrost_server = server_config.get("bifrost") or {}
        self.server_id = str(bifrost_server.get("server_id") or "").strip()
        self.game_type = str(bifrost_server.get("game_type") or "HLL").strip() or "HLL"
        self.oauth_url = BIFROST_OAUTH_URL
        self.graphql_url = BIFROST_GRAPHQL_URL
        self.client_id_env = BIFROST_CLIENT_ID_ENV
        self.client_secret_env = BIFROST_CLIENT_SECRET_ENV
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

        if not self.server_id:
            raise HLLBackendConfigError("BIFROST_SERVER_ID is not configured for the selected HLL backend")

    def _client_credentials(self) -> tuple[str, str]:
        client_id = os.getenv(self.client_id_env, "").strip()
        client_secret = os.getenv(self.client_secret_env, "").strip()
        if not client_id or not client_secret:
            raise HLLBackendConfigError(
                f"{self.client_id_env} and {self.client_secret_env} are required for the selected HLL backend"
            )
        return client_id, client_secret

    async def _get_access_token(self) -> str:
        now = time.time()
        if self._access_token and now < self._access_token_expires_at - 60:
            return self._access_token

        async with self._token_lock:
            now = time.time()
            if self._access_token and now < self._access_token_expires_at - 60:
                return self._access_token

            client_id, client_secret = self._client_credentials()

            def do_request() -> tuple[int, Any]:
                response = requests.post(
                    self.oauth_url,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    data={
                        "grant_type": "client_credentials",
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                    timeout=15,
                )
                return response.status_code, _parse_response_payload(response)

            payload: Any = None
            for attempt in range(1, self.max_rate_limit_retries + 1):
                try:
                    status_code, payload = await asyncio.to_thread(do_request)
                except requests.RequestException as exc:
                    raise HLLBackendError(f"Failed to fetch Bifrost access token: {exc}") from exc

                if status_code == 429:
                    retry_after = _extract_retry_after_seconds(payload)
                    if retry_after is None or attempt >= self.max_rate_limit_retries:
                        raise HLLBackendError(
                            f"Bifrost OAuth rate limited: {_extract_error_message(payload)}"
                        )
                    logger.warning(
                        "bifrost_oauth_rate_limited retry_after=%s attempt=%s",
                        retry_after,
                        attempt,
                    )
                    await asyncio.sleep(retry_after)
                    continue

                if status_code >= 400:
                    raise HLLBackendError(f"Failed to fetch Bifrost access token: {_extract_error_message(payload)}")

                if not isinstance(payload, dict):
                    raise HLLBackendError("Bifrost token response returned an unexpected payload")
                break

            access_token = str(payload.get("access_token") or "").strip()
            expires_in = int(payload.get("expires_in") or 3600)
            if not access_token:
                raise HLLBackendError("Bifrost token response did not include an access token")

            self._access_token = access_token
            self._access_token_expires_at = time.time() + expires_in
            return access_token

    async def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        access_token = await self._get_access_token()

        def do_request() -> tuple[int, Any]:
            response = requests.post(
                self.graphql_url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={"query": query, "variables": variables},
                timeout=20,
            )
            return response.status_code, _parse_response_payload(response)

        payload: Any = None
        for attempt in range(1, self.max_rate_limit_retries + 1):
            try:
                status_code, payload = await asyncio.to_thread(do_request)
            except requests.RequestException as exc:
                raise HLLBackendError(f"Bifrost request failed: {exc}") from exc

            if status_code == 429:
                retry_after = _extract_retry_after_seconds(payload)
                if retry_after is None or attempt >= self.max_rate_limit_retries:
                    raise HLLBackendError(f"Bifrost rate limited: {_extract_error_message(payload)}")
                logger.warning(
                    "bifrost_graphql_rate_limited retry_after=%s attempt=%s",
                    retry_after,
                    attempt,
                )
                await asyncio.sleep(retry_after)
                continue

            if status_code >= 400:
                raise HLLBackendError(f"Bifrost request failed: {_extract_error_message(payload)}")

            if not isinstance(payload, dict):
                raise HLLBackendError("Bifrost returned an unexpected response payload")
            break

        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            messages = []
            for item in errors:
                if isinstance(item, dict) and item.get("message"):
                    messages.append(str(item["message"]))
                elif item:
                    messages.append(str(item))
            raise HLLBackendError("; ".join(messages) or "Bifrost returned GraphQL errors")

        data = payload.get("data")
        if not isinstance(data, dict):
            raise HLLBackendError("Bifrost returned an unexpected response payload")
        return data

    async def resolve_player_id_by_name(self, player_name: str) -> str | None:
        query = (
            "query GuildSearchPlayer($serverId: ID!, $searchTerm: String!, $gameType: String) {"
            " guildSearchPlayer(serverId: $serverId, searchTerm: $searchTerm, gameType: $gameType) {"
            " players { playerName playerId }"
            " }"
            "}"
        )
        data = await self._graphql(
            query,
            {
                "serverId": self.server_id,
                "searchTerm": player_name,
                "gameType": self.game_type,
            },
        )
        payload = data.get("guildSearchPlayer") or {}
        players = payload.get("players") if isinstance(payload, dict) else None
        if not isinstance(players, list) or not players:
            return None

        exact_match = None
        first_match = None
        for item in players:
            if not isinstance(item, dict):
                continue
            player_id = str(item.get("playerId") or "").strip()
            if not player_id:
                continue
            if first_match is None:
                first_match = player_id
            player_name_value = str(item.get("playerName") or "").strip()
            if player_name_value.casefold() == player_name.casefold():
                exact_match = player_id
                break

        return exact_match or first_match

    async def add_guild_member(
        self,
        player_id: str,
        player_name: str,
        *,
        platform: str = "PC",
        membership_type: str = "community",
    ) -> None:
        query = (
            "mutation GuildAddMember($input: GuildAddMemberInput!) {"
            " guildAddMember(input: $input) { success message error }"
            "}"
        )
        data = await self._graphql(
            query,
            {
                "input": {
                    "playerId": player_id,
                    "playerName": player_name,
                    "platform": platform,
                    "membershipType": membership_type,
                }
            },
        )
        payload = data.get("guildAddMember") or {}
        if not isinstance(payload, dict) or not payload.get("success"):
            raise HLLBackendError(_extract_error_message(payload))

    async def remove_guild_member(self, player_id: str) -> None:
        query = (
            "mutation GuildRemoveMember($input: GuildRemoveMemberInput!) {"
            " guildRemoveMember(input: $input) { success message error }"
            "}"
        )
        data = await self._graphql(
            query,
            {
                "input": {
                    "playerId": player_id,
                }
            },
        )
        payload = data.get("guildRemoveMember") or {}
        if not isinstance(payload, dict) or not payload.get("success"):
            raise HLLBackendError(_extract_error_message(payload))

    async def grant_admin_cam(self, player_id: str, player_name: str) -> None:
        query = (
            "mutation GuildGrantAdminCam($input: GuildGrantAdminCamInput!) {"
            " guildGrantAdminCam(input: $input) { success message error }"
            "}"
        )
        data = await self._graphql(
            query,
            {
                "input": {
                    "serverId": self.server_id,
                    "playerId": player_id,
                    "playerName": player_name,
                }
            },
        )
        payload = data.get("guildGrantAdminCam") or {}
        if not isinstance(payload, dict) or not payload.get("success"):
            raise HLLBackendError(_extract_error_message(payload))

    async def revoke_admin_cam(self, player_id: str) -> None:
        query = (
            "mutation GuildRevokeAdminCam($input: GuildRevokeAdminCamInput!) {"
            " guildRevokeAdminCam(input: $input) { success message error }"
            "}"
        )
        data = await self._graphql(
            query,
            {
                "input": {
                    "serverId": self.server_id,
                    "playerId": player_id,
                }
            },
        )
        payload = data.get("guildRevokeAdminCam") or {}
        if not isinstance(payload, dict) or not payload.get("success"):
            raise HLLBackendError(_extract_error_message(payload))


_shared_clients: dict[tuple[str, str], HLLBackendClient] = {}


def get_hll_backend_client(server_name: str | None = None) -> HLLBackendClient:
    provider = get_hll_backend_provider()
    cache_key = (provider, server_name or "")
    cached = _shared_clients.get(cache_key)
    if cached is not None:
        return cached

    server_config = get_hll_backend_server_config(server_name)
    if provider == "crcon":
        client: HLLBackendClient = CRCONBackendClient(server_config)
    elif provider == "bifrost":
        client = BifrostBackendClient(server_config)
    else:
        raise HLLBackendConfigError(f"Unsupported HLL backend provider: {provider}")

    _shared_clients[cache_key] = client
    return client