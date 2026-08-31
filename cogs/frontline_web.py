from __future__ import annotations

import asyncio
import hmac
import html
import io
import logging
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from aiohttp import web
from openpyxl import Workbook

from config import MAIN_GUILD_ID
from rank_order import DEFAULT_RANK_ORDER

logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "liberationapp" / "frontend"
CLAN_EMBLEM_PATH = Path(__file__).resolve().parent.parent / "data" / "emblem_7dr.png"
WEB_HOST = os.getenv("FRONTLINE_WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.getenv("FRONTLINE_WEB_PORT", "7020"))
SESSION_COOKIE = "hll_frontline_session"
SESSION_SECONDS = 7 * 24 * 60 * 60
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES = 5


class FrontlineWeb:
    """Small aiohttp site that presents live data owned by the Discord cogs."""

    def __init__(self, bot):
        self.bot = bot
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._app_pin = os.getenv("APPPIN", "")
        self._sessions: dict[str, int] = {}
        self._login_failures: dict[str, list[float]] = {}

    async def start(self) -> None:
        if len(self._app_pin) < 8:
            raise RuntimeError("APPPIN must be set to at least 8 characters in the environment or .env file")

        app = web.Application(middlewares=[self._security_headers, self._pin_auth], client_max_size=64 * 1024)
        app.router.add_get("/api/health", self.health)
        app.router.add_get("/login", self.login_page)
        app.router.add_post("/login", self.login)
        app.router.add_post("/logout", self.logout)
        app.router.add_get("/api/dashboard", self.dashboard)
        app.router.add_get("/assets/{filename}", self.asset)
        app.router.add_get("/exports/rollcalls/{key}.html", self.rollcall_html_export)
        app.router.add_get("/exports/rollcalls/{key}.xlsx", self.rollcall_excel_export)
        app.router.add_get("/exports/trainees/{key}.html", self.trainee_html_export)
        app.router.add_get("/exports/trainees/{key}.xlsx", self.trainee_excel_export)
        app.router.add_get("/rollcalls/{key}", self.report_page)
        app.router.add_get("/trainees/{key}", self.report_page)
        app.router.add_get("/", self.index)

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
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; "
            "script-src 'self'; connect-src 'self'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com"
        )
        if request.secure or request.headers.get("X-Forwarded-Proto", "").casefold() == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.path.startswith(("/api/", "/exports/")) or request.path == "/login":
            response.headers["Cache-Control"] = "no-store"
        return response

    @web.middleware
    async def _pin_auth(self, request: web.Request, handler):
        if self._is_sensitive_path(request.raw_path.split("?", 1)[0]):
            return web.Response(text="Not found.", status=404, content_type="text/plain")
        if request.path in {"/login", "/api/health"} or request.path.startswith("/assets/"):
            return await handler(request)
        if self._valid_session(request.cookies.get(SESSION_COOKIE, "")):
            return await handler(request)
        if request.path.startswith("/api/"):
            return web.json_response({"error": "Authentication required", "login_url": "/login"}, status=401)
        next_url = quote(self._safe_next(str(request.rel_url)), safe="")
        return web.HTTPSeeOther(location=f"/login?next={next_url}")

    @staticmethod
    def _is_sensitive_path(value: str) -> bool:
        path = str(value or "")
        # Decode twice to catch common encoded-dot probes without interpreting
        # the path as a filesystem location.
        for _ in range(2):
            decoded = unquote(path)
            if decoded == path:
                break
            path = decoded
        segments = [segment.casefold() for segment in path.replace("\\", "/").split("/") if segment]
        if any(segment.startswith(".") for segment in segments):
            return True
        sensitive_names = {
            "config.php",
            "credentials.json",
            "docker-compose.yml",
            "id_rsa",
            "secrets.json",
            "wp-config.php",
        }
        return any(segment in sensitive_names for segment in segments)

    @staticmethod
    def _safe_next(value: str | None) -> str:
        candidate = str(value or "/")
        return candidate if candidate.startswith("/") and not candidate.startswith("//") else "/"

    def _new_session(self) -> str:
        now = int(time.time())
        self._sessions = {token: expiry for token, expiry in self._sessions.items() if expiry >= now}
        token = secrets.token_urlsafe(32)
        self._sessions[token] = now + SESSION_SECONDS
        return token

    def _valid_session(self, token: str) -> bool:
        expiry = self._sessions.get(token)
        if expiry is None:
            return False
        if expiry < int(time.time()):
            self._sessions.pop(token, None)
            return False
        return True

    @staticmethod
    def _client_key(request: web.Request) -> str:
        forwarded = (
            request.headers.get("CF-Connecting-IP")
            or request.headers.get("X-Real-IP")
            or request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        )
        return forwarded or request.remote or "unknown"

    def _recent_failures(self, request: web.Request) -> list[float]:
        key = self._client_key(request)
        cutoff = time.monotonic() - LOGIN_WINDOW_SECONDS
        recent = [attempt for attempt in self._login_failures.get(key, []) if attempt >= cutoff]
        if recent:
            self._login_failures[key] = recent
        else:
            self._login_failures.pop(key, None)
        return recent

    async def login_page(self, request: web.Request) -> web.Response:
        if self._valid_session(request.cookies.get(SESSION_COOKIE, "")):
            return web.HTTPSeeOther(location=self._safe_next(request.query.get("next")))
        return self._login_document(
            next_url=self._safe_next(request.query.get("next")),
            error=request.query.get("error") == "1",
        )

    async def login(self, request: web.Request) -> web.Response:
        next_url = self._safe_next(request.query.get("next"))
        failures = self._recent_failures(request)
        if len(failures) >= LOGIN_MAX_FAILURES:
            return web.Response(
                text="Too many login attempts. Try again in 15 minutes.",
                status=429,
                content_type="text/plain",
                headers={"Retry-After": str(LOGIN_WINDOW_SECONDS)},
            )

        form = await request.post()
        next_url = self._safe_next(str(form.get("next") or next_url))
        supplied_pin = str(form.get("pin") or "")
        if not hmac.compare_digest(supplied_pin.encode("utf-8"), self._app_pin.encode("utf-8")):
            self._login_failures.setdefault(self._client_key(request), []).append(time.monotonic())
            logger.warning("Rejected HLL Frontline PIN login from %s", self._client_key(request))
            return web.HTTPSeeOther(location=f"/login?error=1&next={quote(next_url, safe='')}")

        self._login_failures.pop(self._client_key(request), None)
        response = web.HTTPSeeOther(location=next_url)
        response.set_cookie(
            SESSION_COOKIE,
            self._new_session(),
            max_age=SESSION_SECONDS,
            httponly=True,
            secure=True,
            samesite="Strict",
            path="/",
        )
        return response

    async def logout(self, request: web.Request) -> web.Response:
        self._sessions.pop(request.cookies.get(SESSION_COOKIE, ""), None)
        response = web.HTTPSeeOther(location="/login")
        response.del_cookie(SESSION_COOKIE, path="/")
        return response

    def _login_document(self, *, next_url: str, error: bool) -> web.Response:
        path = FRONTEND_DIR / "login.html"
        if not path.is_file():
            raise web.HTTPNotFound(text="Login frontend is missing.")
        document = path.read_text(encoding="utf-8")
        document = document.replace("{{NEXT}}", html.escape(next_url, quote=True))
        document = document.replace("{{ERROR}}", "The PIN was not recognised." if error else "")
        return web.Response(text=document, content_type="text/html", charset="utf-8")

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

        workbook_members: dict[int, str] = {}
        for row in range(2, ws.max_row + 1):
            try:
                user_id = int(str(ws.cell(row=row, column=1).value))
            except (TypeError, ValueError):
                continue
            workbook_members[user_id] = str(ws.cell(row=row, column=2).value or "")

        current_people = []
        for member in expected:
            value = status.get(member.id, "")
            current_people.append(
                {
                    "name": member.display_name,
                    "status": self._normalise_rollcall_status(value),
                }
            )

        headers = cog._sheet_headers(ws)
        week_headers = [header for header in headers[2:] if cog._parse_week_header(header)]
        history = []
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

        report_members = []
        report_user_ids = list(workbook_members)
        report_user_ids.extend(user_id for user_id in expected_ids if user_id not in workbook_members)
        week_statuses = {header: cog._get_week_status(ws, header) for header in week_headers}
        for user_id in report_user_ids:
            member = guild.get_member(user_id)
            is_former = cog._is_former_member(member)
            rank, rank_order = self._member_rank(None if is_former else member)
            flags = ["LEFT"] if member is None else (["FORMER"] if is_former else [])
            attendance = {
                header: week_statuses[header].get(user_id, "")
                for header in week_headers
            }
            report_members.append(
                {
                    "name": display_name(user_id, workbook_members.get(user_id, "")),
                    "rank": rank,
                    "rank_order": rank_order,
                    "flags": flags,
                    "active": not is_former,
                    "missed_streak": self._missed_rollcall_streak(attendance, week_headers),
                    "attendance": attendance,
                }
            )

        departed_count = sum(not member["active"] for member in report_members)
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
            "report_columns": week_headers,
            "report_members": report_members,
            "departed_count": departed_count,
        }

    @staticmethod
    def _member_rank(member) -> tuple[str, int]:
        if member is None:
            return "Former member", len(DEFAULT_RANK_ORDER) + 1

        display_name = " ".join(str(getattr(member, "display_name", "") or "").strip().split())
        if "#" in display_name:
            display_name = display_name.split("#", 1)[0].strip()
        display_name_folded = display_name.casefold()

        # Clan ranks are normally prefixes in Discord display names. Prefer the
        # longest matching variant so "Lt Gen" is not mistaken for "Lt".
        display_matches: list[tuple[int, int, str]] = []
        for order, (code, variants) in enumerate(DEFAULT_RANK_ORDER):
            for variant in variants:
                prefix = variant.casefold().strip()
                if (
                    display_name_folded == prefix
                    or display_name_folded.startswith(prefix + " ")
                    or display_name_folded.startswith(prefix + ".")
                ):
                    display_matches.append((len(prefix), order, code))
        if display_matches:
            _length, order, code = max(display_matches, key=lambda match: match[0])
            return code, order

        # Retain role matching as a fallback for members whose nickname omits rank.
        def normalise(value: str) -> str:
            return re.sub(r"[^a-z0-9]", "", str(value).casefold())

        member_roles = {normalise(role.name) for role in getattr(member, "roles", [])}
        for order, (code, variants) in enumerate(DEFAULT_RANK_ORDER):
            accepted = {normalise(code), *(normalise(variant) for variant in variants)}
            if member_roles & accepted:
                return code, order
        return "Unranked", len(DEFAULT_RANK_ORDER)

    @staticmethod
    def _normalise_rollcall_status(value: str) -> str:
        if value == "✅":
            return "attending"
        if value == "🅾️":
            return "other-rollcall"
        return "missing"

    @staticmethod
    def _missed_rollcall_streak(attendance: dict[str, str], week_headers: list[str]) -> int:
        """Count consecutive explicit misses, starting with the newest roll call."""
        streak = 0
        for header in reversed(week_headers):
            if attendance.get(header) != "❌":
                break
            streak += 1
        return streak

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
        if filename == "emblem_7dr.png":
            path = CLAN_EMBLEM_PATH
        elif filename in {"app.css", "app.js", "report.js"}:
            path = FRONTEND_DIR / filename
        else:
            raise web.HTTPNotFound()
        if not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    async def index(self, _request: web.Request) -> web.StreamResponse:
        path = FRONTEND_DIR / "index.html"
        if not path.is_file():
            raise web.HTTPNotFound(text="Frontend assets are missing.")
        return web.FileResponse(path)

    async def report_page(self, request: web.Request) -> web.StreamResponse:
        key = request.match_info["key"]
        if request.path.startswith("/rollcalls/"):
            from cogs.rollcall import ROLLCALLS

            valid_keys = {cfg.key for cfg in ROLLCALLS}
        else:
            from cogs.multi_trainee_tracker import TRACKS

            valid_keys = {cfg.key for cfg in TRACKS}
        if key not in valid_keys:
            raise web.HTTPNotFound()

        path = FRONTEND_DIR / "report.html"
        if not path.is_file():
            raise web.HTTPNotFound(text="Report frontend is missing.")
        return web.FileResponse(path)

    def _require_guild_and_cog(self, cog_name: str):
        guild = self.bot.get_guild(MAIN_GUILD_ID)
        cog = self.bot.get_cog(cog_name)
        if guild is None or cog is None:
            raise web.HTTPServiceUnavailable(text="Report data is not ready yet.")
        return guild, cog

    @staticmethod
    def _rollcall_config(key: str):
        from cogs.rollcall import ROLLCALLS

        config = next((cfg for cfg in ROLLCALLS if cfg.key == key), None)
        if config is None:
            raise web.HTTPNotFound()
        return config

    @staticmethod
    def _trainee_config(key: str):
        from cogs.multi_trainee_tracker import TRACKS

        config = next((cfg for cfg in TRACKS if cfg.key == key), None)
        if config is None:
            raise web.HTTPNotFound()
        return config

    async def rollcall_html_export(self, request: web.Request) -> web.Response:
        guild, cog = self._require_guild_and_cog("RollCallCog")
        config = self._rollcall_config(request.match_info["key"])
        async with cog._lock:
            workbook = await asyncio.to_thread(cog._load_or_create_workbook)
            worksheet = cog._get_or_create_sheet(workbook, config)
            state = cog._rc_state(config.key)
            week = state.get("current_week") or cog._week_label(cog._rollcall_date_for_now())
            document = cog._render_html(guild, worksheet, config, highlight_week=week)
        return web.Response(text=document, content_type="text/html", charset="utf-8")

    async def rollcall_excel_export(self, request: web.Request) -> web.Response:
        _guild, cog = self._require_guild_and_cog("RollCallCog")
        config = self._rollcall_config(request.match_info["key"])
        async with cog._lock:
            workbook = await asyncio.to_thread(cog._load_or_create_workbook)
            cog._get_or_create_sheet(workbook, config)
            output = io.BytesIO()
            await asyncio.to_thread(workbook.save, output)
        return self._excel_response(output.getvalue(), "rollcall.xlsx")

    async def trainee_html_export(self, request: web.Request) -> web.Response:
        guild, cog = self._require_guild_and_cog("MultiTraineeTracker")
        config = self._trainee_config(request.match_info["key"])
        rows = cog._collect_rows(guild, config)
        document = cog._render_html(config, rows)
        return web.Response(text=document, content_type="text/html", charset="utf-8")

    async def trainee_excel_export(self, request: web.Request) -> web.Response:
        guild, cog = self._require_guild_and_cog("MultiTraineeTracker")
        config = self._trainee_config(request.match_info["key"])
        rows = cog._collect_rows(guild, config)

        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = config.title[:31]
        headers = ["Name", "Username", "Join Date", "+14 Days"] + [label for label, _ in config.check_roles]
        worksheet.append(headers)
        for row in rows:
            worksheet.append(
                [
                    row["display_name"],
                    row["username"],
                    row["join_date"].date(),
                    row["plus_14"].date(),
                    *("Yes" if row["checks"].get(label) else "No" for label, _ in config.check_roles),
                ]
            )
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions
        for column in worksheet.columns:
            letter = column[0].column_letter
            worksheet.column_dimensions[letter].width = min(
                42,
                max(12, max(len(str(cell.value or "")) for cell in column) + 2),
            )

        output = io.BytesIO()
        await asyncio.to_thread(workbook.save, output)
        return self._excel_response(output.getvalue(), f"{config.key}_trainee_tracker.xlsx")

    @staticmethod
    def _excel_response(content: bytes, filename: str) -> web.Response:
        return web.Response(
            body=content,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


async def setup(bot) -> None:
    service = FrontlineWeb(bot)
    await service.start()
    bot.frontline_web = service


async def teardown(bot) -> None:
    service = getattr(bot, "frontline_web", None)
    if service:
        await service.stop()
        del bot.frontline_web
