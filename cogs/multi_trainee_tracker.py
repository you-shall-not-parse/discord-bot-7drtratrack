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
from discord.ext import commands

from data_paths import data_path

logger = logging.getLogger(__name__)

# =============================
# CONFIG (EDIT THIS)
# =============================
GUILD_ID = 1097913605082579024

# All HTML table uploads go to this single channel (shared across tracks).
# Embeds still post in each track's configured channel.
HTML_CHANNEL_ID = 1098525492631572567

# Trainees are considered "Behind" once they've been in the server longer than this.
BEHIND_AFTER_DAYS = 14

# Backstop refresh so "Behind" updates even if nobody's roles change.
# Set to 0 to disable.
BACKSTOP_REFRESH_HOURS = 24

# Where we store message IDs + last HTML link so we can edit across restarts
STATE_PATH = data_path("multi_trainee_tracker_state.json")

# Optional: emojis to show next to names in the embed when a trainee has a role.
# You can use standard emojis ("🛠️"), custom server emoji strings ("<:name:id>"),
# or short-name tags (":support:") which will be resolved to a real server emoji if it exists.
ROLE_EMOJIS: dict[str, str] = {
    # Infantry
    "Support Role": ":Support:",
    "Engineer Role": ":engineer:",
    # Recon
    "Spotter Role": ":Spotter:",
    "Sniper Role": ":sniper:",
    # Armour
    "BAC Role": "🛡️",
    "Driver Role": "🚗",
    "Gunner Role": "💥",
}


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
        channel_id=1368543744193990676,
        trainee_role_id=1099596178141757542,
        check_roles=[
            ("Support Role", 1100005693546844242),
            ("Engineer Role", 1100005700106719312),
        ],
    ),
    TrackConfig(
        key="recon",
        title="Recon Trainee Tracker",
        channel_id=1391119515609333880,
        trainee_role_id=1103626508645453975,
        check_roles=[
            ("Spotter Role", 1102199425654333522),
            ("Sniper Role", 1102199204887138324),
        ],
    ),
    TrackConfig(
        key="armour",
        title="Armour Trainee Tracker",
        channel_id=1391119412047777974,
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
        self._backstop_task: Optional[asyncio.Task] = None
        self._state = self._load_state()
        if BACKSTOP_REFRESH_HOURS and BACKSTOP_REFRESH_HOURS > 0:
            self._backstop_task = asyncio.create_task(self._backstop_refresh_loop())

    def cog_unload(self):
        if self._backstop_task and not self._backstop_task.done():
            self._backstop_task.cancel()
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
            os.makedirs(os.path.dirname(STATE_PATH) or ".", exist_ok=True)
            tmp_path = f"{STATE_PATH}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, STATE_PATH)
        except Exception:
            logger.warning("Failed to save multi trainee tracker state.", exc_info=True)

    def _track_state(self, key: str) -> dict:
        tracks = self._state.setdefault("tracks", {})
        state = tracks.setdefault(key, {})
        state.setdefault("embed_message_id", None)
        state.setdefault("embed_channel_id", None)
        state.setdefault("html_message_id", None)
        state.setdefault("html_channel_id", None)
        state.setdefault("html_url", None)
        return state

    async def _get_text_channel(self, channel_id: int) -> Optional[discord.TextChannel]:
        ch = self.bot.get_channel(channel_id)
        if isinstance(ch, discord.TextChannel):
            return ch
        try:
            fetched = await self.bot.fetch_channel(channel_id)
            return fetched if isinstance(fetched, discord.TextChannel) else None
        except Exception:
            return None

    async def _backstop_refresh_loop(self) -> None:
        await self.bot.wait_until_ready()
        # Initial refresh on startup
        await self._refresh_all(reason="startup")
        # Periodic refresh for time-based "Behind" changes
        while True:
            await asyncio.sleep(float(BACKSTOP_REFRESH_HOURS) * 3600.0)
            await self._refresh_all(reason="backstop")

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
        # Refresh only when relevant roles change (trainee role or tracked roles)
        before_ids = {r.id for r in before.roles}
        after_ids = {r.id for r in after.roles}
        changed = before_ids ^ after_ids
        if not changed:
            return

        watched: set[int] = set()
        for cfg in TRACKS:
            watched.add(cfg.trainee_role_id)
            watched.update(role_id for _, role_id in cfg.check_roles)

        if changed & watched:
            self._debounced_refresh()

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        # If someone leaves while being tracked, the list should update.
        if any(any(r.id == cfg.trainee_role_id for r in member.roles) for cfg in TRACKS):
            self._debounced_refresh()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        # join date impacts the lists, but they'll typically get a role later.
        # We can skip refreshing here to reduce noise.
        return

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
        embed_channel = await self._get_text_channel(cfg.channel_id)
        if not embed_channel:
            logger.warning("Track %s: channel not found or not a text channel", cfg.key)
            return

        html_channel = await self._get_text_channel(HTML_CHANNEL_ID) if HTML_CHANNEL_ID else None
        if HTML_CHANNEL_ID and not html_channel:
            logger.warning("Track %s: HTML channel not found or not a text channel", cfg.key)
            html_channel = None

        rows = self._collect_rows(guild, cfg)
        html_url = await self._post_html(html_channel, cfg, rows) if html_channel else None
        await self._post_embed(embed_channel, cfg, rows, html_url, reason=reason)

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
                old_channel = channel
                old_channel_id = state.get("html_channel_id")
                if isinstance(old_channel_id, int) and old_channel_id != channel.id:
                    fetched_old = await self._get_text_channel(old_channel_id)
                    if fetched_old:
                        old_channel = fetched_old

                old_msg = await old_channel.fetch_message(old_html_message_id)
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
        state["html_channel_id"] = channel.id
        state["html_url"] = msg.attachments[0].url if msg.attachments else None
        return state["html_url"]

    async def _post_embed(self, channel: discord.TextChannel, cfg: TrackConfig, rows: list[dict], html_url: Optional[str], *, reason: str) -> None:
        state = self._track_state(cfg.key)

        def _resolve_custom_emoji_tag(emoji_tag: str) -> str:
            """Resolve ':name:' to '<:name:id>' using this channel's guild emojis when possible."""

            if not emoji_tag:
                return emoji_tag
            # Already a custom emoji markup or something else (unicode)
            if emoji_tag.startswith("<") and emoji_tag.endswith(">"):
                return emoji_tag
            if not (emoji_tag.startswith(":") and emoji_tag.endswith(":") and len(emoji_tag) > 2):
                return emoji_tag

            guild = getattr(channel, "guild", None)
            if not guild:
                return emoji_tag

            emoji_name = emoji_tag.strip(":")
            for e in getattr(guild, "emojis", []):
                if getattr(e, "name", None) == emoji_name:
                    return str(e)
            # Not found: return original (will display as text)
            return emoji_tag

        now = datetime.utcnow().replace(tzinfo=None)
        behind_cutoff = now - timedelta(days=BEHIND_AFTER_DAYS)

        behind_rows = [r for r in rows if r["join_date"].replace(tzinfo=None) < behind_cutoff]
        current_rows = [r for r in rows if r["join_date"].replace(tzinfo=None) >= behind_cutoff]

        embed = discord.Embed(
            title=cfg.title,
            color=discord.Color.blurple(),
            timestamp=datetime.utcnow(),
            url=html_url if html_url else discord.Embed.Empty,
        )

        def fmt_user(r: dict) -> str:
            emojis: list[str] = []
            checks = r.get("checks") or {}
            for label, _role_id in cfg.check_roles:
                if checks.get(label):
                    tag = ROLE_EMOJIS.get(label, "✅")
                    emojis.append(_resolve_custom_emoji_tag(tag))
            suffix = (" " + "".join(emojis)) if emojis else ""
            return f"{r['display_name']}{suffix}"

        def chunk_lines(items: list[dict], *, max_chars: int = 1024) -> list[str]:
            """Split users into multiple field values (each <= 1024 chars)."""
            if not items:
                return ["None"]

            chunks: list[str] = []
            buf: list[str] = []
            used = 0

            for r in items:
                line = fmt_user(r)
                extra = len(line) + (1 if buf else 0)  # newline if not first

                if buf and used + extra > max_chars:
                    chunks.append("\n".join(buf))
                    buf = [line]
                    used = len(line)
                    continue

                if not buf and len(line) > max_chars:
                    # Extremely long single line; hard cut (shouldn't happen in practice)
                    chunks.append(line[: max_chars - 1] + "…")
                    buf = []
                    used = 0
                    continue

                buf.append(line)
                used += extra

            if buf:
                chunks.append("\n".join(buf))

            return chunks

        def add_section_fields(title: str, items: list[dict]) -> None:
            # Discord limits embeds to 25 fields total.
            values = chunk_lines(items)
            for i, value in enumerate(values):
                if len(embed.fields) >= 25:
                    break
                embed.add_field(name=title if i == 0 else "\u200b", value=value, inline=False)

        embed.description = (
            f"**Total:** {len(rows)}\n"
            f"**Behind (> {BEHIND_AFTER_DAYS} days):** {len(behind_rows)}\n"
            f"**Current (≤ {BEHIND_AFTER_DAYS} days):** {len(current_rows)}"
        )
        if html_url:
            embed.description += f"\n\n[Open full table (HTML)]({html_url})"

        add_section_fields(f"Behind (> {BEHIND_AFTER_DAYS} days)", behind_rows)
        add_section_fields(f"Current (≤ {BEHIND_AFTER_DAYS} days)", current_rows)

        embed.set_footer(text=f"Updated ({reason})")

        existing_id = state.get("embed_message_id")
        existing_channel_id = state.get("embed_channel_id")
        if isinstance(existing_channel_id, int) and existing_channel_id != channel.id:
            existing_id = None

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
        state["embed_channel_id"] = channel.id

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
