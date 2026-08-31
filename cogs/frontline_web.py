from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aiohttp import web

from config import MAIN_GUILD_ID

logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "liberationapp" / "frontend"
WEB_HOST = os.getenv("FRONTLINE_WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.getenv("FRONTLINE_WEB_PORT", "7020"))


class FrontlineWeb:
    """Small aiohttp site that presents live data owned by the Discord cogs."""

    def __init__(self, bot):
        self.bot = bot
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    async def start(self) -> None:
        app = web.Application(middlewares=[self._security_headers])
        app.router.add_get("/api/health", self.health)
        app.router.add_get("/api/dashboard", self.dashboard)
        app.router.add_get("/assets/{filename}", self.asset)
        app.router.add_get("/{tail:.*}", self.index)

        self._runner = web.AppRunner(app, access_log=logger)
        await self._runner.setup()
        try:
            self._site = web.TCPSite(self._runner, WEB_HOST, WEB_PORT)
            await self._site.start()
        except Exception:
            await self._runner.cleanup()
            self._runner = None
            self._site = None
            raise
        logger.info("HLL Frontline web app listening on http://%s:%s", WEB_HOST, WEB_PORT)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            self._site = None

    @web.middleware
    async def _security_headers(self, request: web.Request, handler):
        response = await handler(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    async def health(self, _request: web.Request) -> web.Response:
        guild = self.bot.get_guild(MAIN_GUILD_ID)
        return web.json_response(
            {
                "status": "ok" if guild else "starting",
                "discord_ready": bool(self.bot.is_ready()),
                "guild_available": guild is not None,
            },
            status=200 if guild else 503,
        )

    async def dashboard(self, _request: web.Request) -> web.Response:
        guild = self.bot.get_guild(MAIN_GUILD_ID)
        if guild is None:
            raise web.HTTPServiceUnavailable(
                text='{"error":"Discord data is not ready yet"}',
                content_type="application/json",
            )

        rollcall = self.bot.get_cog("RollCallCog")
        trainee = self.bot.get_cog("MultiTraineeTracker")
        if rollcall is None or trainee is None:
            raise web.HTTPServiceUnavailable(
                text='{"error":"Dashboard data providers are unavailable"}',
                content_type="application/json",
            )

        # The roll-call cog serialises workbook mutations with this lock. Reading under
        # the same lock prevents the API seeing a half-written weekly refresh.
        async with rollcall._lock:
            rollcalls = await asyncio.to_thread(self._rollcall_payload, rollcall, guild)
        trainees = self._trainee_payload(trainee, guild)

        return web.json_response(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "guild": {"name": guild.name},
                "rollcalls": rollcalls,
                "trainee_tracks": trainees,
            }
        )

    def _rollcall_payload(self, cog, guild) -> list[dict[str, Any]]:
        workbook = cog._load_or_create_workbook()
        result: list[dict[str, Any]] = []

        for cfg in cog.ROLLCALLS if hasattr(cog, "ROLLCALLS") else ():
            # Kept for compatibility if configuration is moved onto the cog later.
            result.append(self._one_rollcall(cog, guild, workbook, cfg))

        if result:
            return result

        # Current cogs keep configuration at module scope.
        from cogs.rollcall import ROLLCALLS

        return [self._one_rollcall(cog, guild, workbook, cfg) for cfg in ROLLCALLS]

    def _one_rollcall(self, cog, guild, workbook, cfg) -> dict[str, Any]:
        ws = cog._get_or_create_sheet(workbook, cfg)
        state = cog._rc_state(cfg.key)
        week = state.get("current_week") or cog._week_label(cog._rollcall_date_for_now())
        status = cog._get_week_status(ws, week)
        expected = cog._expected_members(guild, cfg)
        expected_ids = {member.id for member in expected}

        def display_name(user_id: int, stored: str = "") -> str:
            member = guild.get_member(user_id)
            return member.display_name if member else stored or str(user_id)

        row_names: dict[int, str] = {}
        for row in range(2, ws.max_row + 1):
            try:
                user_id = int(str(ws.cell(row=row, column=1).value))
            except (TypeError, ValueError):
                continue
            row_names[user_id] = str(ws.cell(row=row, column=2).value or "")

        current_people = []
        for member in expected:
            value = status.get(member.id, "")
            current_people.append(
                {
                    "name": member.display_name,
                    "status": self._normalise_rollcall_status(value),
                }
            )

        history = []
        headers = cog._sheet_headers(ws)
        week_headers = [header for header in headers[2:] if cog._parse_week_header(header)]
        for header in reversed(week_headers[-12:]):
            values = cog._get_week_status(ws, header)
            active_values = [values.get(user_id, "") for user_id in expected_ids]
            history.append(
                {
                    "week": header,
                    "attending": sum(value == "✅" for value in active_values),
                    "partial": sum(value == "🅾️" for value in active_values),
                    "missing": sum(value not in ("✅", "🅾️") for value in active_values),
                }
            )

        left = [
            {"name": display_name(user_id, name), "status": "left"}
            for user_id, name in row_names.items()
            if user_id not in expected_ids and guild.get_member(user_id) is None
        ]
        attending = sum(person["status"] == "attending" for person in current_people)

        return {
            "key": cfg.key,
            "title": cfg.title,
            "week": week,
            "locked": cog._is_rollcall_locked(state),
            "summary": {
                "total": len(current_people),
                "attending": attending,
                "missing": len(current_people) - attending,
                "rate": round((attending / len(current_people)) * 100) if current_people else 0,
            },
            "members": current_people,
            "history": history,
            "departed_count": len(left),
        }

    @staticmethod
    def _normalise_rollcall_status(value: str) -> str:
        if value == "✅":
            return "attending"
        if value == "🅾️":
            return "other-rollcall"
        return "missing"

    def _trainee_payload(self, cog, guild) -> list[dict[str, Any]]:
        from cogs.multi_trainee_tracker import BEHIND_AFTER_DAYS, TRACKS

        now = datetime.now(timezone.utc)
        result = []
        for cfg in TRACKS:
            rows = cog._collect_rows(guild, cfg)
            members = []
            for row in rows:
                joined = row["join_date"]
                if joined.tzinfo is None:
                    joined = joined.replace(tzinfo=timezone.utc)
                days = max(0, (now - joined).days)
                behind = joined < now - timedelta(days=BEHIND_AFTER_DAYS)
                members.append(
                    {
                        "name": row["display_name"],
                        "username": row["username"],
                        "joined": joined.date().isoformat(),
                        "review_due": row["plus_14"].date().isoformat(),
                        "days": days,
                        "behind": behind,
                        "checks": row["checks"],
                    }
                )
            result.append(
                {
                    "key": cfg.key,
                    "title": cfg.title,
                    "behind_after_days": BEHIND_AFTER_DAYS,
                    "summary": {
                        "total": len(members),
                        "behind": sum(member["behind"] for member in members),
                        "current": sum(not member["behind"] for member in members),
                    },
                    "check_labels": [label for label, _ in cfg.check_roles],
                    "members": members,
                }
            )
        return result

    async def asset(self, request: web.Request) -> web.StreamResponse:
        filename = request.match_info["filename"]
        if filename not in {"app.css", "app.js"}:
            raise web.HTTPNotFound()
        path = FRONTEND_DIR / filename
        if not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    async def index(self, _request: web.Request) -> web.StreamResponse:
        path = FRONTEND_DIR / "index.html"
        if not path.is_file():
            raise web.HTTPNotFound(text="Frontend assets are missing.")
        return web.FileResponse(path)


async def setup(bot) -> None:
    service = FrontlineWeb(bot)
    await service.start()
    bot.frontline_web = service


async def teardown(bot) -> None:
    service = getattr(bot, "frontline_web", None)
    if service:
        await service.stop()
        del bot.frontline_web
