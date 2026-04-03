import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from data_paths import data_path
from league_config import CLAN_ROLE_IDS


# -----------------------------
# Config (fill these in)
# -----------------------------

# Guild scope for slash commands (set this to your server ID)
GUILD_ID: int = 1462382487622914079

# Role allowed to use admin scoreboard edit commands
ADMIN_ROLE_ID: int = 1109147750932676649

# Channel where the main "Submit Scores" embed is posted
SCOREBOARD_CHANNEL_ID: int = 1462387812815998997

# Channel where results are posted for confirmation by the opposing clan
VALIDATION_CHANNEL_ID: int = 1462382488784470181

# Channel where the leaderboard + match images are posted
LEADERBOARD_CHANNEL_ID: int = 1462384116376014911

# Role IDs for each clan (name -> role_id)
# Source of truth is league_config.py
CLAN_ROLES: dict[str, int] = dict(CLAN_ROLE_IDS)


# Cooldown for leaderboard message updates (attachment edits are rate-limit heavy)
# Note: Discord can impose long per-route rate limits (10-20 mins) for attachment edits.
LEADERBOARD_UPDATE_COOLDOWN_SECONDS: float = 1200.0  # 20 minutes, adjust as needed

# Image/font assets
IMAGE_TEMPLATE_PATH: str = os.path.join(os.path.dirname(__file__), "scoreboard_blank1.jpg")
FONT_PATH: str = os.path.join(os.path.dirname(__file__), "scoreboard_font.ttf")


log = logging.getLogger(__name__)


def _utcnow_iso() -> str:
	return datetime.now(timezone.utc).isoformat()


def _safe_int(value: Any) -> Optional[int]:
	try:
		return int(value)
	except Exception:
		return None


def _parse_score(text: str) -> tuple[int, int]:
	# Accept formats like: 3-2, 3:2, 3 2
	cleaned = text.strip().lower().replace(":", "-").replace(" ", "-")
	parts = [p for p in cleaned.split("-") if p]
	if len(parts) != 2:
		raise ValueError("Score must look like 3-2")
	a = _safe_int(parts[0])
	b = _safe_int(parts[1])
	if a is None or b is None:
		raise ValueError("Score must be two numbers")
	if a < 0 or b < 0:
		raise ValueError("Score must be non-negative")
	if a == b:
		raise ValueError("Score cannot be a draw")
	# User examples imply a 5-map series (e.g. 3-2, 4-1, 5-0)
	if a + b != 5:
		raise ValueError("Score must add up to 5 (e.g. 3-2, 4-1, 5-0)")
	return a, b


def _score_options() -> list[tuple[int, int]]:
	# From the submitter clan's perspective
	return [(5, 0), (4, 1), (3, 2), (2, 3), (1, 4), (0, 5)]


def _is_admin_member(member: discord.Member) -> bool:
	if member.guild_permissions.administrator:
		return True
	return any(r.id == ADMIN_ROLE_ID for r in member.roles)


def _admin_app_command_check(interaction: discord.Interaction) -> bool:
	# Safe check wrapper for app_commands decorators.
	cog = interaction.client.get_cog("ScoreboardCog")
	if cog is None:
		return False
	return cog._admin_check(interaction)  # type: ignore[no-any-return]


def _role_name_from_id(role_id: int) -> str:
	for name, rid in CLAN_ROLES.items():
		if rid == role_id:
			return name
	return f"Role {role_id}"


def _build_leaderboard_embed(stats: dict[str, Any]) -> discord.Embed:
	# stats is {role_id(str): {name,w,l,played,for,against}}
	rows: list[tuple[str, dict[str, Any]]] = []
	for rid_str, s in stats.items():
		name = str(s.get("name") or _role_name_from_id(int(rid_str)))
		rows.append((name, s))

	def sort_key(item: tuple[str, dict[str, Any]]):
		s = item[1]
		w = int(s.get("w", 0))
		l = int(s.get("l", 0))
		maps_for = int(s.get("maps_for", 0))
		maps_against = int(s.get("maps_against", 0))
		diff = maps_for - maps_against
		played = int(s.get("played", w + l))
		score = maps_for
		# Primary: score (maps won), then diff, then wins, then fewer losses, then fewer played
		return (score, diff, w, -l, -played)

	rows.sort(key=sort_key, reverse=True)

	header = f"{'#':<3}{'Clan':<22}{'W':>3}{'L':>3}{'MP':>4}{'Score':>7}"
	lines = [header]
	for idx, (name, s) in enumerate(rows, start=1):
		w = int(s.get("w", 0))
		l = int(s.get("l", 0))
		played = int(s.get("played", w + l))
		maps_for = int(s.get("maps_for", 0))
		score = maps_for
		display_name = (name[:19] + "…") if len(name) > 20 else name
		lines.append(f"{idx:<3}{display_name:<22}{w:>3}{l:>3}{played:>4}{score:>7}")

	embed = discord.Embed(
		title="League Leaderboard",
		description="```\n" + "\n".join(lines) + "\n```",
		colour=discord.Colour.blurple(),
	)
	embed.set_footer(text="Score = total maps won")
	return embed


def _sorted_leaderboard_rows(stats: dict[str, Any]) -> list[dict[str, Any]]:
	rows: list[dict[str, Any]] = []
	for rid_str, s in stats.items():
		name = str(s.get("name") or _role_name_from_id(int(rid_str)))
		w = int(s.get("w", 0))
		l = int(s.get("l", 0))
		maps_for = int(s.get("maps_for", 0))
		maps_against = int(s.get("maps_against", 0))
		diff = maps_for - maps_against
		rows.append(
			{
				"name": name,
				"score": maps_for,
				"w": w,
				"l": l,
				"diff": diff,
			},
		)

	rows.sort(key=lambda r: (r["score"], r["diff"], r["w"], -r["l"], r["name"].lower()), reverse=True)
	return rows


def _build_leaderboard_text(stats: dict[str, Any]) -> str:
	rows = _sorted_leaderboard_rows(stats)
	header = f"{'#':<3}{'Clan':<16}{'Score':>6}{'W':>4}{'L':>4}"
	lines = [header]
	for idx, r in enumerate(rows, start=1):
		name = str(r["name"])
		name = (name[:13] + "…") if len(name) > 14 else name
		lines.append(f"{idx:<3}{name:<16}{int(r['score']):>6}{int(r['w']):>4}{int(r['l']):>4}")
	return "```\n" + "\n".join(lines) + "\n```"


def _build_scoreboard_embed() -> discord.Embed:
	embed = discord.Embed(
		title="Submit Match Scores",
		description="- Click the button below to submit a match result for validation by the opposing clan. \n - It will then post a submission in <#1462382488784470181> for the opposing side to confirm \n - When confirmed the league table updates in <#1462384116376014911>, this may queue and take up to 5-10 mins \n - Make sure you have linked the <#1462384116376014911> announcement channel as a feed in your clan discord or just copy and paste the table if you prefer",
		colour=discord.Colour.green(),
	)
	return embed


@dataclass
class PendingMatch:
	match_id: str
	submitter_id: int
	submitter_clan_role_id: int
	opponent_clan_role_id: int
	submitter_score: int
	opponent_score: int
	created_at: str
	validation_message_id: Optional[int] = None
	status: str = "pending"  # pending | confirmed | disputed
	confirmed_by_id: Optional[int] = None
	confirmed_at: Optional[str] = None

	def to_dict(self) -> dict[str, Any]:
		return {
			"match_id": self.match_id,
			"submitter_id": self.submitter_id,
			"submitter_clan_role_id": self.submitter_clan_role_id,
			"opponent_clan_role_id": self.opponent_clan_role_id,
			"submitter_score": self.submitter_score,
			"opponent_score": self.opponent_score,
			"created_at": self.created_at,
			"validation_message_id": self.validation_message_id,
			"status": self.status,
			"confirmed_by_id": self.confirmed_by_id,
			"confirmed_at": self.confirmed_at,
		}

	@staticmethod
	def from_dict(d: dict[str, Any]) -> "PendingMatch":
		return PendingMatch(
			match_id=str(d["match_id"]),
			submitter_id=int(d["submitter_id"]),
			submitter_clan_role_id=int(d["submitter_clan_role_id"]),
			opponent_clan_role_id=int(d["opponent_clan_role_id"]),
			submitter_score=int(d["submitter_score"]),
			opponent_score=int(d["opponent_score"]),
			created_at=str(d.get("created_at") or _utcnow_iso()),
			validation_message_id=_safe_int(d.get("validation_message_id")),
			status=str(d.get("status") or "pending"),
			confirmed_by_id=_safe_int(d.get("confirmed_by_id")),
			confirmed_at=d.get("confirmed_at"),
		)


class ScoreboardStore:
	def __init__(self) -> None:
		self._path = data_path("scoreboard.json")
		self._lock = asyncio.Lock()
		self.data: dict[str, Any] = {}

	async def load(self) -> None:
		async with self._lock:
			if os.path.exists(self._path):
				try:
					with open(self._path, "r", encoding="utf-8") as f:
						self.data = json.load(f)
				except Exception:
					log.exception("Failed reading %s, starting fresh", self._path)
					self.data = {}

			self.data.setdefault("scoreboard_message_id", None)
			self.data.setdefault("leaderboard_message_id", None)
			self.data.setdefault("last_result", None)
			self.data.setdefault("clan_stats", {})
			self.data.setdefault("pending_matches", {})  # match_id -> match dict
			self.data.setdefault("pending_by_validation_message", {})  # message_id(str) -> match_id
			await self._ensure_clans_locked()

	async def save(self) -> None:
		async with self._lock:
			tmp = self._path + ".tmp"
			with open(tmp, "w", encoding="utf-8") as f:
				json.dump(self.data, f, indent=2)
			os.replace(tmp, self._path)

	async def _ensure_clans_locked(self) -> None:
		stats: dict[str, Any] = self.data.setdefault("clan_stats", {})
		allowed_keys = {str(role_id) for role_id in CLAN_ROLES.values()}

		# Remove any clans that are no longer configured (e.g. renamed/removed teams).
		for key in list(stats.keys()):
			if key not in allowed_keys:
				stats.pop(key, None)

		# Ensure all configured clans exist and normalize fields.
		for clan_name, role_id in CLAN_ROLES.items():
			key = str(role_id)
			entry = stats.get(key)
			if not isinstance(entry, dict):
				entry = {}
				stats[key] = entry

			# Back-compat for older field names.
			if "for" in entry and "maps_for" not in entry:
				entry["maps_for"] = entry.get("for")
			if "against" in entry and "maps_against" not in entry:
				entry["maps_against"] = entry.get("against")

			entry["name"] = clan_name
			entry.setdefault("w", 0)
			entry.setdefault("l", 0)
			entry.setdefault("played", int(entry.get("w", 0)) + int(entry.get("l", 0)))
			entry.setdefault("maps_for", 0)
			entry.setdefault("maps_against", 0)

		# If the latest-result line references a removed clan, clear it so it won't render.
		last = self.data.get("last_result")
		if isinstance(last, dict):
			allowed_names = set(CLAN_ROLES.keys())
			a_name = str(last.get("a_name") or "")
			b_name = str(last.get("b_name") or "")
			if a_name not in allowed_names or b_name not in allowed_names:
				self.data["last_result"] = None

	async def ensure_clans(self) -> None:
		async with self._lock:
			await self._ensure_clans_locked()
		await self.save()

	async def add_pending_match(self, match: PendingMatch) -> None:
		async with self._lock:
			self.data["pending_matches"][match.match_id] = match.to_dict()
		await self.save()

	async def link_validation_message(self, match_id: str, validation_message_id: int) -> None:
		async with self._lock:
			m = self.data["pending_matches"].get(match_id)
			if not m:
				return
			m["validation_message_id"] = int(validation_message_id)
			self.data["pending_by_validation_message"][str(validation_message_id)] = match_id
		await self.save()

	async def get_match(self, match_id: str) -> Optional[PendingMatch]:
		async with self._lock:
			d = self.data.get("pending_matches", {}).get(match_id)
			if not d:
				return None
			return PendingMatch.from_dict(d)

	async def get_match_by_validation_message(self, message_id: int) -> Optional[PendingMatch]:
		async with self._lock:
			match_id = self.data.get("pending_by_validation_message", {}).get(str(message_id))
			if not match_id:
				return None
			d = self.data.get("pending_matches", {}).get(match_id)
			if not d:
				return None
			return PendingMatch.from_dict(d)

	async def mark_disputed(self, match_id: str) -> None:
		async with self._lock:
			d = self.data.get("pending_matches", {}).get(match_id)
			if not d:
				return
			d["status"] = "disputed"
		await self.save()

	async def confirm_match(self, match_id: str, confirmed_by_id: int) -> Optional[PendingMatch]:
		async with self._lock:
			d = self.data.get("pending_matches", {}).get(match_id)
			if not d:
				return None
			d["status"] = "confirmed"
			d["confirmed_by_id"] = int(confirmed_by_id)
			d["confirmed_at"] = _utcnow_iso()
			match = PendingMatch.from_dict(d)

			# Apply to leaderboard
			stats: dict[str, Any] = self.data.setdefault("clan_stats", {})

			a_key = str(match.submitter_clan_role_id)
			b_key = str(match.opponent_clan_role_id)
			if a_key not in stats:
				stats[a_key] = {
					"name": _role_name_from_id(match.submitter_clan_role_id),
					"w": 0,
					"l": 0,
					"played": 0,
					"maps_for": 0,
					"maps_against": 0,
				}
			if b_key not in stats:
				stats[b_key] = {
					"name": _role_name_from_id(match.opponent_clan_role_id),
					"w": 0,
					"l": 0,
					"played": 0,
					"maps_for": 0,
					"maps_against": 0,
				}

			a = stats[a_key]
			b = stats[b_key]

			a["played"] = int(a.get("played", 0)) + 1
			b["played"] = int(b.get("played", 0)) + 1

			a["maps_for"] = int(a.get("maps_for", 0)) + match.submitter_score
			a["maps_against"] = int(a.get("maps_against", 0)) + match.opponent_score
			b["maps_for"] = int(b.get("maps_for", 0)) + match.opponent_score
			b["maps_against"] = int(b.get("maps_against", 0)) + match.submitter_score

			if match.submitter_score > match.opponent_score:
				a["w"] = int(a.get("w", 0)) + 1
				b["l"] = int(b.get("l", 0)) + 1
			else:
				b["w"] = int(b.get("w", 0)) + 1
				a["l"] = int(a.get("l", 0)) + 1

			self.data["clan_stats"] = stats
			self.data["last_result"] = {
				"match_id": match.match_id,
				"a_name": _role_name_from_id(match.submitter_clan_role_id),
				"b_name": _role_name_from_id(match.opponent_clan_role_id),
				"a_score": match.submitter_score,
				"b_score": match.opponent_score,
				"at": _utcnow_iso(),
			}

		await self.save()
		return match


def _member_clan_role_id(member: discord.Member) -> Optional[int]:
	clan_role_ids = set(CLAN_ROLES.values())
	hits = [r.id for r in member.roles if r.id in clan_role_ids]
	if len(hits) != 1:
		return None
	return hits[0]


class OpponentSelect(discord.ui.Select):
	def __init__(self, submitter_clan_role_id: int):
		options = []
		for clan_name, role_id in CLAN_ROLES.items():
			if role_id == submitter_clan_role_id:
				continue
			options.append(discord.SelectOption(label=clan_name, value=str(role_id)))
		super().__init__(
			placeholder="Select the opposing clan…",
			min_values=1,
			max_values=1,
			options=options[:25],
			custom_id="scoreboard:opponent_select",
		)

	async def callback(self, interaction: discord.Interaction):
		view: "SubmitFlowView" = self.view  # type: ignore[assignment]
		selected_value = str(self.values[0])
		view.opponent_clan_role_id = int(selected_value)

		# Make the selected opponent "stick" visually in the dropdown.
		selected_label: Optional[str] = None
		refreshed_options: list[discord.SelectOption] = []
		for opt in self.options:
			is_default = opt.value == selected_value
			if is_default:
				selected_label = opt.label
			refreshed_options.append(
				discord.SelectOption(
					label=opt.label,
					value=str(opt.value),
					default=is_default,
				)
			)
		self.options = refreshed_options
		self.placeholder = selected_label or "Select the opposing clan…"

		view._refresh_score_options()
		await interaction.response.edit_message(view=view)


class ScoreSelect(discord.ui.Select):
	def __init__(self, submitter_clan_role_id: int):
		self.submitter_clan_role_id = submitter_clan_role_id
		super().__init__(
			placeholder="Select the match score…",
			min_values=1,
			max_values=1,
			options=[discord.SelectOption(label="Pick an opponent first", value="0-5")],
			disabled=True,
			custom_id="scoreboard:score_select",
		)

	def set_matchup(self, opponent_clan_role_id: Optional[int]) -> None:
		# Preserve current selection if possible.
		selected_value = None
		if self.view is not None and hasattr(self.view, "selected_score"):
			selected_value = getattr(self.view, "selected_score")

		if opponent_clan_role_id is None:
			self.disabled = True
			self.placeholder = "Select the match score…"
			self.options = [discord.SelectOption(label="Pick an opponent first", value="0-5", default=True)]
			return
		a_name = _role_name_from_id(self.submitter_clan_role_id)
		b_name = _role_name_from_id(opponent_clan_role_id)
		self.disabled = False
		options: list[discord.SelectOption] = []
		selected_label: Optional[str] = None
		for a, b in _score_options():
			value = f"{a}-{b}"
			label = f"{a_name} - {b_name} ({a}-{b})"
			is_default = selected_value == value
			if is_default:
				selected_label = label
			options.append(discord.SelectOption(label=label, value=value, default=is_default))
		self.options = options
		self.placeholder = selected_label or "Select the match score…"

	async def callback(self, interaction: discord.Interaction):
		view: "SubmitFlowView" = self.view  # type: ignore[assignment]
		view.selected_score = str(self.values[0])
		# Make the selected score "stick" visually.
		self.set_matchup(view.opponent_clan_role_id)
		view._refresh_submit_button_state()
		await interaction.response.edit_message(view=view)


class SubmitFlowView(discord.ui.View):
	def __init__(self, submitter_id: int, submitter_clan_role_id: int):
		super().__init__(timeout=300)
		self.submitter_id = submitter_id
		self.submitter_clan_role_id = submitter_clan_role_id
		self.opponent_clan_role_id: Optional[int] = None
		self.selected_score: Optional[str] = None

		self.add_item(OpponentSelect(submitter_clan_role_id))
		self.score_select = ScoreSelect(submitter_clan_role_id)
		self.add_item(self.score_select)

	def _refresh_score_options(self) -> None:
		self.score_select.set_matchup(self.opponent_clan_role_id)
		self.selected_score = None
		self._refresh_submit_button_state()

	def _refresh_submit_button_state(self) -> None:
		for child in self.children:
			if isinstance(child, discord.ui.Button) and child.custom_id == "scoreboard:submit_result":
				child.disabled = not (self.opponent_clan_role_id is not None and self.selected_score is not None)

	@discord.ui.button(label="Submit Result", style=discord.ButtonStyle.success, disabled=True, custom_id="scoreboard:submit_result")
	async def submit_result(self, interaction: discord.Interaction, button: discord.ui.Button):
		if not interaction.guild or not isinstance(interaction.user, discord.Member):
			await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
			return
		if interaction.user.id != self.submitter_id:
			await interaction.response.send_message("This submit flow isn’t yours.", ephemeral=True)
			return
		if self.opponent_clan_role_id is None or self.selected_score is None:
			await interaction.response.send_message("Pick an opposing clan and a score first.", ephemeral=True)
			return

		try:
			a, b = _parse_score(self.selected_score)
		except ValueError as e:
			await interaction.response.send_message(str(e), ephemeral=True)
			return

		await interaction.response.defer(ephemeral=True, thinking=True)
		cog: "ScoreboardCog" = interaction.client.get_cog("ScoreboardCog")  # type: ignore[assignment]
		if cog is None:
			await interaction.followup.send("Scoreboard cog is not loaded.", ephemeral=True)
			return

		match_id = uuid.uuid4().hex[:12]
		match = PendingMatch(
			match_id=match_id,
			submitter_id=interaction.user.id,
			submitter_clan_role_id=self.submitter_clan_role_id,
			opponent_clan_role_id=self.opponent_clan_role_id,
			submitter_score=a,
			opponent_score=b,
			created_at=_utcnow_iso(),
		)

		log.info(
			"Result submitted match_id=%s %s vs %s score=%s-%s by user_id=%s",
			match_id,
			_role_name_from_id(self.submitter_clan_role_id),
			_role_name_from_id(self.opponent_clan_role_id),
			a,
			b,
			interaction.user.id,
		)

		await cog.store.add_pending_match(match)
		validation_message = await cog.post_validation_message(interaction.guild, match)
		if validation_message:
			await cog.store.link_validation_message(match.match_id, validation_message.id)
			log.info(
				"Validation posted match_id=%s message_id=%s channel_id=%s",
				match_id,
				validation_message.id,
				getattr(validation_message.channel, "id", None),
			)
		else:
			log.warning("Validation NOT posted match_id=%s", match_id)

		await interaction.followup.send("Submitted! A validation message has been posted.", ephemeral=True)


class ScoreboardMainView(discord.ui.View):
	def __init__(self):
		super().__init__(timeout=None)  # persistent

	@discord.ui.button(
		label="Submit Scores",
		style=discord.ButtonStyle.success,
		custom_id="scoreboard:submit_scores",
	)
	async def submit_scores(self, interaction: discord.Interaction, button: discord.ui.Button):
		if not interaction.guild or not isinstance(interaction.user, discord.Member):
			await interaction.response.send_message("Use this in a server.", ephemeral=True)
			return
		clan_role_id = _member_clan_role_id(interaction.user)
		if clan_role_id is None:
			await interaction.response.send_message(
				"You must have exactly one clan role to submit scores.",
				ephemeral=True,
			)
			return
		if len(CLAN_ROLES) < 2:
			await interaction.response.send_message(
				"No clans configured. Fill in CLAN_ROLES at the top of the cog.",
				ephemeral=True,
			)
			return

		embed = discord.Embed(
			title="Submit a result",
			description="Select the opposing clan and score, then click **Submit Result**.",
			colour=discord.Colour.blurple(),
		)
		await interaction.response.send_message(
			embed=embed,
			view=SubmitFlowView(interaction.user.id, clan_role_id),
			ephemeral=True,
		)


class ValidationView(discord.ui.View):
	def __init__(self, match_id: str):
		super().__init__(timeout=None)  # persistent
		self.match_id = match_id

		confirm_button = discord.ui.Button(
			label="Confirm Result",
			style=discord.ButtonStyle.success,
			custom_id=f"scoreboard:confirm:{match_id}",
		)

		async def confirm_callback(interaction: discord.Interaction):
			cog: "ScoreboardCog" = interaction.client.get_cog("ScoreboardCog")  # type: ignore[assignment]
			if cog is None:
				await interaction.response.send_message("Scoreboard cog is not loaded.", ephemeral=True)
				return
			await cog.handle_confirm(interaction, self.match_id)

		confirm_button.callback = confirm_callback
		self.add_item(confirm_button)

		dispute_button = discord.ui.Button(
			label="Dispute",
			style=discord.ButtonStyle.danger,
			custom_id=f"scoreboard:dispute:{match_id}",
		)

		async def dispute_callback(interaction: discord.Interaction):
			cog: "ScoreboardCog" = interaction.client.get_cog("ScoreboardCog")  # type: ignore[assignment]
			if cog is None:
				await interaction.response.send_message("Scoreboard cog is not loaded.", ephemeral=True)
				return
			await cog.handle_dispute(interaction, self.match_id)

		dispute_button.callback = dispute_callback
		self.add_item(dispute_button)


class ScoreboardCog(commands.Cog):
	"""Score submission + validation + leaderboard."""

	def __init__(self, bot: commands.Bot):
		self.bot = bot
		self.store = ScoreboardStore()
		self._did_guild_sync = False
		self._did_initial_ensure = False
		self._leaderboard_lock = asyncio.Lock()
		self._scoreboard_lock = asyncio.Lock()
		self._last_leaderboard_update_ts: float = 0.0
		# Leaderboard update telemetry
		self._leaderboard_update_task: Optional[asyncio.Task] = None
		self._leaderboard_update_pending: bool = False
		self._leaderboard_update_request_count: int = 0
		self._leaderboard_rate_limited_until_ts: float = 0.0

	def _leaderboard_eta_seconds(self) -> float:
		"""Best-effort estimate for when the leaderboard can next visibly update."""
		now = asyncio.get_running_loop().time()
		eta = 0.0
		cooldown = float(LEADERBOARD_UPDATE_COOLDOWN_SECONDS)
		if self._last_leaderboard_update_ts and (now - self._last_leaderboard_update_ts) < cooldown:
			eta = max(eta, cooldown - (now - self._last_leaderboard_update_ts))
		if self._leaderboard_rate_limited_until_ts and now < self._leaderboard_rate_limited_until_ts:
			eta = max(eta, self._leaderboard_rate_limited_until_ts - now)
		return max(0.0, float(eta))

	async def cog_load(self) -> None:
		await self.store.load()
		await self.store.ensure_clans()
		# Register persistent base view
		self.bot.add_view(ScoreboardMainView())
		# Re-register persistent validation views for pending matches
		pending = self.store.data.get("pending_matches", {})
		for match_id, d in pending.items():
			try:
				match = PendingMatch.from_dict(d)
			except Exception:
				continue
			if match.status == "pending":
				self.bot.add_view(ValidationView(match_id))

	@commands.Cog.listener()
	async def on_ready(self):
		# Guild-scoped slash command sync (instant availability in this guild).
		if not self._did_guild_sync and GUILD_ID:
			try:
				await self.bot.tree.sync(guild=discord.Object(id=GUILD_ID))
				self._did_guild_sync = True
			except Exception:
				log.exception("Failed syncing scoreboard commands to guild %s", GUILD_ID)
				# Don't flip the flag; we'll retry next on_ready.
				return

		# on_ready can fire multiple times (reconnects). Only do the auto-repair once
		# per process start to avoid accidental re-posting.
		if self._did_initial_ensure:
			return
		self._did_initial_ensure = True

		# Post/repair the main scoreboard message and leaderboard message.
		await self.ensure_scoreboard_message()
		await self.ensure_leaderboard_message()

	async def ensure_scoreboard_message(self) -> None:
		async with self._scoreboard_lock:
			if SCOREBOARD_CHANNEL_ID == 0:
				return
			channel = self.bot.get_channel(SCOREBOARD_CHANNEL_ID)
			if channel is None:
				try:
					channel = await self.bot.fetch_channel(SCOREBOARD_CHANNEL_ID)
				except Exception:
					log.exception("Failed to fetch scoreboard channel")
					return
			if not isinstance(channel, discord.TextChannel):
				return

			message_id = self.store.data.get("scoreboard_message_id")
			embed = _build_scoreboard_embed()
			view = ScoreboardMainView()

			if message_id:
				try:
					msg = await channel.fetch_message(int(message_id))
					await msg.edit(embed=embed, view=view)
					return
				except discord.NotFound:
					# Message was deleted; clear the stored ID and re-send.
					self.store.data["scoreboard_message_id"] = None
					await self.store.save()
				except Exception:
					# Don't spam new messages on transient failures.
					log.exception("Could not edit existing scoreboard message")
					return

			msg = await channel.send(embed=embed, view=view)
			self.store.data["scoreboard_message_id"] = msg.id
			await self.store.save()

	async def ensure_leaderboard_message(self) -> None:
		# Debounce/coalesce leaderboard updates to avoid rate limits.
		self._leaderboard_update_request_count += 1
		eta = 0.0
		try:
			eta = self._leaderboard_eta_seconds()
		except Exception:
			eta = 0.0

		if self._leaderboard_update_task is not None and not self._leaderboard_update_task.done():
			# Only one extra run is queued; multiple requests coalesce into that.
			self._leaderboard_update_pending = True
			log.info(
				"Leaderboard update requested (coalesced). queued=1 eta~%.0fs requests=%s",
				eta,
				self._leaderboard_update_request_count,
			)
			return

		self._leaderboard_update_pending = False
		log.info(
			"Leaderboard update queued. queued=0 eta~%.0fs requests=%s",
			eta,
			self._leaderboard_update_request_count,
		)
		self._leaderboard_update_task = asyncio.create_task(self._run_leaderboard_update())

	async def _run_leaderboard_update(self):
		while True:
			async with self._leaderboard_lock:
				now = asyncio.get_running_loop().time()
				# Enforce cooldown between updates
				cooldown = float(LEADERBOARD_UPDATE_COOLDOWN_SECONDS)
				if self._last_leaderboard_update_ts and (now - self._last_leaderboard_update_ts) < cooldown:
					sleep_time = cooldown - (now - self._last_leaderboard_update_ts)
					log.info("Leaderboard update cooling down for %.1fs", sleep_time)
					await asyncio.sleep(sleep_time)

				if LEADERBOARD_CHANNEL_ID == 0:
					return
				channel = self.bot.get_channel(LEADERBOARD_CHANNEL_ID)
				if channel is None:
					try:
						channel = await self.bot.fetch_channel(LEADERBOARD_CHANNEL_ID)
					except Exception:
						log.exception("Failed to fetch leaderboard channel")
						return
				if not isinstance(channel, discord.TextChannel):
					return

				message_id = self.store.data.get("leaderboard_message_id")
				image_path = await self._render_scoreboard_image()
				filename = os.path.basename(image_path)

				def _make_file() -> discord.File:
					# A discord.File can be consumed/closed by a send/edit attempt.
					# Create a fresh instance each time we retry.
					return discord.File(image_path, filename=filename)

				file = _make_file()
				embed = discord.Embed(
					title="League Scoreboard",
					colour=discord.Colour.blurple(),
					timestamp=datetime.now(timezone.utc),
				)
				embed.set_image(url=f"attachment://{filename}")
				content = ""

				try:
					if message_id:
						try:
							msg = await channel.fetch_message(int(message_id))
							await msg.edit(content=content, embed=embed, attachments=[file])
						except discord.NotFound:
							# Message was deleted; clear the stored ID and re-send.
							self.store.data["leaderboard_message_id"] = None
							await self.store.save()
							file = _make_file()
							msg = await channel.send(content=content, embed=embed, file=file)
							self.store.data["leaderboard_message_id"] = msg.id
							await self.store.save()
					else:
						msg = await channel.send(content=content, embed=embed, file=file)
						self.store.data["leaderboard_message_id"] = msg.id
						await self.store.save()

					# Only mark the timestamp after a successful update.
					self._last_leaderboard_update_ts = asyncio.get_running_loop().time()
					self._leaderboard_rate_limited_until_ts = 0.0
					log.info(
						"Leaderboard updated message_id=%s requests=%s",
						self.store.data.get("leaderboard_message_id"),
						self._leaderboard_update_request_count,
					)
				except discord.HTTPException as e:
					if e.status == 429:
						retry_after = getattr(e, 'retry_after', 60)
						self._leaderboard_rate_limited_until_ts = asyncio.get_running_loop().time() + float(retry_after)
						eta2 = 0.0
						try:
							eta2 = self._leaderboard_eta_seconds()
						except Exception:
							eta2 = float(retry_after)
						log.warning(f"Rate limited by Discord, retrying leaderboard update in {retry_after} seconds.")
						log.warning(
							"Leaderboard delayed by rate limit. eta~%.0fs pending=%s requests=%s",
							eta2,
							self._leaderboard_update_pending,
							self._leaderboard_update_request_count,
						)
						await asyncio.sleep(retry_after)
						continue
					else:
						log.exception("HTTPException during leaderboard update")
						return
				except Exception:
					log.exception("Could not update leaderboard message")
					return

			# If another update was requested during the cooldown, run again.
			if getattr(self, '_leaderboard_update_pending', False):
				self._leaderboard_update_pending = False
				log.info("Leaderboard update running again (coalesced pending request)")
				continue
			break

	async def _render_scoreboard_image(self) -> str:
		"""Render the scoreboard onto the provided template image."""
		from PIL import Image, ImageDraw, ImageFont  # pillow

		if os.path.exists(IMAGE_TEMPLATE_PATH):
			base = Image.open(IMAGE_TEMPLATE_PATH).convert("RGBA")
		else:
			# Fallback so the bot keeps working even if the template is missing.
			log.warning("Scoreboard template image not found at %s", IMAGE_TEMPLATE_PATH)
			base = Image.new("RGBA", (1080, 1920), (16, 18, 24, 255))

		w, h = base.size
		draw = ImageDraw.Draw(base)

		def _clamp(n: int, lo: int, hi: int) -> int:
			return max(lo, min(hi, n))

		def _truetype(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
			try:
				return ImageFont.truetype(FONT_PATH, size)
			except Exception:
				return ImageFont.load_default()

		def _fit_font(text: str, max_width: int, start_size: int, min_size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
			# Try to shrink font until it fits. If we fall back to load_default() we can't resize further.
			for size in range(start_size, min_size - 1, -2):
				font = _truetype(size)
				bbox = draw.textbbox((0, 0), text, font=font)
				if (bbox[2] - bbox[0]) <= max_width:
					return font
			return _truetype(min_size)

		# No outline; write plain text onto the template
		text_fill = (255, 255, 255, 255)

		margin = int(w * 0.06)
		# Areas (relative). These should roughly match scoreboard_blank1.jpg's boxes.
		result_top = int(h * 0.08)
		result_bottom = int(h * 0.30)
		table_top = int(h * 0.36)
		table_bottom = int(h * 0.94)

		# Latest result text (centered)
		last = self.store.data.get("last_result")
		center_y = result_top + ((result_bottom - result_top) // 2)
		if isinstance(last, dict):
			a_name = str(last.get("a_name", "") or "")
			b_name = str(last.get("b_name", "") or "")
			a_score = int(last.get("a_score", 0))
			b_score = int(last.get("b_score", 0))
			score_text = f"{a_score} - {b_score}"

			# Score centered, clans anchored left/right so they don't crowd the numbers.
			max_score_w = int((w - 2 * margin) * 0.45)
			# Slightly smaller than before so long names/scores have more breathing room.
			score_font = _fit_font(score_text, max_score_w, start_size=_clamp(int(h * 0.070), 28, 110), min_size=18)
			clan_font = _fit_font(a_name if len(a_name) >= len(b_name) else b_name, int((w - 2 * margin) * 0.35), start_size=_clamp(int(h * 0.066), 26, 100), min_size=16)

			left_x = margin + int(w * 0.08)
			right_x = w - margin - int(w * 0.08)
			draw.text((left_x, center_y), a_name, font=clan_font, fill=text_fill, anchor="lm")
			draw.text((w // 2, center_y), score_text, font=score_font, fill=text_fill, anchor="mm")
			draw.text((right_x, center_y), b_name, font=clan_font, fill=text_fill, anchor="rm")
		else:
			result_text = "NO RESULTS YET"
			max_result_w = (w - 2 * margin) - 40
			result_font = _fit_font(result_text, max_result_w, start_size=_clamp(int(h * 0.070), 28, 110), min_size=18)
			draw.text((w // 2, center_y), result_text, font=result_font, fill=text_fill, anchor="mm")

		# Leaderboard table (auto-fit all teams)
		rows = _sorted_leaderboard_rows(self.store.data.get("clan_stats", {}))
		row_count = max(1, len(rows))
		usable_top = table_top + int(h * 0.06)
		usable_bottom = table_bottom - int(h * 0.03)
		# +1 for header row
		row_h = max(16, int((usable_bottom - usable_top) / (row_count + 1)))

		header_font = _truetype(_clamp(int(row_h * 0.78), 12, 44))
		row_font = _truetype(_clamp(int(row_h * 0.78), 12, 42))

		col_idx = margin + int(w * 0.02)
		col_name = margin + int(w * 0.12)
		# Evenly space numeric columns in a fixed right-side band
		# Widen the numeric band so Score / W / L / MP have more breathing room.
		# (This also nudges Score a bit further left.)
		numeric_right = w - margin - int(w * 0.07)
		numeric_left = w - margin - int(w * 0.58)
		segment = max(1, (numeric_right - numeric_left) / 4)
		col_score = int(numeric_left + segment * 0.5)
		col_w = int(numeric_left + segment * 1.5)
		col_l = int(numeric_left + segment * 2.5)
		col_mp = int(numeric_left + segment * 3.5)

		header_y = usable_top
		# Use anchors so columns align consistently
		draw.text((col_idx, header_y), "#", font=header_font, fill=text_fill, anchor="la")
		draw.text((col_name, header_y), "CLAN", font=header_font, fill=text_fill, anchor="la")
		draw.text((col_score, header_y), "SCORE", font=header_font, fill=text_fill, anchor="ma")
		draw.text((col_w, header_y), "W", font=header_font, fill=text_fill, anchor="ma")
		draw.text((col_l, header_y), "L", font=header_font, fill=text_fill, anchor="ma")
		draw.text((col_mp, header_y), "MP", font=header_font, fill=text_fill, anchor="ma")

		for i, r in enumerate(rows, start=1):
			y = header_y + row_h * i
			if y + row_h > usable_bottom + 2:
				break
			name = str(r["name"])
			# keep names from spilling
			if len(name) > 12:
				name = name[:11] + "…"
			draw.text((col_idx, y), str(i), font=row_font, fill=text_fill, anchor="la")
			draw.text((col_name, y), name, font=row_font, fill=text_fill, anchor="la")
			draw.text((col_score, y), str(int(r["score"])), font=row_font, fill=text_fill, anchor="ma")
			draw.text((col_w, y), str(int(r["w"])), font=row_font, fill=text_fill, anchor="ma")
			draw.text((col_l, y), str(int(r["l"])), font=row_font, fill=text_fill, anchor="ma")
			played = int(r.get("w", 0)) + int(r.get("l", 0))
			draw.text((col_mp, y), str(played), font=row_font, fill=text_fill, anchor="ma")

		out_path = data_path("scoreboard_rendered.png")
		base.save(out_path, format="PNG")
		return out_path

	async def post_validation_message(self, guild: discord.Guild, match: PendingMatch) -> Optional[discord.Message]:
		if VALIDATION_CHANNEL_ID == 0:
			return None
		channel = guild.get_channel(VALIDATION_CHANNEL_ID)
		if channel is None:
			try:
				channel = await guild.fetch_channel(VALIDATION_CHANNEL_ID)
			except Exception:
				log.exception("Failed to fetch validation channel")
				return None
		if not isinstance(channel, discord.TextChannel):
			return None

		a_name = _role_name_from_id(match.submitter_clan_role_id)
		b_name = _role_name_from_id(match.opponent_clan_role_id)
		embed = discord.Embed(
			title="Match Result Submitted",
			description=(
				f"**{a_name}** vs **{b_name}**\n"
				f"Proposed score: **{match.submitter_score}-{match.opponent_score}**\n\n"
				f"Opposing clan should confirm below."
			),
			colour=discord.Colour.orange(),
			timestamp=datetime.now(timezone.utc),
		)
		embed.add_field(name="Submitted by", value=f"<@{match.submitter_id}>", inline=False)
		embed.set_footer(text=f"Match ID: {match.match_id}")

		opponent_role_mention = f"<@&{match.opponent_clan_role_id}>"
		msg = await channel.send(
			content=f"Validation required from {opponent_role_mention}",
			embed=embed,
			view=ValidationView(match.match_id),
			allowed_mentions=discord.AllowedMentions(roles=True),
		)
		return msg

	async def handle_confirm(self, interaction: discord.Interaction, match_id: str) -> None:
		if not interaction.guild or not isinstance(interaction.user, discord.Member):
			await interaction.response.send_message("Use this in a server.", ephemeral=True)
			return
		await interaction.response.defer(ephemeral=True, thinking=True)
		match = await self.store.get_match(match_id)
		if match is None:
			await interaction.followup.send("This match can’t be found.", ephemeral=True)
			return
		if match.status != "pending":
			await interaction.followup.send(f"This match is already {match.status}.", ephemeral=True)
			return

		# Only the opposing clan role can confirm.
		opponent_role = interaction.guild.get_role(match.opponent_clan_role_id)
		if opponent_role is None or opponent_role not in interaction.user.roles:
			await interaction.followup.send(
				"Only a member of the opposing clan can confirm this result.",
				ephemeral=True,
			)
			return

		confirmed = await self.store.confirm_match(match_id, interaction.user.id)
		if confirmed is None:
			await interaction.followup.send("Failed to confirm match.", ephemeral=True)
			return

		log.info(
			"Result confirmed match_id=%s by user_id=%s -> queue leaderboard update",
			match_id,
			interaction.user.id,
		)

		# Update validation message
		try:
			if interaction.message:
				new_embed = interaction.message.embeds[0] if interaction.message.embeds else None
				if new_embed:
					new_embed = new_embed.copy()
					new_embed.colour = discord.Colour.green()
					new_embed.add_field(name="Confirmed by", value=f"<@{interaction.user.id}>", inline=False)
				await interaction.message.edit(embed=new_embed, view=None)
		except Exception:
			log.exception("Failed updating validation message")

		await self.ensure_leaderboard_message()
		# Leaderboard message is now the combined scoreboard image.

		await interaction.followup.send("Confirmed and leaderboard updated.", ephemeral=True)

	async def handle_dispute(self, interaction: discord.Interaction, match_id: str) -> None:
		if not interaction.guild or not isinstance(interaction.user, discord.Member):
			await interaction.response.send_message("Use this in a server.", ephemeral=True)
			return
		await interaction.response.defer(ephemeral=True, thinking=True)
		match = await self.store.get_match(match_id)
		if match is None:
			await interaction.followup.send("This match can’t be found.", ephemeral=True)
			return
		if match.status != "pending":
			await interaction.followup.send(f"This match is already {match.status}.", ephemeral=True)
			return
		opponent_role = interaction.guild.get_role(match.opponent_clan_role_id)
		if opponent_role is None or opponent_role not in interaction.user.roles:
			await interaction.followup.send(
				"Only a member of the opposing clan can dispute this result.",
				ephemeral=True,
			)
			return
		await self.store.mark_disputed(match_id)
		try:
			if interaction.message:
				new_embed = interaction.message.embeds[0] if interaction.message.embeds else None
				if new_embed:
					new_embed = new_embed.copy()
					new_embed.colour = discord.Colour.red()
					new_embed.add_field(name="Disputed by", value=f"<@{interaction.user.id}>", inline=False)
				await interaction.message.edit(embed=new_embed, view=None)
		except Exception:
			log.exception("Failed updating disputed message")
		await interaction.followup.send("Marked as disputed.", ephemeral=True)

	# (No Pillow rendering: leaderboard uses the static IMAGE_TEMPLATE_PATH)


	def _admin_check(self, interaction: discord.Interaction) -> bool:
		if not isinstance(interaction.user, discord.Member):
			return False
		return _is_admin_member(interaction.user)


	@app_commands.guilds(discord.Object(id=GUILD_ID))
	@app_commands.command(name="scoreboard_admin_edit_clan", description="Admin: edit a clan's leaderboard values")
	@app_commands.check(_admin_app_command_check)
	async def scoreboard_admin_edit_clan(
		self,
		interaction: discord.Interaction,
		clan_role: discord.Role,
		score: int,
		wins: int,
		losses: int,
	):
		await interaction.response.defer(ephemeral=True)
		if clan_role.id not in set(CLAN_ROLES.values()):
			await interaction.followup.send("That role is not a configured clan role.", ephemeral=True)
			return
		if score < 0 or wins < 0 or losses < 0:
			await interaction.followup.send("Score/Wins/Losses must be non-negative.", ephemeral=True)
			return

		key = str(clan_role.id)
		stats = self.store.data.setdefault("clan_stats", {})
		s = stats.setdefault(
			key,
			{"name": clan_role.name, "w": 0, "l": 0, "played": 0, "maps_for": 0, "maps_against": 0},
		)
		s["name"] = s.get("name") or clan_role.name
		s["maps_for"] = int(score)
		s["w"] = int(wins)
		s["l"] = int(losses)
		s["played"] = int(wins) + int(losses)
		self.store.data["clan_stats"] = stats
		await self.store.save()
		await self.ensure_leaderboard_message()
		await interaction.followup.send(f"Updated {clan_role.name}: score={score}, W={wins}, L={losses}", ephemeral=True)


	@app_commands.guilds(discord.Object(id=GUILD_ID))
	@app_commands.command(name="scoreboard_admin_edit_match", description="Admin: edit a confirmed match and adjust leaderboard")
	@app_commands.check(_admin_app_command_check)
	async def scoreboard_admin_edit_match(
		self,
		interaction: discord.Interaction,
		match_id: str,
		new_score: str,
	):
		await interaction.response.defer(ephemeral=True)
		match = await self.store.get_match(match_id)
		if match is None:
			await interaction.followup.send("Match not found.", ephemeral=True)
			return
		if match.status != "confirmed":
			await interaction.followup.send("Only confirmed matches can be edited with leaderboard adjustment.", ephemeral=True)
			return

		try:
			new_a, new_b = _parse_score(new_score)
		except ValueError as e:
			await interaction.followup.send(str(e), ephemeral=True)
			return

		# Compute delta vs old and apply to clan_stats
		old_a, old_b = match.submitter_score, match.opponent_score
		a_key = str(match.submitter_clan_role_id)
		b_key = str(match.opponent_clan_role_id)
		stats: dict[str, Any] = self.store.data.setdefault("clan_stats", {})
		a = stats.setdefault(a_key, {"name": _role_name_from_id(match.submitter_clan_role_id), "w": 0, "l": 0, "played": 0, "maps_for": 0, "maps_against": 0})
		b = stats.setdefault(b_key, {"name": _role_name_from_id(match.opponent_clan_role_id), "w": 0, "l": 0, "played": 0, "maps_for": 0, "maps_against": 0})

		# Undo old maps
		a["maps_for"] = int(a.get("maps_for", 0)) - int(old_a)
		a["maps_against"] = int(a.get("maps_against", 0)) - int(old_b)
		b["maps_for"] = int(b.get("maps_for", 0)) - int(old_b)
		b["maps_against"] = int(b.get("maps_against", 0)) - int(old_a)

		# Undo old W/L
		if old_a > old_b:
			a["w"] = int(a.get("w", 0)) - 1
			b["l"] = int(b.get("l", 0)) - 1
		else:
			b["w"] = int(b.get("w", 0)) - 1
			a["l"] = int(a.get("l", 0)) - 1

		# Apply new maps
		a["maps_for"] = int(a.get("maps_for", 0)) + int(new_a)
		a["maps_against"] = int(a.get("maps_against", 0)) + int(new_b)
		b["maps_for"] = int(b.get("maps_for", 0)) + int(new_b)
		b["maps_against"] = int(b.get("maps_against", 0)) + int(new_a)

		# Apply new W/L
		if new_a > new_b:
			a["w"] = int(a.get("w", 0)) + 1
			b["l"] = int(b.get("l", 0)) + 1
		else:
			b["w"] = int(b.get("w", 0)) + 1
			a["l"] = int(a.get("l", 0)) + 1

		# Normalize played
		a["played"] = int(a.get("w", 0)) + int(a.get("l", 0))
		b["played"] = int(b.get("w", 0)) + int(b.get("l", 0))

		# Persist match update
		match.submitter_score = int(new_a)
		match.opponent_score = int(new_b)
		self.store.data["pending_matches"][match.match_id] = match.to_dict()

		# Update last_result to this edited match
		self.store.data["last_result"] = {
			"match_id": match.match_id,
			"a_name": _role_name_from_id(match.submitter_clan_role_id),
			"b_name": _role_name_from_id(match.opponent_clan_role_id),
			"a_score": match.submitter_score,
			"b_score": match.opponent_score,
			"at": _utcnow_iso(),
		}

		await self.store.save()
		await self.ensure_leaderboard_message()
		await interaction.followup.send(f"Updated match {match_id} to {new_a}-{new_b} and adjusted leaderboard.", ephemeral=True)

	@app_commands.guilds(discord.Object(id=GUILD_ID))
	@app_commands.command(name="scoreboard_admin_set_latest", description="Admin: set the displayed latest result line")
	@app_commands.check(_admin_app_command_check)
	async def scoreboard_admin_set_latest(
		self,
		interaction: discord.Interaction,
		clan_a: discord.Role,
		clan_b: discord.Role,
		score: str,
	):
		"""Sets the displayed latest result on the scoreboard image.

		This does NOT change leaderboard totals; use the other admin commands to correct totals.
		"""
		await interaction.response.defer(ephemeral=True)
		if clan_a.id not in set(CLAN_ROLES.values()) or clan_b.id not in set(CLAN_ROLES.values()):
			await interaction.followup.send("Both clans must be configured clan roles.", ephemeral=True)
			return
		try:
			a, b = _parse_score(score)
		except ValueError as e:
			await interaction.followup.send(str(e), ephemeral=True)
			return

		self.store.data["last_result"] = {
			"match_id": "manual",
			"a_name": _role_name_from_id(clan_a.id),
			"b_name": _role_name_from_id(clan_b.id),
			"a_score": a,
			"b_score": b,
			"at": _utcnow_iso(),
		}
		await self.store.save()
		await self.ensure_leaderboard_message()
		await interaction.followup.send(f"Set latest result to {_role_name_from_id(clan_a.id)} {a}-{b} {_role_name_from_id(clan_b.id)}.", ephemeral=True)

	@app_commands.guilds(discord.Object(id=GUILD_ID))
	@app_commands.command(name="scoreboard_repost", description="Repost/repair the scoreboard and leaderboard messages")
	@app_commands.checks.has_permissions(administrator=True)
	async def scoreboard_repost(self, interaction: discord.Interaction):
		await interaction.response.defer(ephemeral=True)
		await self.store.ensure_clans()
		await self.ensure_scoreboard_message()
		await self.ensure_leaderboard_message()
		await interaction.followup.send("Scoreboard + leaderboard repaired.", ephemeral=True)

	@app_commands.guilds(discord.Object(id=GUILD_ID))
	@app_commands.command(
		name="scoreboard_leaderboard_repost",
		description="Admin: repost the leaderboard image (posts a new message; does not delete the old one)",
	)
	@app_commands.check(_admin_app_command_check)
	async def scoreboard_leaderboard_repost(self, interaction: discord.Interaction):
		await interaction.response.defer(ephemeral=True)
		# Clear stored message id so the next update sends a fresh message.
		self.store.data["leaderboard_message_id"] = None
		await self.store.save()

		# Best-effort: bypass the local cooldown so admins can repost immediately.
		self._last_leaderboard_update_ts = 0.0
		self._leaderboard_rate_limited_until_ts = 0.0

		await self.ensure_leaderboard_message()
		await interaction.followup.send("Queued a fresh leaderboard repost (new message).", ephemeral=True)

	@app_commands.guilds(discord.Object(id=GUILD_ID))
	@app_commands.command(name="scoreboard_admin_reset", description="Admin: reset leaderboard and clear latest result")
	@app_commands.check(_admin_app_command_check)
	async def scoreboard_admin_reset(self, interaction: discord.Interaction):
		await interaction.response.defer(ephemeral=True)

		# Reset clan stats
		stats: dict[str, Any] = self.store.data.setdefault("clan_stats", {})
		for clan_name, role_id in CLAN_ROLES.items():
			key = str(role_id)
			stats[key] = {
				"name": clan_name,
				"w": 0,
				"l": 0,
				"played": 0,
				"maps_for": 0,
				"maps_against": 0,
			}
		self.store.data["clan_stats"] = stats

		# Clear latest result so the image shows NO RESULTS YET
		self.store.data["last_result"] = None

		# Optional: clear pending matches (fresh season)
		self.store.data["pending_matches"] = {}
		self.store.data["pending_by_validation_message"] = {}

		await self.store.save()
		await self.ensure_leaderboard_message()
		await interaction.followup.send("Leaderboard reset and latest result cleared.", ephemeral=True)


async def setup(bot: commands.Bot):
	await bot.add_cog(ScoreboardCog(bot))
