import asyncio
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import discord
from discord.ext import commands

from data_paths import data_path

# --------------------------------------------------
# CONFIG YOU EDIT
# --------------------------------------------------

GUILD_ID = 1097913605082579024

# Where the main "strikes" embed lives
NAMESHAME_MAIN_CHANNEL_ID = 1099806153170489485  # e.g. 123... (text channel ID)

# Where approval messages get sent (can be a channel OR a thread ID)
NAMESHAME_APPROVAL_CHANNEL_ID = 1099806153170489485  # e.g. 123...

# Restrict who can submit reports by role. Leave empty to allow anyone.
NAMESHAME_REPORTER_ROLE_IDS: set[int] = {
	1213495462632361994,
	1100005629264941137,
	1214284764077363231,
	1108426806635282513, 
	1216760317283926046,
	1102199425654333522,
	1113880506115829963,
	1097946662942560407,
}

# Restrict who can approve/reject by role. Leave empty to allow admins only.
NAMESHAME_APPROVER_ROLE_IDS: set[int] = {
	1097946543065137183,
}

# Role that can use the Admin Reports button
NAMESHAME_ADMIN_ROLE_ID = 1213495462632361994  # set to your admin role ID

# Preset reasons for the reason dropdown
NAMESHAME_REASON_OPTIONS: list[str] = [
	"Excessive Teamkilling / Griefing",
	"Racism / Hate speech",
	"Cheating / Exploiting",
	"Toxic / Harassment",
	"No Show / AFK",
	"Other (provide details to an NCO)",
]

# Persistent state file (main embed message id, approval channel id, strikes/history)
NAMESHAME_STATE_FILE = data_path("nameshame_state.json")

# Limit how many players show in the main embed / dropdown
MAX_LISTED_PLAYERS = 25

# Limit how many report entries show in detail view
MAX_DETAILS_ENTRIES = 10


# --------------------------------------------------
# Persistence
# --------------------------------------------------


def _utc_now_iso() -> str:
	return datetime.now(timezone.utc).isoformat()


def _load_state() -> dict:
	if not os.path.exists(NAMESHAME_STATE_FILE):
		return {}
	try:
		with open(NAMESHAME_STATE_FILE, "r", encoding="utf-8") as f:
			return json.load(f)
	except Exception as e:
		print(f"[NameShame] Failed to load state: {e}")
		return {}


def _save_state(state: dict) -> None:
	try:
		with open(NAMESHAME_STATE_FILE, "w", encoding="utf-8") as f:
			json.dump(state, f, indent=2)
	except Exception as e:
		print(f"[NameShame] Failed to save state: {e}")


def _reason_options(selected_reason: str | None) -> list[discord.SelectOption]:
	options: list[discord.SelectOption] = []
	for idx, label in enumerate(NAMESHAME_REASON_OPTIONS):
		val = str(idx)
		options.append(
			discord.SelectOption(
				label=label[:100],
				value=val,
				default=(label == selected_reason),
			)
		)
	return options


def _strike_value_options(selected_value: int | None) -> list[discord.SelectOption]:
	# 0 = clear strikes
	vals = list(range(0, 11))
	options: list[discord.SelectOption] = []
	for v in vals:
		label = "0 (clear strikes)" if v == 0 else str(v)
		options.append(
			discord.SelectOption(
				label=label,
				value=str(v),
				default=(v == selected_value),
			)
		)
	return options


async def _safe_ephemeral_reply(interaction: discord.Interaction, content: str) -> None:
	"""Send an ephemeral reply or followup without throwing if the interaction expired."""
	try:
		if interaction.response.is_done():
			await interaction.followup.send(content, ephemeral=True)
		else:
			await interaction.response.send_message(content, ephemeral=True)
	except discord.NotFound:
		# Interaction expired / already gone
		return
	except Exception:
		return


# --------------------------------------------------
# Data model helpers
# --------------------------------------------------


def _get_reports_root(state: dict) -> dict:
	# Structure:
	# state = {
	#   "message_id": int,
	#   "channel_id": int,
	#   "approval_channel_id": int,
	#   "reports": {
	#       "<user_id>": {
	#           "strikes": int,
	#           "history": [ {"ts": iso, "by": user_id, "reason": str, "approval_url": str|None} ]
	#       }
	#   }
	# }
	return state.setdefault("reports", {})


def _get_player_entry(state: dict, user_id: int) -> dict:
	reports = _get_reports_root(state)
	entry = reports.setdefault(str(user_id), {})
	entry.setdefault("strikes", 0)
	entry.setdefault("history", [])
	return entry


def _player_sort_key(item: tuple[str, dict]):
	user_id_str, entry = item
	strikes = int(entry.get("strikes") or 0)
	history = entry.get("history") or []
	last_ts = history[-1].get("ts") if history else ""
	return (-strikes, last_ts, user_id_str)


@dataclass
class PendingReport:
	report_id: str
	target_user_id: int
	reporter_user_id: int
	reason: str
	created_ts_iso: str


# --------------------------------------------------
# UI
# --------------------------------------------------



class ReportPlayerSelect(discord.ui.UserSelect):
	def __init__(self, view: "ReportFlowView"):
		super().__init__(placeholder="Select a player…", min_values=1, max_values=1)
		self.flow_view = view

	async def callback(self, interaction: discord.Interaction):
		target = self.values[0] if self.values else None
		target_id = getattr(target, "id", None)
		if not target_id:
			return await interaction.response.send_message("Invalid selection.", ephemeral=True)

		self.flow_view.selected_target_user_id = int(target_id)
		await self.flow_view.refresh_message(interaction)


class ReportReasonSelect(discord.ui.Select):
	def __init__(self, view: "ReportFlowView"):
		self.flow_view = view
		super().__init__(
			placeholder="Select a reason…",
			custom_id="nameshame:reason",
			min_values=1,
			max_values=1,
			options=_reason_options(view.selected_reason),
		)

	async def callback(self, interaction: discord.Interaction):
		idx = int(self.values[0])
		idx = max(0, min(idx, len(NAMESHAME_REASON_OPTIONS) - 1))
		self.flow_view.selected_reason = NAMESHAME_REASON_OPTIONS[idx]
		await self.flow_view.refresh_message(interaction)


class ReportFlowView(discord.ui.View):
	def __init__(self, cog: "NameShame"):
		super().__init__(timeout=180)
		self.cog = cog
		self.selected_target_user_id: int | None = None
		self.selected_reason: str | None = None
		self.submitted: bool = False

		self.player_select = ReportPlayerSelect(self)
		self.reason_select = ReportReasonSelect(self)
		self.add_item(self.player_select)
		self.add_item(self.reason_select)

		self.submit_button = discord.ui.Button(
			label="Submit",
			style=discord.ButtonStyle.success,
			custom_id="nameshame:submit",
			disabled=True,
		)
		self.submit_button.callback = self._submit
		self.add_item(self.submit_button)

		self.cancel_button = discord.ui.Button(
			label="Cancel",
			style=discord.ButtonStyle.secondary,
			custom_id="nameshame:cancel",
		)
		self.cancel_button.callback = self._cancel
		self.add_item(self.cancel_button)

	def _content(self) -> str:
		player_txt = f"<@{self.selected_target_user_id}>" if self.selected_target_user_id else "(not selected)"
		reason_txt = self.selected_reason if self.selected_reason else "(not selected)"
		return f"**Player:** {player_txt}\n**Reason:** {reason_txt}"

	def _sync_controls(self):
		ready = bool(self.selected_target_user_id and self.selected_reason) and not self.submitted
		self.submit_button.disabled = not ready
		self.player_select.disabled = self.submitted
		self.reason_select.disabled = self.submitted
		# Refresh reason options to show the selected one as default ("sticks")
		self.reason_select.options = _reason_options(self.selected_reason)
		self.cancel_button.disabled = self.submitted

	async def refresh_message(self, interaction: discord.Interaction):
		self._sync_controls()
		try:
			await interaction.response.edit_message(content=self._content(), view=self)
		except Exception:
			# Fallback when already responded
			try:
				await interaction.edit_original_response(content=self._content(), view=self)
			except Exception:
				return

	async def _cancel(self, interaction: discord.Interaction):
		self.submitted = True
		self._sync_controls()
		try:
			await interaction.response.edit_message(content="Cancelled.", view=None)
		except Exception:
			try:
				await interaction.edit_original_response(content="Cancelled.", view=None)
			except Exception:
				return

	async def _submit(self, interaction: discord.Interaction):
		if not isinstance(interaction.user, discord.Member):
			return await _safe_ephemeral_reply(interaction, "Reporting must be done inside the server.")

		if NAMESHAME_REPORTER_ROLE_IDS:
			allowed_roles = {rid for rid in NAMESHAME_REPORTER_ROLE_IDS if rid}
			if allowed_roles and not any(r.id in allowed_roles for r in interaction.user.roles):
				return await _safe_ephemeral_reply(interaction, "You are not allowed to submit reports.")

		if not self.cog.approval_channel_id:
			return await _safe_ephemeral_reply(
				interaction,
				"Reporting is not configured yet. Set `NAMESHAME_APPROVAL_CHANNEL_ID` in the cog.",
			)

		if not self.selected_target_user_id or not self.selected_reason:
			return await _safe_ephemeral_reply(interaction, "Select a player and a reason first.")

		# Acknowledge quickly to avoid interaction expiry
		try:
			if not interaction.response.is_done():
				await interaction.response.defer(ephemeral=True)
		except Exception:
			pass

		await self.cog.create_pending_report(
			interaction=interaction,
			target_user_id=int(self.selected_target_user_id),
			reason=str(self.selected_reason),
		)

		self.submitted = True
		self._sync_controls()
		try:
			await interaction.edit_original_response(content=f"Submitted for approval.\n\n{self._content()}", view=self)
		except Exception:
			return



class AdminPlayerSelect(discord.ui.Select):
	def __init__(self, view: "AdminManageView", options: list[discord.SelectOption]):
		self.flow_view = view
		disabled = len(options) == 0
		if disabled:
			options = [
				discord.SelectOption(
					label="No reports yet",
					value="0",
					description="No reported players exist.",
				)
			]
		super().__init__(
			placeholder="Select a reported player…",
			custom_id="nameshame:admin_player",
			min_values=1,
			max_values=1,
			options=options,
			disabled=disabled,
		)

	async def callback(self, interaction: discord.Interaction):
		if self.values and self.values[0] == "0":
			return await interaction.response.send_message("No reported players yet.", ephemeral=True)
		target_user_id = int(self.values[0])
		self.flow_view.selected_target_user_id = target_user_id
		if self.flow_view.selected_strikes is None:
			self.flow_view.selected_strikes = self.flow_view.cog.get_strikes(target_user_id)
		await self.flow_view.refresh_message(interaction)


class AdminStrikesSelect(discord.ui.Select):
	def __init__(self, view: "AdminManageView"):
		self.flow_view = view
		options = _strike_value_options(view.selected_strikes)
		super().__init__(
			placeholder="Set strikes to…",
			custom_id="nameshame:admin_strikes",
			min_values=1,
			max_values=1,
			options=options,
		)

	async def callback(self, interaction: discord.Interaction):
		self.flow_view.selected_strikes = int(self.values[0])
		await self.flow_view.refresh_message(interaction)


class AdminManageView(discord.ui.View):
	def __init__(self, cog: "NameShame"):
		super().__init__(timeout=180)
		self.cog = cog
		self.selected_target_user_id: int | None = None
		self.selected_strikes: int | None = None
		self.submitted: bool = False
		self._players_available = len(self.cog.build_admin_player_options(selected_user_id=None)) > 0

		self.player_select = AdminPlayerSelect(
			self,
			options=self.cog.build_admin_player_options(selected_user_id=self.selected_target_user_id),
		)
		self.strikes_select = AdminStrikesSelect(self)
		self.add_item(self.player_select)
		self.add_item(self.strikes_select)

		self.submit_button = discord.ui.Button(
			label="Apply",
			style=discord.ButtonStyle.success,
			custom_id="nameshame:admin_apply",
			disabled=True,
		)
		self.submit_button.callback = self._apply
		self.add_item(self.submit_button)

		self.cancel_button = discord.ui.Button(
			label="Close",
			style=discord.ButtonStyle.secondary,
			custom_id="nameshame:admin_close",
		)
		self.cancel_button.callback = self._close
		self.add_item(self.cancel_button)

	def _content(self) -> str:
		player_txt = f"<@{self.selected_target_user_id}>" if self.selected_target_user_id else "(not selected)"
		strikes_txt = str(self.selected_strikes) if self.selected_strikes is not None else "(not selected)"
		return (
			f"**Admin Reports**\n\n"
			f"**Player:** {player_txt}\n"
			f"**Set strikes to:** {strikes_txt}\n\n"
			f"**Reason:** admin changed"
		)

	def _sync_controls(self):
		ready = bool(self.selected_target_user_id and self.selected_strikes is not None) and not self.submitted
		self.submit_button.disabled = not ready
		self.player_select.disabled = (not self._players_available) or self.submitted
		self.strikes_select.disabled = self.submitted
		self.cancel_button.disabled = False

		# rebuild options so they "stick"
		self.player_select.options = self.cog.build_admin_player_options(selected_user_id=self.selected_target_user_id) or self.player_select.options
		self.strikes_select.options = _strike_value_options(self.selected_strikes)

	async def refresh_message(self, interaction: discord.Interaction):
		self._sync_controls()
		try:
			await interaction.response.edit_message(content=self._content(), view=self)
		except Exception:
			try:
				await interaction.edit_original_response(content=self._content(), view=self)
			except Exception:
				return

	async def _close(self, interaction: discord.Interaction):
		try:
			await interaction.response.edit_message(content="Closed.", view=None)
		except Exception:
			try:
				await interaction.edit_original_response(content="Closed.", view=None)
			except Exception:
				return

	async def _apply(self, interaction: discord.Interaction):
		if not await self.cog.is_admin_reports(interaction):
			return await _safe_ephemeral_reply(interaction, "You cannot use Admin Reports.")

		if not self.selected_target_user_id or self.selected_strikes is None:
			return await _safe_ephemeral_reply(interaction, "Select a player and strike value first.")

		try:
			if not interaction.response.is_done():
				await interaction.response.defer(ephemeral=True)
		except Exception:
			pass

		await self.cog.admin_set_strikes(
			target_user_id=int(self.selected_target_user_id),
			new_strikes=int(self.selected_strikes),
			admin_user_id=interaction.user.id,
		)

		self.submitted = True
		self._sync_controls()
		try:
			await interaction.edit_original_response(content=f"Applied.\n\n{self._content()}", view=self)
		except Exception:
			return


class DetailsSelect(discord.ui.Select):
	def __init__(self, cog: "NameShame"):
		self.cog = cog

		options = cog.build_details_options()
		disabled = len(options) == 0
		if disabled:
			options = [
				discord.SelectOption(
					label="No reports yet",
					value="0",
					description="No approved reports have been logged.",
				)
			]
		super().__init__(
			placeholder="View details for a reported player…",
			custom_id="nameshame:details",
			min_values=1,
			max_values=1,
			options=options,
			disabled=disabled,
		)

	async def callback(self, interaction: discord.Interaction):
		if self.values and self.values[0] == "0":
			return await interaction.response.send_message("No reports yet.", ephemeral=True)

		user_id = int(self.values[0])
		embed = await self.cog.build_player_details_embed(interaction.guild, user_id)
		await interaction.response.send_message(embed=embed, ephemeral=True)


class NameShameMainView(discord.ui.View):
	def __init__(self, cog: "NameShame"):
		super().__init__(timeout=None)
		self.cog = cog
		self.add_item(DetailsSelect(cog))

	@discord.ui.button(label="Report Player", style=discord.ButtonStyle.danger, custom_id="nameshame:report")
	async def report_button(self, interaction: discord.Interaction, button: discord.ui.Button):
		await interaction.response.send_message(
			content="Select a player and a reason, then submit:",
			ephemeral=True,
			view=ReportFlowView(self.cog),
		)

	@discord.ui.button(label="Admin Reports", style=discord.ButtonStyle.primary, custom_id="nameshame:admin")
	async def admin_button(self, interaction: discord.Interaction, button: discord.ui.Button):
		if not await self.cog.is_admin_reports(interaction):
			return await interaction.response.send_message("You cannot use Admin Reports.", ephemeral=True)
		view = AdminManageView(self.cog)
		await interaction.response.send_message(content=view._content(), ephemeral=True, view=view)


class ApprovalView(discord.ui.View):
	def __init__(self, cog: "NameShame", pending: PendingReport):
		super().__init__(timeout=None)
		self.cog = cog
		self.pending = pending

	@discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="nameshame:approve")
	async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
		if not await self.cog.is_approver(interaction):
			return await _safe_ephemeral_reply(interaction, "You cannot approve reports.")

		# Acknowledge immediately to avoid "Unknown interaction" if processing takes time.
		try:
			if not interaction.response.is_done():
				await interaction.response.defer(ephemeral=True)
		except discord.NotFound:
			return
		except Exception:
			pass
		await self.cog.approve_report(interaction, self.pending)

	@discord.ui.button(label="Reject", style=discord.ButtonStyle.secondary, custom_id="nameshame:reject")
	async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
		if not await self.cog.is_approver(interaction):
			return await _safe_ephemeral_reply(interaction, "You cannot reject reports.")

		try:
			if not interaction.response.is_done():
				await interaction.response.defer(ephemeral=True)
		except discord.NotFound:
			return
		except Exception:
			pass
		await self.cog.reject_report(interaction, self.pending)


# --------------------------------------------------
# COG
# --------------------------------------------------


class NameShame(commands.Cog):
	def __init__(self, bot: commands.Bot):
		self.bot = bot
		self._lock = asyncio.Lock()

		self.state = _load_state()
		self.main_message_id: int | None = self.state.get("message_id")
		self.main_channel_id: int | None = NAMESHAME_MAIN_CHANNEL_ID or self.state.get("channel_id")
		self.approval_channel_id: int | None = NAMESHAME_APPROVAL_CHANNEL_ID or self.state.get("approval_channel_id")

		# Ensure defaults exist
		_get_reports_root(self.state)
		_save_state(self.state)

	# ----------------- helpers -----------------

	async def is_approver(self, interaction: discord.Interaction) -> bool:
		if not isinstance(interaction.user, discord.Member):
			return False
		allowed_roles = {rid for rid in NAMESHAME_APPROVER_ROLE_IDS if rid}
		if allowed_roles:
			return any(r.id in allowed_roles for r in interaction.user.roles)
		return interaction.user.guild_permissions.administrator

	async def is_admin_reports(self, interaction: discord.Interaction) -> bool:
		if not isinstance(interaction.user, discord.Member):
			return False
		if interaction.user.guild_permissions.administrator:
			return True
		if not NAMESHAME_ADMIN_ROLE_ID:
			return False
		return any(r.id == int(NAMESHAME_ADMIN_ROLE_ID) for r in interaction.user.roles)

	def _persist(self):
		self.state["message_id"] = self.main_message_id
		# channel/thread IDs are typically configured at the top; persist as fallback
		self.state["channel_id"] = self.main_channel_id
		self.state["approval_channel_id"] = self.approval_channel_id
		_save_state(self.state)

	def build_main_embed(self, guild: discord.Guild | None) -> discord.Embed:
		embed = discord.Embed(
			title="🚨 Player Reporting Tool",
			color=discord.Color.orange(),
			timestamp=datetime.now(timezone.utc),
			description=(
				"Use **Report Player** to submit a report with a reason.\n"
				"Reports are reviewed by staff in the <#1097920406398185512> channel. You may be asked to provide additional details.\n\n"
				"Use the dropdown to view **who reported**, **why**, and **when** for a player."
			),
		)

		reports = _get_reports_root(self.state)
		items = sorted(reports.items(), key=_player_sort_key)
		items = [it for it in items if int((it[1] or {}).get("strikes") or 0) > 0]

		if not items:
			if reports:
				embed.add_field(name="No active strikes", value="Nobody currently has strikes (0-strike entries are hidden).", inline=False)
			else:
				embed.add_field(name="No reports yet", value="No approved reports have been logged.", inline=False)
			return embed

		lines: list[str] = []
		for user_id_str, entry in items[:MAX_LISTED_PLAYERS]:
			strikes = int(entry.get("strikes") or 0)
			try:
				user_id = int(user_id_str)
			except Exception:
				continue

			mention = f"<@{user_id}>"
			lines.append(f"{mention} — **{strikes}** strike{'s' if strikes != 1 else ''}")

		embed.add_field(name="Strike list", value="\n".join(lines)[:1024], inline=False)
		return embed

	def build_details_options(self) -> list[discord.SelectOption]:
		guild = self.bot.get_guild(GUILD_ID)
		reports = _get_reports_root(self.state)
		items = sorted(reports.items(), key=_player_sort_key)
		items = [it for it in items if (it[1] or {}).get("history")]

		opts: list[discord.SelectOption] = []
		for user_id_str, entry in items[:MAX_LISTED_PLAYERS]:
			try:
				user_id = int(user_id_str)
			except Exception:
				continue
			strikes = int(entry.get("strikes") or 0)
			member = guild.get_member(user_id) if guild else None
			name = member.display_name if member else str(user_id)
			label = f"{name} ({strikes} strikes)" if strikes else name
			opts.append(discord.SelectOption(label=label[:100], value=str(user_id)))
		return opts

	def build_admin_player_options(self, selected_user_id: int | None) -> list[discord.SelectOption]:
		guild = self.bot.get_guild(GUILD_ID)
		reports = _get_reports_root(self.state)
		items = sorted(reports.items(), key=_player_sort_key)
		# include anyone who has strikes or history
		items = [it for it in items if int((it[1] or {}).get("strikes") or 0) > 0 or (it[1] or {}).get("history")]

		opts: list[discord.SelectOption] = []
		for user_id_str, entry in items[:MAX_LISTED_PLAYERS]:
			try:
				user_id = int(user_id_str)
			except Exception:
				continue
			member = guild.get_member(user_id) if guild else None
			name = member.display_name if member else str(user_id)
			strikes = int((entry or {}).get("strikes") or 0)
			opts.append(
				discord.SelectOption(
					label=(f"{name}" if len(name) <= 90 else name[:90] + "…"),
					value=str(user_id),
					description=f"{strikes} strike{'s' if strikes != 1 else ''}",
					default=(selected_user_id == user_id),
				)
			)
		return opts

	async def admin_set_strikes(self, target_user_id: int, new_strikes: int, admin_user_id: int):
		new_strikes = max(0, int(new_strikes))
		async with self._lock:
			entry = _get_player_entry(self.state, target_user_id)
			entry["strikes"] = new_strikes
			entry["history"].append(
				{
					"ts": _utc_now_iso(),
					"by": int(admin_user_id),
					"reason": "admin changed",
					"approval_url": None,
				}
			)
			self._persist()

		guild = self.bot.get_guild(GUILD_ID)
		await self.refresh_main_message(guild)

	def get_strikes(self, target_user_id: int) -> int:
		reports = _get_reports_root(self.state)
		entry = reports.get(str(target_user_id)) or {}
		return int(entry.get("strikes") or 0)

	async def build_player_details_embed(self, guild: discord.Guild | None, user_id: int) -> discord.Embed:
		entry = _get_player_entry(self.state, user_id)
		strikes = int(entry.get("strikes") or 0)
		history = list(entry.get("history") or [])

		target_label = f"<@{user_id}>"
		embed = discord.Embed(
			title=f"📌 Report details for {target_label}",
			color=discord.Color.red(),
			timestamp=datetime.now(timezone.utc),
		)
		embed.add_field(name="Strikes", value=str(strikes), inline=True)
		embed.add_field(name="Total reports", value=str(len(history)), inline=True)

		if not history:
			embed.description = "No reports found for this player."
			return embed

		recent = history[-MAX_DETAILS_ENTRIES:]
		recent.reverse()
		lines: list[str] = []
		for h in recent:
			ts = h.get("ts") or ""
			by = h.get("by")
			reason = (h.get("reason") or "").strip()
			approval_url = h.get("approval_url")

			when = ts.replace("T", " ").replace("+00:00", " UTC") if ts else "Unknown time"
			by_txt = f"<@{int(by)}>" if by else "Unknown"
			reason_short = reason if len(reason) <= 200 else (reason[:197] + "…")
			link = f" ([approval]({approval_url}))" if approval_url else ""
			lines.append(f"• **When:** {when}\n  **By:** {by_txt}{link}\n  **Why:** {reason_short}")

		embed.description = "\n\n".join(lines)
		return embed

	async def ensure_main_message(self, guild: discord.Guild | None):
		async with self._lock:
			# Always prefer configured channel IDs
			self.main_channel_id = NAMESHAME_MAIN_CHANNEL_ID or self.main_channel_id
			self.approval_channel_id = NAMESHAME_APPROVAL_CHANNEL_ID or self.approval_channel_id

			if not self.main_channel_id:
				return
			channel = self.bot.get_channel(self.main_channel_id)
			if channel is None:
				try:
					channel = await self.bot.fetch_channel(self.main_channel_id)
				except Exception:
					channel = None

			if not isinstance(channel, discord.TextChannel):
				return

			embed = self.build_main_embed(guild)
			view = NameShameMainView(self)

			msg = None
			if self.main_message_id:
				try:
					msg = await channel.fetch_message(self.main_message_id)
				except Exception:
					msg = None

			if msg is None:
				msg = await channel.send(embed=embed, view=view)
				self.main_message_id = msg.id
				self.main_channel_id = channel.id
				self._persist()
			else:
				await msg.edit(embed=embed, view=view)

	async def refresh_main_message(self, guild: discord.Guild | None):
		await self.ensure_main_message(guild)

	# ----------------- lifecycle -----------------

	@commands.Cog.listener()
	async def on_ready(self):
		try:
			self.bot.add_view(NameShameMainView(self))
		except Exception:
			# add_view can be called multiple times safely; ignore if discord.py complains.
			pass

		# Auto-post/refresh only if configured.
		guild = self.bot.get_guild(GUILD_ID)
		if NAMESHAME_MAIN_CHANNEL_ID:
			self.main_channel_id = NAMESHAME_MAIN_CHANNEL_ID
		if NAMESHAME_APPROVAL_CHANNEL_ID:
			self.approval_channel_id = NAMESHAME_APPROVAL_CHANNEL_ID

		if self.main_channel_id:
			await self.ensure_main_message(guild)

	# ----------------- report flow -----------------

	async def create_pending_report(self, interaction: discord.Interaction, target_user_id: int, reason: str):
		self.approval_channel_id = NAMESHAME_APPROVAL_CHANNEL_ID or self.approval_channel_id
		approval_channel_id = self.approval_channel_id
		if not approval_channel_id:
			return

		dest = self.bot.get_channel(approval_channel_id)
		if dest is None:
			try:
				dest = await self.bot.fetch_channel(approval_channel_id)
			except Exception:
				dest = None

		if not isinstance(dest, (discord.TextChannel, discord.Thread)):
			return

		report_id = f"{int(datetime.now(timezone.utc).timestamp())}:{interaction.user.id}:{target_user_id}"
		pending = PendingReport(
			report_id=report_id,
			target_user_id=target_user_id,
			reporter_user_id=interaction.user.id,
			reason=reason,
			created_ts_iso=_utc_now_iso(),
		)

		entry = _get_player_entry(self.state, target_user_id)
		strikes = int(entry.get("strikes") or 0)

		embed = discord.Embed(
			title="📝 Player report (pending approval)",
			color=discord.Color.blurple(),
			timestamp=datetime.now(timezone.utc),
		)
		embed.add_field(name="Player", value=f"<@{target_user_id}> (`{target_user_id}`)", inline=False)
		embed.add_field(name="Reported by", value=f"<@{interaction.user.id}> (`{interaction.user.id}`)", inline=False)
		embed.add_field(name="Reason", value=reason[:1024], inline=False)
		embed.add_field(name="Current strikes", value=str(strikes), inline=True)
		embed.set_footer(text=f"Report ID: {report_id}")

		view = ApprovalView(self, pending)
		await dest.send(embed=embed, view=view)

	async def approve_report(self, interaction: discord.Interaction, pending: PendingReport):
		async with self._lock:
			entry = _get_player_entry(self.state, pending.target_user_id)
			entry["strikes"] = int(entry.get("strikes") or 0) + 1

			approval_url = None
			try:
				if interaction.message:
					approval_url = interaction.message.jump_url
			except Exception:
				approval_url = None

			entry["history"].append(
				{
					"ts": pending.created_ts_iso,
					"by": pending.reporter_user_id,
					"reason": pending.reason,
					"approval_url": approval_url,
				}
			)
			self._persist()

		# Update the approval message to show status
		try:
			if interaction.message and interaction.message.embeds:
				e = interaction.message.embeds[0]
				e = e.copy()
				e.color = discord.Color.green()
				e.title = "✅ Player report (approved)"
				await interaction.message.edit(embed=e, view=None)
		except Exception:
			pass

		await _safe_ephemeral_reply(interaction, "Approved and strike added.")
		guild = self.bot.get_guild(GUILD_ID)
		await self.refresh_main_message(guild)

	async def reject_report(self, interaction: discord.Interaction, pending: PendingReport):
		try:
			if interaction.message and interaction.message.embeds:
				e = interaction.message.embeds[0]
				e = e.copy()
				e.color = discord.Color.dark_grey()
				e.title = "⛔ Player report (rejected)"
				await interaction.message.edit(embed=e, view=None)
		except Exception:
			pass

		await _safe_ephemeral_reply(interaction, "Rejected.")

async def setup(bot: commands.Bot):
	await bot.add_cog(NameShame(bot))
