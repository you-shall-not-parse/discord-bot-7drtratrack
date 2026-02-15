import asyncio
import html
import io
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional

import discord
from discord import app_commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from discord.ext import commands
from openpyxl import Workbook, load_workbook

from data_paths import data_path

logger = logging.getLogger(__name__)


# =============================
# CONFIG (EDIT THIS)
# =============================

# Target guild
GUILD_ID = 1097913605082579024

# When to send the rollcall each week.
# NOTE: Columns are based on the date the rollcall is sent.
TIMEZONE = "Europe/London"  # uses pytz tz database name
SCHEDULE_WEEKDAY = "mon"  # mon/tue/wed/thu/fri/sat/sun
SCHEDULE_HOUR = 7  # 24h format, local time
SCHEDULE_MINUTE = 0

# Emoji members should react with to mark attendance.
ROLLCALL_EMOJI = "✅"

# Where to store the attendance workbook.
# The bot writes to .xlsx. If you have a legacy .xls, see IMPORT_LEGACY_XLS_PATH.
WORKBOOK_PATH = data_path("rollcall.xlsx")

# Optional: path to a legacy .xls workbook to import once (only if WORKBOOK_PATH doesn't exist yet).
# Requires pandas + xlrd==1.2.0 installed.
IMPORT_LEGACY_XLS_PATH: Optional[str] = None

# Optional: post HTML table uploads to a single channel.
# If None, HTML is posted to each rollcall channel.
HTML_CHANNEL_ID: Optional[int] = None

# Backstop refresh for embeds/html in case of missed reaction events
BACKSTOP_REFRESH_MINUTES = 30

# State file: message IDs + last posted week so we can edit across restarts
STATE_PATH = data_path("rollcall_state.json")

# Role allowed to use /forcerollcall
FORCE_ROLLCALL_ROLE_ID = 1213495462632361994


@dataclass(frozen=True)
class RollCallConfig:
	key: str
	title: str
	channel_id: int
	tracked_role_id: Optional[int] = None  # if set, only these members are expected to respond
	ping_role_id: Optional[int] = None  # optional role mention in the post


# Configure one entry per rollcall channel.
ROLLCALLS: list[RollCallConfig] = [
	 RollCallConfig(
	     key="admin",
	     title="Admin Weekly Roll Call",
	     channel_id=1099806153170489485,
	     tracked_role_id=1213495462632361994,
	     ping_role_id=1213495462632361994,
	 ),
]


class RollCallCog(commands.Cog):
	def __init__(self, bot: commands.Bot):
		self.bot = bot
		self._lock = asyncio.Lock()
		self._state = self._load_state()
		self._scheduler: Optional[AsyncIOScheduler] = None
		self._backstop_task: Optional[asyncio.Task] = None
		self._refresh_task: Optional[asyncio.Task] = None
		self._debounce_task: Optional[asyncio.Task] = None

		self._start_scheduler()
		if BACKSTOP_REFRESH_MINUTES and BACKSTOP_REFRESH_MINUTES > 0:
			self._backstop_task = asyncio.create_task(self._backstop_loop())

	def _user_can_force(self, interaction: discord.Interaction) -> bool:
		user = interaction.user
		if isinstance(user, discord.Member):
			return any(r.id == FORCE_ROLLCALL_ROLE_ID for r in user.roles)
		return False

	@app_commands.command(name="forcerollcall", description="Force-run all configured roll calls now.")
	@app_commands.guilds(discord.Object(id=GUILD_ID))
	@app_commands.guild_only()
	async def forcerollcall(self, interaction: discord.Interaction) -> None:
		if interaction.guild_id != GUILD_ID:
			await interaction.response.send_message("This command can only be used in the main guild.", ephemeral=True)
			return
		if not self._user_can_force(interaction):
			await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
			return

		await interaction.response.defer(ephemeral=True, thinking=True)
		try:
			await self._send_rollcalls(reason="forced_command")
			week_label = self._week_label(self._rollcall_date_for_now())
			await interaction.followup.send(f"Forced roll call run complete for **{week_label}**.", ephemeral=True)
		except Exception:
			logger.exception("/forcerollcall failed")
			await interaction.followup.send("Force roll call failed; check logs.", ephemeral=True)

	def cog_unload(self):
		if self._scheduler:
			try:
				self._scheduler.shutdown(wait=False)
			except Exception:
				pass
		if self._backstop_task and not self._backstop_task.done():
			self._backstop_task.cancel()
		if self._refresh_task and not self._refresh_task.done():
			self._refresh_task.cancel()
		if self._debounce_task and not self._debounce_task.done():
			self._debounce_task.cancel()

	# -----------------
	# State
	# -----------------
	def _load_state(self) -> dict:
		try:
			if not os.path.exists(STATE_PATH):
				return {"version": 1, "rollcalls": {}}
			with open(STATE_PATH, "r", encoding="utf-8") as f:
				data = json.load(f)
			if not isinstance(data, dict):
				return {"version": 1, "rollcalls": {}}
			data.setdefault("version", 1)
			data.setdefault("rollcalls", {})
			return data
		except Exception:
			logger.warning("Failed to load rollcall state; starting fresh.", exc_info=True)
			return {"version": 1, "rollcalls": {}}

	def _save_state(self) -> None:
		try:
			self._state["updated_at"] = datetime.utcnow().isoformat()
			os.makedirs(os.path.dirname(STATE_PATH) or ".", exist_ok=True)
			tmp_path = f"{STATE_PATH}.tmp"
			with open(tmp_path, "w", encoding="utf-8") as f:
				json.dump(self._state, f, indent=2, ensure_ascii=False)
			os.replace(tmp_path, STATE_PATH)
		except Exception:
			logger.warning("Failed to save rollcall state.", exc_info=True)

	def _rc_state(self, key: str) -> dict:
		rc = self._state.setdefault("rollcalls", {}).setdefault(key, {})
		rc.setdefault("current_week", None)
		rc.setdefault("rollcall_message_id", None)
		rc.setdefault("rollcall_channel_id", None)
		rc.setdefault("html_message_id", None)
		rc.setdefault("html_channel_id", None)
		rc.setdefault("html_url", None)
		rc.setdefault("last_sent_at", None)
		return rc

	# -----------------
	# Scheduler
	# -----------------
	def _start_scheduler(self) -> None:
		try:
			import pytz

			tz = pytz.timezone(TIMEZONE)
		except Exception:
			logger.warning("Invalid TIMEZONE %r; falling back to UTC", TIMEZONE)
			import pytz

			tz = pytz.UTC

		self._scheduler = AsyncIOScheduler(timezone=tz)

		trigger = CronTrigger(
			day_of_week=SCHEDULE_WEEKDAY,
			hour=SCHEDULE_HOUR,
			minute=SCHEDULE_MINUTE,
			timezone=tz,
		)

		def _job():
			asyncio.create_task(self._scheduled_send())

		self._scheduler.add_job(_job, trigger=trigger, id="weekly_rollcall", replace_existing=True)
		self._scheduler.start()

	async def _backstop_loop(self) -> None:
		await self.bot.wait_until_ready()
		while True:
			try:
				await asyncio.sleep(float(BACKSTOP_REFRESH_MINUTES) * 60.0)
				await self._refresh_all(reason="backstop")
			except asyncio.CancelledError:
				return
			except Exception:
				logger.exception("RollCall backstop refresh failed")

	def _debounced_refresh(self, *, reason: str, delay_seconds: float = 3.0) -> None:
		if self._debounce_task and not self._debounce_task.done():
			self._debounce_task.cancel()
		self._debounce_task = asyncio.create_task(self._debounce_worker(reason=reason, delay_seconds=delay_seconds))

	async def _debounce_worker(self, *, reason: str, delay_seconds: float) -> None:
		try:
			await asyncio.sleep(delay_seconds)
			await self._refresh_all(reason=reason)
		except asyncio.CancelledError:
			return

	# -----------------
	# Helpers
	# -----------------
	async def _get_text_channel(self, channel_id: int) -> Optional[discord.TextChannel]:
		ch = self.bot.get_channel(channel_id)
		if isinstance(ch, discord.TextChannel):
			return ch
		try:
			fetched = await self.bot.fetch_channel(channel_id)
			return fetched if isinstance(fetched, discord.TextChannel) else None
		except Exception:
			return None

	def _week_label(self, d: date) -> str:
		# Columns are week numbers with the date rollcall was sent.
		week = d.isocalendar().week
		return f"W{week:02d} {d.isoformat()}"

	def _rollcall_date_for_now(self) -> date:
		"""Return the rollcall date corresponding to the most recent scheduled send."""
		try:
			import pytz

			tz = pytz.timezone(TIMEZONE)
		except Exception:
			import pytz

			tz = pytz.UTC

		now_local = datetime.now(tz)
		scheduled_t = time(hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE)
		# Find most recent weekday occurrence
		weekday_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
		target_wd = weekday_map.get(str(SCHEDULE_WEEKDAY).lower(), 0)
		days_back = (now_local.weekday() - target_wd) % 7
		candidate = (now_local.date() - timedelta(days=days_back))
		if days_back == 0 and now_local.time() < scheduled_t:
			candidate = candidate - timedelta(days=7)
		return candidate

	def _maybe_import_legacy_xls(self) -> None:
		if not IMPORT_LEGACY_XLS_PATH:
			return
		if os.path.exists(WORKBOOK_PATH):
			return
		if not os.path.exists(IMPORT_LEGACY_XLS_PATH):
			logger.warning("Legacy .xls import path not found: %s", IMPORT_LEGACY_XLS_PATH)
			return

		try:
			import pandas as pd
		except Exception:
			logger.warning("pandas not installed; cannot import legacy .xls")
			return

		try:
			xls = pd.ExcelFile(IMPORT_LEGACY_XLS_PATH)
			wb = Workbook()
			# Remove default sheet
			default_sheet = wb.active
			wb.remove(default_sheet)

			for sheet_name in xls.sheet_names:
				df = xls.parse(sheet_name)
				ws = wb.create_sheet(title=str(sheet_name)[:31])
				# Write headers
				ws.append([str(c) for c in df.columns.tolist()])
				# Write rows
				for row in df.itertuples(index=False):
					ws.append([None if (isinstance(v, float) and pd.isna(v)) else v for v in row])

			os.makedirs(os.path.dirname(WORKBOOK_PATH) or ".", exist_ok=True)
			wb.save(WORKBOOK_PATH)
			logger.info("Imported legacy .xls into %s", WORKBOOK_PATH)
		except Exception:
			logger.exception("Failed importing legacy .xls")

	# -----------------
	# Workbook (.xlsx)
	# -----------------
	def _load_or_create_workbook(self) -> Workbook:
		self._maybe_import_legacy_xls()

		if os.path.exists(WORKBOOK_PATH):
			return load_workbook(WORKBOOK_PATH)

		wb = Workbook()
		# Keep the default sheet but rename it to something sane.
		wb.active.title = "README"
		wb.active.append(["This workbook is managed by the bot."])
		os.makedirs(os.path.dirname(WORKBOOK_PATH) or ".", exist_ok=True)
		wb.save(WORKBOOK_PATH)
		return wb

	def _get_or_create_sheet(self, wb: Workbook, cfg: RollCallConfig):
		name = cfg.key[:31]
		if name in wb.sheetnames:
			ws = wb[name]
		else:
			ws = wb.create_sheet(title=name)
			ws.append(["User ID", "Nickname"])
		return ws

	def _sheet_headers(self, ws) -> list[str]:
		headers: list[str] = []
		for cell in ws[1]:
			headers.append(str(cell.value) if cell.value is not None else "")
		return headers

	def _ensure_week_column(self, ws, week_label: str) -> int:
		headers = self._sheet_headers(ws)
		if week_label in headers:
			return headers.index(week_label) + 1
		ws.cell(row=1, column=len(headers) + 1, value=week_label)
		return len(headers) + 1

	def _index_user_rows(self, ws) -> dict[str, int]:
		idx: dict[str, int] = {}
		for r in range(2, ws.max_row + 1):
			v = ws.cell(row=r, column=1).value
			if v is None:
				continue
			idx[str(v)] = r
		return idx

	def _upsert_member_row(self, ws, user_id: int, nickname: str) -> int:
		idx = self._index_user_rows(ws)
		key = str(user_id)
		if key in idx:
			row = idx[key]
			ws.cell(row=row, column=2, value=nickname)
			return row
		row = ws.max_row + 1
		ws.cell(row=row, column=1, value=key)
		ws.cell(row=row, column=2, value=nickname)
		return row

	def _set_cell(self, ws, row: int, col: int, value: str) -> None:
		ws.cell(row=row, column=col, value=value)

	def _save_workbook(self, wb: Workbook) -> None:
		tmp_path = f"{WORKBOOK_PATH}.tmp"
		wb.save(tmp_path)
		os.replace(tmp_path, WORKBOOK_PATH)

	# -----------------
	# Core flows
	# -----------------
	async def _scheduled_send(self) -> None:
		await self.bot.wait_until_ready()
		await self._send_rollcalls(reason="scheduled")

	async def _send_rollcalls(self, *, reason: str) -> None:
		async with self._lock:
			guild = self.bot.get_guild(GUILD_ID)
			if not guild:
				logger.warning("RollCall: guild not found")
				return

			rollcall_d = self._rollcall_date_for_now()
			week_label = self._week_label(rollcall_d)

			wb = self._load_or_create_workbook()

			for cfg in ROLLCALLS:
				try:
					await self._send_rollcall_for_cfg(guild, wb, cfg, week_label=week_label, rollcall_d=rollcall_d, reason=reason)
				except Exception:
					logger.exception("RollCall: failed sending for %s", cfg.key)

			self._save_workbook(wb)
			self._save_state()

	async def _refresh_all(self, *, reason: str) -> None:
		async with self._lock:
			guild = self.bot.get_guild(GUILD_ID)
			if not guild:
				return
			week_label = self._week_label(self._rollcall_date_for_now())
			wb = self._load_or_create_workbook()
			update_html = reason != "reaction"
			for cfg in ROLLCALLS:
				try:
					await self._update_outputs_for_cfg(
						guild,
						wb,
						cfg,
						week_label=week_label,
						reason=reason,
						update_html=update_html,
					)
				except Exception:
					logger.exception("RollCall: failed refresh for %s", cfg.key)
			self._save_workbook(wb)
			self._save_state()

	async def _send_rollcall_for_cfg(
		self,
		guild: discord.Guild,
		wb: Workbook,
		cfg: RollCallConfig,
		*,
		week_label: str,
		rollcall_d: date,
		reason: str,
	) -> None:
		channel = await self._get_text_channel(cfg.channel_id)
		if not channel:
			logger.warning("RollCall %s: channel not found", cfg.key)
			return

		state = self._rc_state(cfg.key)
		# If we already sent this week, don't re-post; just refresh embed/html.
		if state.get("current_week") == week_label and isinstance(state.get("rollcall_message_id"), int):
			await self._update_outputs_for_cfg(guild, wb, cfg, week_label=week_label, reason="already_sent", update_html=True)
			return

		ws = self._get_or_create_sheet(wb, cfg)
		week_col = self._ensure_week_column(ws, week_label)

		expected_members = self._expected_members(guild, cfg)
		for m in expected_members:
			row = self._upsert_member_row(ws, m.id, m.display_name)
			self._set_cell(ws, row, week_col, "❌")

		ping = f"<@&{cfg.ping_role_id}> " if cfg.ping_role_id else ""
		embed = await self._build_status_embed(guild, ws, cfg, week_label=week_label, rollcall_d=rollcall_d, html_url=state.get("html_url"))

		msg = await channel.send(content=f"{ping}Weekly roll call: react with {ROLLCALL_EMOJI}", embed=embed)
		try:
			await msg.add_reaction(ROLLCALL_EMOJI)
		except Exception:
			logger.warning("RollCall %s: failed to add reaction", cfg.key, exc_info=True)

		state["current_week"] = week_label
		state["rollcall_message_id"] = msg.id
		state["rollcall_channel_id"] = channel.id
		state["last_sent_at"] = datetime.utcnow().isoformat()

		# Post HTML + refresh embed to include latest HTML link
		await self._update_outputs_for_cfg(guild, wb, cfg, week_label=week_label, reason=reason, update_html=True)

	def _expected_members(self, guild: discord.Guild, cfg: RollCallConfig) -> list[discord.Member]:
		if cfg.tracked_role_id:
			role = guild.get_role(cfg.tracked_role_id)
			if role:
				return sorted(role.members, key=lambda m: (m.display_name or "").lower())
		# fallback: nobody "expected" (we'll still record reactions)
		return []

	async def _update_outputs_for_cfg(
		self,
		guild: discord.Guild,
		wb: Workbook,
		cfg: RollCallConfig,
		*,
		week_label: str,
		reason: str,
		update_html: bool,
	) -> None:
		state = self._rc_state(cfg.key)
		channel = await self._get_text_channel(cfg.channel_id)
		if not channel:
			return

		ws = self._get_or_create_sheet(wb, cfg)
		rollcall_d = self._parse_week_label_date(week_label) or self._rollcall_date_for_now()

		# Ensure member nicknames stay up to date in the sheet.
		for m in self._expected_members(guild, cfg):
			self._upsert_member_row(ws, m.id, m.display_name)

		if update_html:
			await self._post_html_for_cfg(guild, ws, cfg, week_label=week_label)
		html_url = self._rc_state(cfg.key).get("html_url")

		embed = await self._build_status_embed(guild, ws, cfg, week_label=week_label, rollcall_d=rollcall_d, html_url=html_url)
		msg_id = state.get("rollcall_message_id")
		if isinstance(msg_id, int):
			try:
				msg = await channel.fetch_message(msg_id)
				await msg.edit(embed=embed)
			except discord.NotFound:
				pass
			except discord.Forbidden:
				logger.warning("RollCall %s: missing permission to edit message", cfg.key)
			except Exception:
				logger.warning("RollCall %s: failed editing message", cfg.key, exc_info=True)

	def _parse_week_label_date(self, week_label: str) -> Optional[date]:
		# format: 'W07 2026-02-16'
		try:
			parts = str(week_label).split()
			if len(parts) >= 2:
				return date.fromisoformat(parts[1])
			return None
		except Exception:
			return None

	async def _build_status_embed(
		self,
		guild: discord.Guild,
		ws,
		cfg: RollCallConfig,
		*,
		week_label: str,
		rollcall_d: date,
		html_url: Optional[str],
	) -> discord.Embed:
		expected = self._expected_members(guild, cfg)
		expected_ids = {m.id for m in expected}
		status = self._get_week_status(ws, week_label)

		ticked_ids = {uid for uid, v in status.items() if v == "✅"}
		missing_ids = (expected_ids - ticked_ids) if expected_ids else set()

		def name_for(uid: int) -> str:
			m = guild.get_member(uid)
			return m.display_name if m else str(uid)

		ticked_names = [name_for(uid) for uid in sorted(ticked_ids, key=lambda u: name_for(u).lower())]
		missing_names = [name_for(uid) for uid in sorted(missing_ids, key=lambda u: name_for(u).lower())]

		embed = discord.Embed(
			title=cfg.title,
			color=discord.Color.green(),
			timestamp=datetime.utcnow(),
			description=(
				f"**Week:** {week_label}\n"
				f"**Roll call date:** {rollcall_d.isoformat()}\n"
				f"**React with:** {ROLLCALL_EMOJI}"
			),
			url=(html_url or None),
		)

		if html_url:
			embed.description += f"\n\n[Open full table (HTML)]({html_url})"

		embed.add_field(name="Ticked", value=self._chunk_list(ticked_names), inline=False)
		if expected_ids:
			embed.add_field(name="Missing", value=self._chunk_list(missing_names) if missing_names else "None", inline=False)
		else:
			embed.add_field(name="Missing", value="(No tracked role configured)", inline=False)

		embed.set_footer(text="Updates live as reactions are added/removed")
		return embed

	def _chunk_list(self, items: list[str], *, max_chars: int = 1024) -> str:
		if not items:
			return "None"
		out: list[str] = []
		used = 0
		for s in items:
			extra = len(s) + (2 if out else 0)
			if used + extra > max_chars:
				out.append("…")
				break
			out.append(s)
			used += extra
		return ", ".join(out)

	def _get_week_status(self, ws, week_label: str) -> dict[int, str]:
		headers = self._sheet_headers(ws)
		if week_label not in headers:
			return {}
		col = headers.index(week_label) + 1
		out: dict[int, str] = {}
		for r in range(2, ws.max_row + 1):
			uid = ws.cell(row=r, column=1).value
			if uid is None:
				continue
			v = ws.cell(row=r, column=col).value
			try:
				out[int(str(uid))] = str(v) if v is not None else ""
			except Exception:
				continue
		return out

	async def _post_html_for_cfg(self, guild: discord.Guild, ws, cfg: RollCallConfig, *, week_label: str) -> None:
		state = self._rc_state(cfg.key)
		target_channel_id = HTML_CHANNEL_ID if HTML_CHANNEL_ID else cfg.channel_id
		channel = await self._get_text_channel(int(target_channel_id))
		if not channel:
			return

		# Delete previous HTML message to keep clean
		old_id = state.get("html_message_id")
		old_channel_id = state.get("html_channel_id")
		if isinstance(old_id, int):
			try:
				old_channel = channel
				if isinstance(old_channel_id, int) and old_channel_id != channel.id:
					fetched_old = await self._get_text_channel(old_channel_id)
					if fetched_old:
						old_channel = fetched_old
				old_msg = await old_channel.fetch_message(old_id)
				await old_msg.delete()
			except discord.NotFound:
				pass
			except discord.Forbidden:
				logger.warning("RollCall %s: missing permission to delete old HTML message", cfg.key)
			except Exception:
				logger.warning("RollCall %s: failed deleting old HTML message", cfg.key, exc_info=True)

		html_text = self._render_html(ws, cfg, highlight_week=week_label)
		file_bytes = html_text.encode("utf-8")
		file = discord.File(fp=io.BytesIO(file_bytes), filename=f"{cfg.key}_rollcall.html")

		msg = await channel.send(content=f"{cfg.title} (HTML table)", file=file)
		state["html_message_id"] = msg.id
		state["html_channel_id"] = channel.id
		state["html_url"] = msg.attachments[0].url if msg.attachments else None

	def _render_html(self, ws, cfg: RollCallConfig, *, highlight_week: Optional[str]) -> str:
		headers = self._sheet_headers(ws)
		if not headers or headers[0] != "User ID":
			headers = ["User ID", "Nickname"] + headers[2:]

		col_headers = headers[1:]  # hide User ID
		head_html = "".join(f"<th>{html.escape(str(h))}</th>" for h in col_headers)

		# Collect rows
		body_rows = []
		for r in range(2, ws.max_row + 1):
			uid = ws.cell(row=r, column=1).value
			nick = ws.cell(row=r, column=2).value
			if uid is None and nick is None:
				continue
			cols = [html.escape(str(nick or ""))]
			for c in range(3, len(headers) + 1):
				v = ws.cell(row=r, column=c).value
				cols.append(html.escape(str(v or "")))
			body_rows.append("<tr>" + "".join(f"<td>{v}</td>" for v in cols) + "</tr>")

		table_html = "".join(body_rows) if body_rows else '<tr><td colspan="100">No data.</td></tr>'
		highlight_css = ""
		if highlight_week and highlight_week in headers:
			idx = headers.index(highlight_week)  # 0-based in headers
			# We removed User ID from display, so display index shifts by -1.
			display_col = idx - 1  # since col_headers starts at headers[1]
			# nth-child is 1-based; td/th
			nth = display_col + 1
			highlight_css = f"th:nth-child({nth}), td:nth-child({nth}) {{ background: #fff7cc; }}"

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
	{highlight_css}
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

	# -----------------
	# Events
	# -----------------
	@commands.Cog.listener()
	async def on_ready(self):
		# One-time refresh after login.
		if self._refresh_task is None or self._refresh_task.done():
			self._refresh_task = asyncio.create_task(self._refresh_all(reason="startup"))

	@commands.Cog.listener()
	async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
		if payload.guild_id != GUILD_ID:
			return
		if payload.user_id == getattr(self.bot.user, "id", None):
			return
		if str(payload.emoji) != str(ROLLCALL_EMOJI):
			return
		await self._handle_reaction_change(payload, marked=True)

	@commands.Cog.listener()
	async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
		if payload.guild_id != GUILD_ID:
			return
		if str(payload.emoji) != str(ROLLCALL_EMOJI):
			return
		await self._handle_reaction_change(payload, marked=False)

	async def _handle_reaction_change(self, payload: discord.RawReactionActionEvent, *, marked: bool) -> None:
		# Find which rollcall this message belongs to
		match_cfg: Optional[RollCallConfig] = None
		week_label: Optional[str] = None
		for cfg in ROLLCALLS:
			st = self._rc_state(cfg.key)
			if st.get("rollcall_message_id") == payload.message_id:
				match_cfg = cfg
				week_label = st.get("current_week")
				break
		if not match_cfg or not week_label:
			return

		async with self._lock:
			guild = self.bot.get_guild(GUILD_ID)
			if not guild:
				return

			member = guild.get_member(payload.user_id)
			if not member:
				try:
					member = await guild.fetch_member(payload.user_id)
				except Exception:
					member = None

			wb = self._load_or_create_workbook()
			ws = self._get_or_create_sheet(wb, match_cfg)
			col = self._ensure_week_column(ws, week_label)
			row = self._upsert_member_row(ws, payload.user_id, member.display_name if member else str(payload.user_id))
			self._set_cell(ws, row, col, "✅" if marked else "❌")
			self._save_workbook(wb)
			self._save_state()

		# Outside lock: update outputs (debounced). HTML refresh is skipped for reaction updates.
		self._debounced_refresh(reason="reaction", delay_seconds=2.0)

	@commands.Cog.listener()
	async def on_member_update(self, before: discord.Member, after: discord.Member):
		if before.guild.id != GUILD_ID:
			return
		if before.display_name != after.display_name:
			# Keep nicknames in workbook + HTML in sync.
			self._debounced_refresh(reason="nickname", delay_seconds=5.0)


async def setup(bot: commands.Bot):
	await bot.add_cog(RollCallCog(bot))
	logger.info("RollCallCog loaded")

