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
}

# Restrict who can approve/reject by role. Leave empty to allow admins only.
NAMESHAME_APPROVER_ROLE_IDS: set[int] = {
	1213495462632361994,
}

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


def _parse_user_id(raw: str) -> int | None:
	if not raw:
		return None
	raw = raw.strip()

	m = re.match(r"^<@!?(\d+)>$", raw)
	if m:
		return int(m.group(1))

	if raw.isdigit():
		try:
			return int(raw)
		except Exception:
			return None

	m = re.search(r"(\d{15,25})", raw)
	if m:
		try:
			return int(m.group(1))
		except Exception:
			return None
	return None


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


class ReportModal(discord.ui.Modal, title="Report a player"):
	player = discord.ui.TextInput(
		label="Player (mention or Discord ID)",
		placeholder="e.g. @Player or 123456789012345678",
		max_length=100,
	)
	reason = discord.ui.TextInput(
		label="Reason",
		placeholder="What happened? Be specific.",
		style=discord.TextStyle.paragraph,
		max_length=1000,
	)

	def __init__(self, cog: "NameShame"):
		super().__init__()
		self.cog = cog

	async def on_submit(self, interaction: discord.Interaction):
		if not isinstance(interaction.user, discord.Member):
			return await interaction.response.send_message(
				"Reporting must be done inside the server.",
				ephemeral=True,
			)

		if NAMESHAME_REPORTER_ROLE_IDS:
			allowed_roles = {rid for rid in NAMESHAME_REPORTER_ROLE_IDS if rid}
			if allowed_roles and not any(r.id in allowed_roles for r in interaction.user.roles):
				return await interaction.response.send_message(
					"You are not allowed to submit reports.",
					ephemeral=True,
				)

		if not self.cog.approval_channel_id:
			return await interaction.response.send_message(
				"Reporting is not configured yet. Set `NAMESHAME_APPROVAL_CHANNEL_ID` in the cog.",
				ephemeral=True,
			)

		target_id = _parse_user_id(str(self.player.value))
		if not target_id:
			return await interaction.response.send_message(
				"Please mention the player or paste their Discord user ID.",
				ephemeral=True,
			)

		reason = str(self.reason.value).strip()
		if not reason:
			return await interaction.response.send_message(
				"Please provide a reason.",
				ephemeral=True,
			)

		await interaction.response.send_message("Report submitted for approval.", ephemeral=True)
		await self.cog.create_pending_report(
			interaction=interaction,
			target_user_id=target_id,
			reason=reason,
		)


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
		await interaction.response.send_modal(ReportModal(self.cog))


class ApprovalView(discord.ui.View):
	def __init__(self, cog: "NameShame", pending: PendingReport):
		super().__init__(timeout=None)
		self.cog = cog
		self.pending = pending

	@discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="nameshame:approve")
	async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
		if not await self.cog.is_approver(interaction):
			return await interaction.response.send_message("You cannot approve reports.", ephemeral=True)
		await self.cog.approve_report(interaction, self.pending)

	@discord.ui.button(label="Reject", style=discord.ButtonStyle.secondary, custom_id="nameshame:reject")
	async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
		if not await self.cog.is_approver(interaction):
			return await interaction.response.send_message("You cannot reject reports.", ephemeral=True)
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

	def _persist(self):
		self.state["message_id"] = self.main_message_id
		# channel/thread IDs are typically configured at the top; persist as fallback
		self.state["channel_id"] = self.main_channel_id
		self.state["approval_channel_id"] = self.approval_channel_id
		_save_state(self.state)

	def build_main_embed(self, guild: discord.Guild | None) -> discord.Embed:
		embed = discord.Embed(
			title="🚨 Player Reports / Strikes",
			color=discord.Color.orange(),
			timestamp=datetime.now(timezone.utc),
			description=(
				"Use **Report Player** to submit a report with a reason.\n"
				"Reports are reviewed by staff in the approval channel.\n\n"
				"Use the dropdown to view **who reported**, **why**, and **when** for a player."
			),
		)

		reports = _get_reports_root(self.state)
		items = sorted(reports.items(), key=_player_sort_key)
		items = [it for it in items if int((it[1] or {}).get("strikes") or 0) > 0 or (it[1] or {}).get("history")]

		if not items:
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

		await interaction.response.send_message("Approved and strike added.", ephemeral=True)
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

		await interaction.response.send_message("Rejected.", ephemeral=True)

async def setup(bot: commands.Bot):
	await bot.add_cog(NameShame(bot))
