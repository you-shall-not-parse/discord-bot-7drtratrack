import html
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import discord
from discord.ext import commands

from config import MAIN_GUILD_ID

logger = logging.getLogger(__name__)

# =============================
# CONFIG (EDIT THIS)
# =============================
GUILD_ID = MAIN_GUILD_ID

# Trainees are considered "Behind" once they've been in the server longer than this.
BEHIND_AFTER_DAYS = 14

@dataclass(frozen=True)
class TrackConfig:
    key: str
    title: str
    trainee_role_id: int
    check_roles: list[tuple[str, int]]  # (label, role_id)


TRACKS: list[TrackConfig] = [
    TrackConfig(
        key="infantry",
        title="Infantry Trainee Tracker",
        trainee_role_id=1099596178141757542,
        check_roles=[
            ("Support Role", 1100005693546844242),
            ("Engineer Role", 1100005700106719312),
        ],
    ),
    TrackConfig(
        key="recon",
        title="Recon Trainee Tracker",
        trainee_role_id=1103626508645453975,
        check_roles=[
            ("Spotter Role", 1102199425654333522),
            ("Sniper Role", 1102199204887138324),
        ],
    ),
    TrackConfig(
        key="armour",
        title="Armour Trainee Tracker",
        trainee_role_id=1099615408518070313,
        check_roles=[
            ("BAC Role", 1182154521129009202),
            ("Driver Role", 1108427017998827521),
            ("Gunner Role", 1108426942610407494),
        ],
    ),
]


class MultiTraineeTracker(commands.Cog):
    """Website data provider; trainee progress comes from current Discord roles."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

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
