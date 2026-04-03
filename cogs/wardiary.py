import asyncio
import io
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import discord
from discord.ext import commands

from data_paths import data_path


log = logging.getLogger(__name__)


# -----------------------------
# Config (fill these in)
# -----------------------------

# Only members with one of these roles can use the submission button.
ALLOWED_ROLE_IDS: list[int] = [1213495462632361994, 1097946543065137183, 1097946662942560407]

# Target Discord forum channel where war diary posts should live.
WAR_DIARY_FORUM_CHANNEL_ID: int = 1489703502426018002

# The persistent submission post created inside the forum.
SUBMISSION_POST_NAME: str = "Result Submission"
SUBMISSION_POST_AUTO_ARCHIVE_MINUTES: int = 10080

# 7DR is always the home clan for submissions.
HOME_CLAN_NAME: str = "7DR"

# GIF shown on the persistent submission embed.
SUBMISSION_EMBED_GIF_URL: str = "https://cdn.discordapp.com/attachments/1098976074852999261/1489717665923862748/YouCut_20250324_130038097-ezgif.com-optimize_1.gif?ex=69d16f31&is=69d01db1&hm=3362336f1497cb538a1331e6f374112e6c15f6fbb34f0a0bdb5402bf01b52da5"

# Clan names are loaded from this file.
CLAN_CONFIG_PATH: str = data_path("clannames.json")

# Persistent state for the submission post.
STATE_PATH: str = data_path("wardiary_state.json")

# Optional font/background assets for the generated result image.
FONT_PATH: str = os.path.join(os.path.dirname(__file__), "scoreboard_font.ttf")
BACKGROUND_IMAGE_PATH: str = os.path.join(os.path.dirname(__file__), "scoreboard_blank.jpg")


def _safe_int(value: Any) -> Optional[int]:
	try:
		return int(value)
	except Exception:
		return None


def _utcnow() -> datetime:
	return datetime.now(timezone.utc)


def _score_options() -> list[tuple[int, int]]:
	return [(5, 0), (4, 1), (3, 2), (2, 3), (1, 4), (0, 5)]


def _parse_score(text: str) -> tuple[int, int]:
	cleaned = text.strip().replace(":", "-").replace(" ", "-")
	parts = [part for part in cleaned.split("-") if part]
	if len(parts) != 2:
		raise ValueError("Score must look like 3-2")

	left = _safe_int(parts[0])
	right = _safe_int(parts[1])
	if left is None or right is None:
		raise ValueError("Score must be two numbers")
	if left < 0 or right < 0:
		raise ValueError("Score must be non-negative")
	if left == right:
		raise ValueError("Score cannot be a draw")
	if left + right != 5:
		raise ValueError("Score must add up to 5 (for example 3-2, 4-1, 5-0)")
	return left, right


def _normalize_stats_link(text: str) -> Optional[str]:
	value = (text or "").strip()
	if not value:
		return None
	if not (value.startswith("http://") or value.startswith("https://")):
		raise ValueError("Stats link must start with http:// or https://")
	return value


def _truncate_thread_name(name: str) -> str:
	clean = " ".join(name.split())
	if len(clean) <= 100:
		return clean
	return clean[:97] + "..."


@dataclass(frozen=True)
class ClanConfig:
	name: str


def _can_submit_member(member: discord.Member) -> bool:
	if not ALLOWED_ROLE_IDS:
		return True
	member_role_ids = {role.id for role in member.roles}
	return any(role_id in member_role_ids for role_id in ALLOWED_ROLE_IDS)


class OpponentSelect(discord.ui.Select):
	def __init__(self, clans: list[ClanConfig]):
		self.clans = clans
		super().__init__(
			placeholder="Select the opposing clan...",
			min_values=1,
			max_values=1,
			options=[discord.SelectOption(label="Loading opponents...", value="__pending__")],
			disabled=True,
		)

	def set_options(self, clan_name: Optional[str], selected_opponent: Optional[str]) -> None:
		self.disabled = False
		options: list[discord.SelectOption] = []
		selected_label: Optional[str] = None
		for clan in self.clans:
			if clan.name == HOME_CLAN_NAME:
				continue
			is_default = clan.name == selected_opponent
			if is_default:
				selected_label = clan.name
			options.append(discord.SelectOption(label=clan.name, value=clan.name, default=is_default))

		self.options = options[:25] or [discord.SelectOption(label="No opposing clans configured", value="__none__", default=True)]
		self.placeholder = selected_label or "Select the opposing clan..."
		self.disabled = not bool(options)

	async def callback(self, interaction: discord.Interaction):
		view = self.view
		if not isinstance(view, WarDiarySubmissionView):
			return
		if not view.is_owner(interaction.user.id):
			await interaction.response.send_message("This submission form is not yours.", ephemeral=True)
			return

		selected = str(self.values[0])
		view.opponent_clan_name = selected

		refreshed: list[discord.SelectOption] = []
		selected_label: Optional[str] = None
		for option in self.options:
			is_default = str(option.value) == selected
			if is_default:
				selected_label = option.label
			refreshed.append(
				discord.SelectOption(label=option.label, value=str(option.value), default=is_default)
			)
		self.options = refreshed
		self.placeholder = selected_label or "Select the opposing clan..."

		view.refresh_score_options()
		await interaction.response.edit_message(view=view)


class ScoreSelect(discord.ui.Select):
	def __init__(self):
		super().__init__(
			placeholder="Select the result...",
			min_values=1,
			max_values=1,
			options=[discord.SelectOption(label="Pick an opponent first", value="5-0", default=True)],
			disabled=True,
		)

	def set_matchup(self, opponent_clan_name: Optional[str], selected_score: Optional[str]) -> None:
		if not opponent_clan_name:
			self.disabled = True
			self.placeholder = "Select the result..."
			self.options = [discord.SelectOption(label="Pick an opponent first", value="5-0", default=True)]
			return

		self.disabled = False
		options: list[discord.SelectOption] = []
		selected_label: Optional[str] = None
		for left, right in _score_options():
			value = f"{left}-{right}"
			label = f"{HOME_CLAN_NAME} {left}-{right} {opponent_clan_name}"
			is_default = selected_score == value
			if is_default:
				selected_label = label
			options.append(discord.SelectOption(label=label, value=value, default=is_default))

		self.options = options
		self.placeholder = selected_label or "Select the result..."

	async def callback(self, interaction: discord.Interaction):
		view = self.view
		if not isinstance(view, WarDiarySubmissionView):
			return
		if not view.is_owner(interaction.user.id):
			await interaction.response.send_message("This submission form is not yours.", ephemeral=True)
			return

		view.selected_score = str(self.values[0])
		self.set_matchup(view.opponent_clan_name, view.selected_score)
		view.refresh_submit_state()
		await interaction.response.edit_message(view=view)


class StatsLinkModal(discord.ui.Modal, title="Optional Match Stats"):
	stats_link = discord.ui.TextInput(
		label="Stats link",
		placeholder="https://...",
		required=False,
		max_length=500,
	)

	def __init__(self, cog: "WarDiaryCog", clan_name: str, opponent_clan_name: str, selected_score: str):
		super().__init__()
		self.cog = cog
		self.clan_name = clan_name
		self.opponent_clan_name = opponent_clan_name
		self.selected_score = selected_score

	async def on_submit(self, interaction: discord.Interaction) -> None:
		if not interaction.guild or not isinstance(interaction.user, discord.Member):
			await interaction.response.send_message("Use this in a server.", ephemeral=True)
			return

		try:
			left, right = _parse_score(self.selected_score)
			stats_link = _normalize_stats_link(str(self.stats_link))
		except ValueError as exc:
			await interaction.response.send_message(str(exc), ephemeral=True)
			return

		thread = await self.cog.create_result_post(
			guild=interaction.guild,
			submitter=interaction.user,
			clan_name=self.clan_name,
			opponent_clan_name=self.opponent_clan_name,
			submitter_score=left,
			opponent_score=right,
			stats_link=stats_link,
		)
		if thread is None:
			await interaction.response.send_message("Failed to create the war diary post. Check the forum channel config.", ephemeral=True)
			return

		await interaction.response.send_message(f"Posted to {thread.mention}", ephemeral=True)


class WarDiarySubmissionView(discord.ui.View):
	def __init__(self, cog: "WarDiaryCog", owner_id: int, clans: list[ClanConfig]):
		super().__init__(timeout=300)
		self.cog = cog
		self.owner_id = owner_id
		self.clan_name: str = HOME_CLAN_NAME
		self.opponent_clan_name: Optional[str] = None
		self.selected_score: Optional[str] = None

		self.opponent_select = OpponentSelect(clans)
		self.opponent_select.set_options(self.clan_name, self.opponent_clan_name)
		self.add_item(self.opponent_select)

		self.score_select = ScoreSelect()
		self.add_item(self.score_select)

	def is_owner(self, user_id: int) -> bool:
		return self.owner_id == user_id

	def refresh_opponent_options(self) -> None:
		self.opponent_clan_name = None
		self.selected_score = None
		self.opponent_select.set_options(self.clan_name, self.opponent_clan_name)
		self.score_select.set_matchup(self.opponent_clan_name, self.selected_score)
		self.refresh_submit_state()

	def refresh_score_options(self) -> None:
		self.selected_score = None
		self.score_select.set_matchup(self.opponent_clan_name, self.selected_score)
		self.refresh_submit_state()

	def refresh_submit_state(self) -> None:
		for child in self.children:
			if isinstance(child, discord.ui.Button) and child.custom_id == "wardiary:submit":
				child.disabled = not (self.opponent_clan_name and self.selected_score)

	@discord.ui.button(label="Add Optional Stats Link & Submit", style=discord.ButtonStyle.success, disabled=True, custom_id="wardiary:submit")
	async def submit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
		if not self.is_owner(interaction.user.id):
			await interaction.response.send_message("This submission form is not yours.", ephemeral=True)
			return
		if not self.opponent_clan_name or not self.selected_score:
			await interaction.response.send_message("Pick the opposing clan and the result first.", ephemeral=True)
			return

		await interaction.response.send_modal(
			StatsLinkModal(
				cog=self.cog,
				clan_name=self.clan_name,
				opponent_clan_name=self.opponent_clan_name,
				selected_score=self.selected_score,
			)
		)


class WarDiaryMainView(discord.ui.View):
	def __init__(self, cog: "WarDiaryCog"):
		super().__init__(timeout=None)
		self.cog = cog

	@discord.ui.button(label="Submit Match Result", style=discord.ButtonStyle.success, custom_id="wardiary:open_submit")
	async def open_submit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
		if not interaction.guild or not isinstance(interaction.user, discord.Member):
			await interaction.response.send_message("Use this in a server.", ephemeral=True)
			return
		if not _can_submit_member(interaction.user):
			await interaction.response.send_message("You do not have permission to submit war diary results.", ephemeral=True)
			return

		clans = self.cog.load_clans()
		if len(clans) < 2:
			await interaction.response.send_message(
				f"Configure at least two clans in {CLAN_CONFIG_PATH} before using this.",
				ephemeral=True,
			)
			return

		embed = discord.Embed(
			title="Submit War Diary Result",
			description=(
				f"Home clan is fixed as **{HOME_CLAN_NAME}**. Pick the opposing clan and the result, then optionally paste a stats link in the next step."
			),
			colour=discord.Colour.blurple(),
		)
		view = WarDiarySubmissionView(self.cog, interaction.user.id, clans)
		await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class WarDiaryCog(commands.Cog):
	def __init__(self, bot: commands.Bot):
		self.bot = bot
		self._did_initial_ensure = False
		self._state = self._load_state()
		self._ensure_lock = asyncio.Lock()

	def _load_state(self) -> dict[str, Any]:
		try:
			if not os.path.exists(STATE_PATH):
				return {}
			with open(STATE_PATH, "r", encoding="utf-8") as handle:
				data = json.load(handle)
			return data if isinstance(data, dict) else {}
		except Exception:
			log.warning("Failed to load war diary state; starting fresh.", exc_info=True)
			return {}

	def _save_state(self) -> None:
		try:
			self._state["updated_at"] = _utcnow().isoformat()
			os.makedirs(os.path.dirname(STATE_PATH) or ".", exist_ok=True)
			tmp_path = f"{STATE_PATH}.tmp"
			with open(tmp_path, "w", encoding="utf-8") as handle:
				json.dump(self._state, handle, indent=2)
			os.replace(tmp_path, STATE_PATH)
		except Exception:
			log.warning("Failed to save war diary state.", exc_info=True)

	def load_clans(self) -> list[ClanConfig]:
		try:
			if not os.path.exists(CLAN_CONFIG_PATH):
				return []
			with open(CLAN_CONFIG_PATH, "r", encoding="utf-8") as handle:
				raw = json.load(handle)
		except Exception:
			log.warning("Failed to read clan config from %s", CLAN_CONFIG_PATH, exc_info=True)
			return []

		if isinstance(raw, dict):
			entries = raw.get("clans", [])
		elif isinstance(raw, list):
			entries = raw
		else:
			return []

		clans: list[ClanConfig] = []
		seen: set[str] = set()
		for entry in entries:
			if isinstance(entry, str):
				name = entry.strip()
			elif isinstance(entry, dict):
				name = str(entry.get("name") or "").strip()
			else:
				continue

			if not name or name in seen:
				continue
			clans.append(ClanConfig(name=name))
			seen.add(name)
		return clans

	async def cog_load(self) -> None:
		self.bot.add_view(WarDiaryMainView(self))

	@commands.Cog.listener()
	async def on_ready(self) -> None:
		if self._did_initial_ensure:
			return
		self._did_initial_ensure = True
		await self.ensure_submission_post()

	def _submission_embed(self) -> discord.Embed:
		clans = self.load_clans()
		embed = discord.Embed(
			title="War Diary Match Submission",
			description=(
				"Use the button below to submit a new match result.\n\n"
				"The bot will create a new forum post titled with the final score and attach a result image."
			),
			colour=discord.Colour.green(),
			timestamp=_utcnow(),
		)
		embed.add_field(name="Home clan", value=HOME_CLAN_NAME, inline=False)
		embed.add_field(
			name="How To Submit",
			value=(
				"1. Click the Submit Match Result button.\n"
				"2. Select the opposing clan, click 'other' if it is not listed.\n"
				"3. Select the result\n"
				"4. Before you go to the next step, check you have the stats link for the match, if you want to include that.\n"
				"5. Click 'Add Optional Stats Link & Submit'\n"
				"6. Paste the stats ink and click Submit!"
			),
			inline=False,
		)
		embed.set_image(url=SUBMISSION_EMBED_GIF_URL)
		embed.set_footer(text=os.path.basename(CLAN_CONFIG_PATH))
		return embed

	async def _get_forum_channel(self) -> Optional[discord.ForumChannel]:
		if not WAR_DIARY_FORUM_CHANNEL_ID:
			return None
		channel = self.bot.get_channel(WAR_DIARY_FORUM_CHANNEL_ID)
		if channel is None:
			try:
				channel = await self.bot.fetch_channel(WAR_DIARY_FORUM_CHANNEL_ID)
			except Exception:
				log.exception("Failed to fetch war diary forum channel")
				return None
		return channel if isinstance(channel, discord.ForumChannel) else None

	async def _get_thread(self, thread_id: int) -> Optional[discord.Thread]:
		channel = self.bot.get_channel(thread_id)
		if isinstance(channel, discord.Thread):
			return channel
		try:
			fetched = await self.bot.fetch_channel(thread_id)
		except Exception:
			return None
		return fetched if isinstance(fetched, discord.Thread) else None

	def _extract_created_post(self, created: Any) -> tuple[Optional[discord.Thread], Optional[discord.Message]]:
		thread = getattr(created, "thread", None)
		message = getattr(created, "message", None)

		if isinstance(thread, discord.Thread):
			return thread, message if isinstance(message, discord.Message) else None
		if isinstance(created, tuple) and len(created) == 2:
			maybe_thread, maybe_message = created
			return (
				maybe_thread if isinstance(maybe_thread, discord.Thread) else None,
				maybe_message if isinstance(maybe_message, discord.Message) else None,
			)
		if isinstance(created, discord.Thread):
			return created, None
		return None, None

	async def ensure_submission_post(self) -> None:
		async with self._ensure_lock:
			forum = await self._get_forum_channel()
			if forum is None:
				return

			embed = self._submission_embed()
			view = WarDiaryMainView(self)
			thread_id = _safe_int(self._state.get("submission_thread_id"))
			message_id = _safe_int(self._state.get("submission_message_id"))

			if thread_id and message_id:
				thread = await self._get_thread(thread_id)
				if thread is not None:
					try:
						if thread.archived:
							await thread.edit(archived=False, locked=False)
					except Exception:
						pass
					try:
						message = await thread.fetch_message(message_id)
						await message.edit(content="Open the submission flow below.", embed=embed, view=view)
						return
					except discord.NotFound:
						self._state.pop("submission_thread_id", None)
						self._state.pop("submission_message_id", None)
						self._save_state()
					except Exception:
						log.exception("Failed updating existing war diary submission post")
						return

			try:
				created = await forum.create_thread(
					name=_truncate_thread_name(SUBMISSION_POST_NAME),
					content="Open the submission flow below.",
					embed=embed,
					view=view,
					auto_archive_duration=SUBMISSION_POST_AUTO_ARCHIVE_MINUTES,
				)
			except Exception:
				log.exception("Failed to create war diary submission post")
				return

			thread, message = self._extract_created_post(created)
			if thread is None:
				log.warning("War diary submission post was created but thread details were unavailable")
				return

			self._state["submission_thread_id"] = thread.id
			if message is not None:
				self._state["submission_message_id"] = message.id
			self._save_state()

	def _build_result_embed(
		self,
		*,
		submitter_clan_name: str,
		opponent_clan_name: str,
		submitter_score: int,
		opponent_score: int,
		filename: str,
		submitter: discord.Member,
	) -> discord.Embed:
		embed = discord.Embed(
			title="War Diary Result",
			description=f"**{submitter_clan_name}** {submitter_score}-{opponent_score} **{opponent_clan_name}**",
			colour=discord.Colour.blurple(),
			timestamp=_utcnow(),
		)
		embed.add_field(name="Submitted by", value=submitter.mention, inline=False)
		embed.set_image(url=f"attachment://{filename}")
		return embed

	def _render_result_image(
		self,
		*,
		submitter_clan_name: str,
		opponent_clan_name: str,
		submitter_score: int,
		opponent_score: int,
	) -> bytes:
		from PIL import Image, ImageDraw, ImageFont, ImageOps

		width = 1600
		height = 900

		if os.path.exists(BACKGROUND_IMAGE_PATH):
			base = Image.open(BACKGROUND_IMAGE_PATH).convert("RGBA")
			base = ImageOps.fit(base, (width, height), method=Image.Resampling.LANCZOS)
			overlay = Image.new("RGBA", (width, height), (8, 12, 20, 140))
			base = Image.alpha_composite(base, overlay)
		else:
			base = Image.new("RGBA", (width, height), (18, 24, 38, 255))

		draw = ImageDraw.Draw(base)

		def load_font(size: int):
			try:
				return ImageFont.truetype(FONT_PATH, size)
			except Exception:
				return ImageFont.load_default()

		def fit_font(text: str, max_width: int, start_size: int, min_size: int):
			for size in range(start_size, min_size - 1, -2):
				font = load_font(size)
				bbox = draw.textbbox((0, 0), text, font=font)
				if (bbox[2] - bbox[0]) <= max_width:
					return font
			return load_font(min_size)

		text_fill = (255, 255, 255, 255)
		accent_fill = (120, 190, 255, 255)

		label_font = fit_font("WAR DIARY", width - 120, 56, 20)
		score_font = fit_font(f"{submitter_score} - {opponent_score}", int(width * 0.35), 170, 48)
		clan_font = fit_font(
			submitter_clan_name if len(submitter_clan_name) >= len(opponent_clan_name) else opponent_clan_name,
			int(width * 0.28),
			80,
			24,
		)
		date_font = fit_font(_utcnow().strftime("%d/%m/%Y"), width - 120, 32, 16)

		center_y = height // 2
		draw.text((width // 2, 110), "WAR DIARY", font=label_font, fill=accent_fill, anchor="mm")
		draw.text((width * 0.24, center_y), submitter_clan_name, font=clan_font, fill=text_fill, anchor="lm")
		draw.text((width // 2, center_y), f"{submitter_score} - {opponent_score}", font=score_font, fill=text_fill, anchor="mm")
		draw.text((width * 0.76, center_y), opponent_clan_name, font=clan_font, fill=text_fill, anchor="rm")
		draw.text((width // 2, height - 90), _utcnow().strftime("%d/%m/%Y"), font=date_font, fill=text_fill, anchor="mm")

		out = io.BytesIO()
		base.save(out, format="PNG")
		out.seek(0)
		return out.getvalue()

	async def create_result_post(
		self,
		*,
		guild: discord.Guild,
		submitter: discord.Member,
		clan_name: str,
		opponent_clan_name: str,
		submitter_score: int,
		opponent_score: int,
		stats_link: Optional[str],
	) -> Optional[discord.Thread]:
		forum = await self._get_forum_channel()
		if forum is None:
			return None

		thread_name = _truncate_thread_name(
			f"{clan_name} {submitter_score} - {opponent_score} {opponent_clan_name}"
		)
		filename = f"wardiary_{submitter_score}_{opponent_score}.png"
		image_bytes = self._render_result_image(
			submitter_clan_name=clan_name,
			opponent_clan_name=opponent_clan_name,
			submitter_score=submitter_score,
			opponent_score=opponent_score,
		)
		file = discord.File(io.BytesIO(image_bytes), filename=filename)
		embed = self._build_result_embed(
			submitter_clan_name=clan_name,
			opponent_clan_name=opponent_clan_name,
			submitter_score=submitter_score,
			opponent_score=opponent_score,
			filename=filename,
			submitter=submitter,
		)

		content_lines: list[str] = []
		if stats_link:
			content_lines.append(f"Stats link: {stats_link}")
		content = "\n".join(content_lines) if content_lines else None

		try:
			created = await forum.create_thread(
				name=thread_name,
				content=content,
				embed=embed,
				file=file,
				allowed_mentions=discord.AllowedMentions.none(),
			)
		except Exception:
			log.exception("Failed creating war diary result post")
			return None

		thread, _message = self._extract_created_post(created)
		return thread


async def setup(bot: commands.Bot):
	await bot.add_cog(WarDiaryCog(bot))
