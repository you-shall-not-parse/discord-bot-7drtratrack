from __future__ import annotations

import os
from typing import Any


# Set these in code to choose which backend/server this bot should use.
HLL_BACKEND_PROVIDER = "crcon"
HLL_BACKEND_DEFAULT_SERVER = "main"

BIFROST_OAUTH_URL = os.getenv("BIFROST_OAUTH_URL", "https://api.dev.bifrostgaming.com/v1/oauth/token").strip()
BIFROST_GRAPHQL_URL = os.getenv("BIFROST_GRAPHQL_URL", "https://api.dev.bifrostgaming.com/v1/graphql").strip()
BIFROST_CLIENT_ID_ENV = "BIFROST_CLIENT_ID"
BIFROST_CLIENT_SECRET_ENV = "BIFROST_CLIENT_SECRET"

HLL_BACKEND_SERVERS: dict[str, dict[str, Any]] = {
    "main": {
        "crcon": {
            "panel_url": os.getenv("CRCON_PANEL_URL", "https://7dr.hlladmin.com/api/").strip(),
            "api_key_env": "CRCON_API_KEY",
        },
        "bifrost": {
            "server_id": os.getenv("BIFROST_SERVER_ID", "").strip(),
            "game_type": os.getenv("BIFROST_GAME_TYPE", "HLL").strip() or "HLL",
        },
    }
}


def get_hll_backend_provider() -> str:
    return HLL_BACKEND_PROVIDER


def get_hll_backend_server_config(server_name: str | None = None) -> dict[str, Any]:
    selected_name = (server_name or HLL_BACKEND_DEFAULT_SERVER).strip() or HLL_BACKEND_DEFAULT_SERVER
    try:
        return HLL_BACKEND_SERVERS[selected_name]
    except KeyError as exc:
        raise KeyError(f"Unknown HLL backend server configuration: {selected_name}") from exc


def get_hll_backend_default_server_name() -> str:
    return HLL_BACKEND_DEFAULT_SERVER


def get_hll_backend_status(server_name: str | None = None) -> dict[str, Any]:
    selected_name = (server_name or HLL_BACKEND_DEFAULT_SERVER).strip() or HLL_BACKEND_DEFAULT_SERVER
    server_config = get_hll_backend_server_config(selected_name)
    provider = get_hll_backend_provider()

    status: dict[str, Any] = {
        "provider": provider,
        "server_name": selected_name,
        "server_exists": True,
    }

    if provider == "crcon":
        crcon_config = server_config.get("crcon") or {}
        api_key_env = str(crcon_config.get("api_key_env") or "CRCON_API_KEY")
        status.update(
            {
                "panel_url": str(crcon_config.get("panel_url") or "").strip(),
                "api_key_env": api_key_env,
                "api_key_present": bool(os.getenv(api_key_env, "").strip()),
            }
        )
        return status

    if provider == "bifrost":
        bifrost_config = server_config.get("bifrost") or {}
        client_id = os.getenv(BIFROST_CLIENT_ID_ENV, "").strip()
        client_secret = os.getenv(BIFROST_CLIENT_SECRET_ENV, "").strip()
        status.update(
            {
                "oauth_url": BIFROST_OAUTH_URL,
                "graphql_url": BIFROST_GRAPHQL_URL,
                "client_id_env": BIFROST_CLIENT_ID_ENV,
                "client_secret_env": BIFROST_CLIENT_SECRET_ENV,
                "client_id_present": bool(client_id),
                "client_secret_present": bool(client_secret),
                "server_id": str(bifrost_config.get("server_id") or "").strip(),
                "game_type": str(bifrost_config.get("game_type") or "HLL").strip() or "HLL",
            }
        )
        return status

    status["error"] = f"Unsupported HLL backend provider: {provider}"
    return status