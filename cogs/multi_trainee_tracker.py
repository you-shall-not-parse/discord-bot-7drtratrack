import asyncio
import html
import io
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord.ext import commands, tasks

from data_paths import data_path

logger = logging.getLogger(__name__)

# =============================
# CONFIG (EDIT THIS)
# =============================
GUILD_ID = 1097913605082579024

# How often to refresh the tables (minutes)
REFRESH_INTERVAL_MINUTES = 15

# Where we store message IDs + last HTML link so we can edit across restarts
STATE_PATH = data_path("multi_trainee_tracker_state.json")


@dataclass(frozen=True)
class TrackConfig:
    key: str
    title: str
    channel_id: int
    trainee_role_id: int
    check_roles: list[tuple[str, int]]  # (label, role_id)


TRACKS: list[TrackConfig] = [
    TrackConfig(
        key="infantry",
        title="Infantry Trainee Tracker",
        channel_id=1099806153170489485,
        trainee_role_id=1099596178141757542,
        check_roles=[
            ("Support Role", 1100005693546844242),
            ("Engineer Role", 1100005700106719312),
        ],
    ),
    TrackConfig(
        key="recon",
        title="Recon Trainee Tracker",
        channel_id=1099806153170489485,
        trainee_role_id=1103626508645453975,
        check_roles=[
            ("Spotter Role", 1102199425654333522),
            ("Sniper Role", 1102199204887138324),
        ],
    ),
    TrackConfig(
        key="armour",
        title="Armour Trainee Tracker",
        channel_id=1099806153170489485,
        trainee_role_id=1099615408518070313,
        check_roles=[
            ("BAC Role", 1182154521129009202),
            ("Driver Role", 1108427017998827521),
            ("Gunner Role", 1108426942610407494),
        ],
    ),
]


class MultiTraineeTracker(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._lock = asyncio.Lock()
        self._debounce_task: Optional[asyncio.Task] = None
        self._state = self._load_state()
        self.refresh_all.start()

    def cog_unload(self):
        self.refresh_all.cancel()
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()

    # -----------------
    # State
    # -----------------
    def _load_state(self) -> dict:
        try:
            if not os.path.exists(STATE_PATH):
                return {"version": 1, "tracks": {}}
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {"version": 1, "tracks": {}}
            data.setdefault("version", 1)
            data.setdefault("tracks", {})
            return data
        except Exception:
            logger.warning("Failed to load multi trainee tracker state; starting fresh.", exc_info=True)
            return {"version": 1, "tracks": {}}

    def _save_state(self) -> None:
        try:
            self._state["updated_at"] = datetime.utcnow().isoformat()
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2, ensure_ascii=False)
        except Exception:
            logger.warning("Failed to save multi trainee tracker state.", exc_info=True)

    def _track_state(self, key: str) -> dict:
        tracks = self._state.setdefault("tracks", {})
        state = tracks.setdefault(key, {})
        state.setdefault("embed_message_id", None)
        state.setdefault("html_message_id", None)
        state.setdefault("html_url", None)
        return state

    # -----------------
    # Refresh loop + debounce
    # -----------------
    @tasks.loop(minutes=REFRESH_INTERVAL_MINUTES)
    async def refresh_all(self):
        await self._refresh_all(reason="interval")

    @refresh_all.before_loop
    async def _before_refresh_all(self):
        await self.bot.wait_until_ready()
        await self._refresh_all(reason="startup")

    def _debounced_refresh(self, delay_seconds: float = 3.0) -> None:
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
        self._debounce_task = asyncio.create_task(self._debounce_worker(delay_seconds))

    async def _debounce_worker(self, delay_seconds: float) -> None:
        try:
            await asyncio.sleep(delay_seconds)
            await self._refresh_all(reason="member_update")
        except asyncio.CancelledError:
            return

    # -----------------
    # Event listeners
    # -----------------
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        # Any role change could affect any of the tables.
        if before.roles != after.roles:
            self._debounced_refresh()

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        self._debounced_refresh()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        # join date impacts the tables
        self._debounced_refresh()

    # -----------------
    # Core logic
    # -----------------
    async def _refresh_all(self, *, reason: str) -> None:
        async with self._lock:
            guild = self.bot.get_guild(GUILD_ID)
            if not guild:
                logger.warning("MultiTraineeTracker: guild not found")
                return

            for cfg in TRACKS:
                try:
                    await self._refresh_track(guild, cfg, reason=reason)
                except Exception:
                    logger.exception("Failed refreshing track %s", cfg.key)

            self._save_state()

    async def _refresh_track(self, guild: discord.Guild, cfg: TrackConfig, *, reason: str) -> None:
        channel = self.bot.get_channel(cfg.channel_id)
        if not isinstance(channel, discord.TextChannel):
            logger.warning("Track %s: channel not found or not a text channel", cfg.key)
            return

        rows = self._collect_rows(guild, cfg)
        html_url = await self._post_html(channel, cfg, rows)
        await self._post_embed(channel, cfg, rows, html_url, reason=reason)

    def _collect_rows(self, guild: discord.Guild, cfg: TrackConfig) -> list[dict]:
        now = datetime.utcnow()
        rows: list[dict] = []

        for member in guild.members:
            if not any(r.id == cfg.trainee_role_id for r in member.roles):
                continue

            join_date = member.joined_at or now
            plus_14 = join_date + timedelta(days=14)

            checks = {}
            for label, role_id in cfg.check_roles:
                checks[label] = any(r.id == role_id for r in member.roles)

            rows.append(
                {
                    "member_id": member.id,
                    "display_name": member.display_name,
                    "username": member.name,
                    "join_date": join_date,
                    "plus_14": plus_14,
                    "checks": checks,
                }
            )

        rows.sort(key=lambda r: r["join_date"])
        return rows

    async def _post_html(self, channel: discord.TextChannel, cfg: TrackConfig, rows: list[dict]) -> Optional[str]:
        state = self._track_state(cfg.key)

        # Delete previous HTML message to keep the channel clean (attachments can't be edited reliably).
        old_html_message_id = state.get("html_message_id")
        if isinstance(old_html_message_id, int):
            try:
                old_msg = await channel.fetch_message(old_html_message_id)
                await old_msg.delete()
            except discord.NotFound:
                pass
            except discord.Forbidden:
                # Not fatal; we'll still post a new one.
                logger.warning("Track %s: missing permission to delete old HTML message", cfg.key)
            except Exception:
                logger.warning("Track %s: failed deleting old HTML message", cfg.key, exc_info=True)

        html_text = self._render_html(cfg, rows)
        file_bytes = html_text.encode("utf-8")
        file = discord.File(fp=io.BytesIO(file_bytes), filename=f"{cfg.key}_trainees.html")

        msg = await channel.send(content=f"{cfg.title} (HTML table)", file=file)
        state["html_message_id"] = msg.id
        state["html_url"] = msg.attachments[0].url if msg.attachments else None
        return state["html_url"]

    async def _post_embed(self, channel: discord.TextChannel, cfg: TrackConfig, rows: list[dict], html_url: Optional[str], *, reason: str) -> None:
        state = self._track_state(cfg.key)

        embed = discord.Embed(
            title=cfg.title,
            color=discord.Color.blurple(),
            timestamp=datetime.utcnow(),
            url=html_url if html_url else discord.Embed.Empty,
        )

        trainee_role = channel.guild.get_role(cfg.trainee_role_id)
        embed.description = (
            (f"[Open full table (HTML)]({html_url})\n\n" if html_url else "")
            + f"**Trainee Role:** {trainee_role.mention if trainee_role else cfg.trainee_role_id}\n"
            + f"**Count:** {len(rows)}"
        )

        # Show which roles are tracked
        role_lines = []
        for label, role_id in cfg.check_roles:
            role = channel.guild.get_role(role_id)
            role_lines.append(f"- {label}: {role.mention if role else role_id}")
        if role_lines:
            embed.add_field(name="Tracked Roles", value="\n".join(role_lines), inline=False)

        embed.set_footer(text=f"Updated ({reason})")

        existing_id = state.get("embed_message_id")
        if isinstance(existing_id, int):
            try:
                msg = await channel.fetch_message(existing_id)
                await msg.edit(embed=embed)
                return
            except discord.NotFound:
                pass
            except discord.Forbidden:
                logger.warning("Track %s: missing permission to edit embed message", cfg.key)
            except Exception:
                logger.warning("Track %s: failed to edit embed message", cfg.key, exc_info=True)

        msg = await channel.send(embed=embed)
        state["embed_message_id"] = msg.id

    def _render_html(self, cfg: TrackConfig, rows: list[dict]) -> str:
        headers = [
            "Name",
            "Username",
            "Join Date",
            "+14 Days",
        ] + [label for (label, _) in cfg.check_roles]

        head_html = "".join(f"<th>{html.escape(h)}</th>" for h in headers)

        body_rows = []
        for r in rows:
            join_date = r["join_date"].strftime("%d/%m/%Y")
            plus_14 = r["plus_14"].strftime("%d/%m/%Y")
            user_link = f"https://discord.com/users/{r['member_id']}"

            cols = [
                f"<a href=\"{user_link}\">{html.escape(r['display_name'])}</a>",
                html.escape(r["username"]),
                join_date,
                plus_14,
            ]

            for label, _ in cfg.check_roles:
                cols.append("✅" if r["checks"].get(label) else "❌")

            body_rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in cols) + "</tr>")

        table_html = "".join(body_rows) if body_rows else "<tr><td colspan=\"100\">No trainees found.</td></tr>"

        return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
  <title>{html.escape(cfg.title)}</title>
  <style>
    body {{ font-family: Arial, sans-serif; padding: 16px; }}
    h1 {{ margin: 0 0 12px 0; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
    th {{ background: #f5f5f5; position: sticky; top: 0; }}
    tr:nth-child(even) {{ background: #fafafa; }}
    a {{ color: #5865F2; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <h1>{html.escape(cfg.title)}</h1>
  <p>Last updated: {datetime.utcnow().strftime('%d/%m/%Y %H:%M UTC')}</p>
  <table>
    <thead><tr>{head_html}</tr></thead>
    <tbody>{table_html}</tbody>
  </table>
</body>
</html>"""


async def setup(bot: commands.Bot):
    await bot.add_cog(MultiTraineeTracker(bot))
    logger.info("MultiTraineeTracker loaded")
