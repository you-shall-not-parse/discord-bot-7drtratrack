from __future__ import annotations

import asyncio
import io
import json
import logging
import re
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import MAIN_GUILD_ID
from data_paths import data_path
from state_io import atomic_json_dump


LOGGER = logging.getLogger(__name__)

STRATEGIC_REVIEW_CHANNEL_ID = 1535617056752537710
UK_TIMEZONE = ZoneInfo("Europe/London")
STATE_PATH = Path(data_path("strategic_review_notes_state.json"))
MONDAY_DIGEST_TIME = time(hour=9, minute=0, tzinfo=UK_TIMEZONE)
TRANSCRIPT_SIZE_MARGIN = 1024
EMBED_DESCRIPTION_LIMIT = 3900


def _parse_uk_since_time(
    value: str,
    *,
    now_utc: datetime | None = None,
) -> tuple[datetime | None, str | None]:
    """Parse a past UK clock time and return an aware UTC datetime."""
    raw = " ".join(value.strip().split())
    current_utc = now_utc or datetime.now(timezone.utc)
    if current_utc.tzinfo is None:
        current_utc = current_utc.replace(tzinfo=timezone.utc)
    current_utc = current_utc.astimezone(timezone.utc)
    current_uk = current_utc.astimezone(UK_TIMEZONE)
    local_naive: datetime | None = None

    time_match = re.fullmatch(
        r"(?:(today|yesterday)\s+)?([01]?\d|2[0-3])[:.]([0-5]\d)",
        raw,
        flags=re.IGNORECASE,
    )
    if time_match:
        day_word, hour, minute = time_match.groups()
        target_date = current_uk.date()
        if day_word and day_word.casefold() == "yesterday":
            target_date -= timedelta(days=1)
        local_naive = datetime.combine(target_date, datetime.min.time()).replace(
            hour=int(hour),
            minute=int(minute),
        )
    else:
        for date_format in (
            "%d/%m/%Y %H:%M",
            "%d/%m/%y %H:%M",
            "%Y-%m-%d %H:%M",
        ):
            try:
                local_naive = datetime.strptime(raw, date_format)
                break
            except ValueError:
                continue

    if local_naive is None:
        return None, (
            "Use UK time in one of these formats: `09:30`, `today 09:30`, "
            "`yesterday 18:00`, or `07/08/2026 09:30`."
        )

    local_time = local_naive.replace(tzinfo=UK_TIMEZONE)
    round_trip = (
        local_time.astimezone(timezone.utc)
        .astimezone(UK_TIMEZONE)
        .replace(tzinfo=None)
    )
    if round_trip != local_naive:
        return None, "That UK clock time does not exist because of the daylight-saving change."

    since_utc = local_time.astimezone(timezone.utc)
    if since_utc > current_utc:
        return None, "The transcript start time cannot be in the future."
    return since_utc, None


def _clean_transcript_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _message_transcript_block(message: discord.Message) -> str:
    created_uk = message.created_at.astimezone(UK_TIMEZONE)
    author_name = getattr(message.author, "display_name", str(message.author))
    author_id = getattr(message.author, "id", "unknown")
    lines = [
        f"[{created_uk.strftime('%Y-%m-%d %H:%M:%S %Z')}] "
        f"{author_name} (Discord ID: {author_id})",
    ]

    content = _clean_transcript_text(message.content).strip()
    if content:
        lines.append(content)
    else:
        lines.append("[No text content]")

    for attachment in message.attachments:
        lines.append(f"[Attachment: {attachment.filename}] {attachment.url}")
    for sticker in message.stickers:
        lines.append(f"[Sticker: {sticker.name}] {sticker.url}")
    for embed in message.embeds:
        parts = [part for part in (embed.title, embed.description, embed.url) if part]
        summary = " | ".join(_clean_transcript_text(str(part)) for part in parts)
        lines.append(f"[Embed] {summary}" if summary else "[Embed]")

    lines.append(f"[Message link] {message.jump_url}")
    return "\n".join(lines)


def _safe_filename(title: str, captured_at: datetime) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", title).strip("-")[:60]
    if not slug:
        slug = "note"
    stamp = captured_at.astimezone(UK_TIMEZONE).strftime("%Y%m%d-%H%M")
    return f"strategic-review-{slug}-{stamp}.txt"


def _load_state() -> dict[str, Any]:
    default: dict[str, Any] = {"notes": {}, "last_digest_week": None}
    if not STATE_PATH.exists():
        return default
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        LOGGER.warning("Could not load strategic-review-note state.", exc_info=True)
        return default
    if not isinstance(raw, dict) or not isinstance(raw.get("notes"), dict):
        return default
    raw.setdefault("last_digest_week", None)
    return raw


class CloseStrategicReviewNoteButton(discord.ui.Button):
    def __init__(self, *, disabled: bool = False) -> None:
        super().__init__(
            label="Close Note" if not disabled else "Note Closed",
            style=discord.ButtonStyle.danger if not disabled else discord.ButtonStyle.secondary,
            custom_id="strategic_review_note:close",
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("StrategicReviewNote")
        if not isinstance(cog, StrategicReviewNote):
            await interaction.response.send_message(
                "The strategic review note tool is unavailable.",
                ephemeral=True,
            )
            return
        await cog.close_note(interaction)


class StrategicReviewNoteView(discord.ui.View):
    def __init__(self, *, closed: bool = False) -> None:
        super().__init__(timeout=None)
        self.add_item(CloseStrategicReviewNoteButton(disabled=closed))


class StrategicReviewNote(commands.Cog):
    """Create transcript-backed strategic review threads and a weekly index."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.state = _load_state()
        self._state_lock = asyncio.Lock()
        self._digest_lock = asyncio.Lock()
        self.bot.add_view(StrategicReviewNoteView())
        self.monday_digest.start()

    def cog_unload(self) -> None:
        self.monday_digest.cancel()

    def _save_state(self) -> None:
        atomic_json_dump(STATE_PATH, self.state, indent=2, ensure_ascii=False)

    async def _target_channel(self) -> discord.TextChannel | None:
        channel = self.bot.get_channel(STRATEGIC_REVIEW_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(STRATEGIC_REVIEW_CHANNEL_ID)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                LOGGER.exception(
                    "Could not fetch strategic review channel %s.",
                    STRATEGIC_REVIEW_CHANNEL_ID,
                )
                return None
        if not isinstance(channel, discord.TextChannel):
            LOGGER.error(
                "Strategic review destination %s is not a text channel.",
                STRATEGIC_REVIEW_CHANNEL_ID,
            )
            return None
        return channel

    @staticmethod
    def _note_embed(note: dict[str, Any], *, closed: bool = False) -> discord.Embed:
        since_at = datetime.fromisoformat(str(note["since_at"]))
        captured_at = datetime.fromisoformat(str(note["created_at"]))
        embed = discord.Embed(
            title=str(note["title"]),
            description=(
                "The attached transcript is the starting context for this strategic review. "
                "Download it and paste it into ChatGPT to summarise the discussion, then continue "
                "the review in this thread."
            ),
            color=discord.Color.dark_grey() if closed else discord.Color.blue(),
            timestamp=captured_at,
        )
        embed.add_field(name="Status", value="Closed" if closed else "Open", inline=True)
        embed.add_field(name="Created by", value=f"<@{note['creator_id']}>", inline=True)
        embed.add_field(
            name="Transcript window",
            value=f"<t:{int(since_at.timestamp())}:F> to <t:{int(captured_at.timestamp())}:F>",
            inline=False,
        )
        embed.add_field(
            name="Messages captured",
            value=str(note.get("message_count", 0)),
            inline=True,
        )
        if closed and note.get("closed_at"):
            closed_at = datetime.fromisoformat(str(note["closed_at"]))
            closed_by = note.get("closed_by")
            closed_value = f"<t:{int(closed_at.timestamp())}:F>"
            if closed_by:
                closed_value += f" by <@{closed_by}>"
            embed.add_field(name="Closed", value=closed_value, inline=False)
        embed.set_footer(text="Use the Close Note button when this review is complete.")
        return embed

    async def _build_transcript(
        self,
        channel: discord.TextChannel,
        *,
        since_utc: datetime,
        captured_at: datetime,
    ) -> tuple[bytes, int]:
        blocks: list[str] = []
        async for message in channel.history(
            limit=None,
            after=since_utc,
            before=captured_at,
            oldest_first=True,
        ):
            blocks.append(_message_transcript_block(message))

        header = (
            "STRATEGIC REVIEW NOTE TRANSCRIPT\n"
            f"Channel: #{channel.name} ({channel.id})\n"
            f"From: {since_utc.astimezone(UK_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
            f"Captured: {captured_at.astimezone(UK_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
            f"Messages: {len(blocks)}\n"
            "\n"
        )
        body = "\n\n---\n\n".join(blocks)
        return (header + body + "\n").encode("utf-8"), len(blocks)

    @app_commands.command(
        name="strategic-review-note",
        description="Open a strategic review thread with a transcript of recent channel messages.",
    )
    @app_commands.guilds(discord.Object(id=MAIN_GUILD_ID))
    @app_commands.guild_only()
    @app_commands.describe(
        title="Title for the new strategic review thread",
        since="Start of the transcript in UK time, e.g. yesterday 18:00",
    )
    async def strategic_review_note(
        self,
        interaction: discord.Interaction,
        title: str,
        since: str,
    ) -> None:
        clean_title = " ".join(title.strip().split())
        if not clean_title or len(clean_title) > 100:
            await interaction.response.send_message(
                "The title must contain between 1 and 100 characters.",
                ephemeral=True,
            )
            return

        captured_at = datetime.now(timezone.utc)
        since_utc, error = _parse_uk_since_time(since, now_utc=captured_at)
        if since_utc is None:
            await interaction.response.send_message(error, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        channel = await self._target_channel()
        if channel is None:
            await interaction.followup.send(
                "I could not access the configured strategic review channel. Please contact an administrator.",
                ephemeral=True,
            )
            return

        try:
            transcript, message_count = await self._build_transcript(
                channel,
                since_utc=since_utc,
                captured_at=captured_at,
            )
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.exception("Could not read strategic review channel history.")
            await interaction.followup.send(
                "I could not read that channel's message history. Please check my permissions.",
                ephemeral=True,
            )
            return

        upload_limit = interaction.guild.filesize_limit if interaction.guild else 8 * 1024 * 1024
        if len(transcript) > max(0, upload_limit - TRANSCRIPT_SIZE_MARGIN):
            await interaction.followup.send(
                "That transcript is too large for Discord's upload limit. Choose a later start time and try again.",
                ephemeral=True,
            )
            return

        note: dict[str, Any] = {
            "title": clean_title,
            "creator_id": interaction.user.id,
            "created_at": captured_at.isoformat(),
            "since_at": since_utc.isoformat(),
            "status": "open",
            "message_count": message_count,
            "channel_id": channel.id,
        }
        filename = _safe_filename(clean_title, captured_at)
        try:
            header_message = await channel.send(
                embed=self._note_embed(note),
                file=discord.File(io.BytesIO(transcript), filename=filename),
                view=StrategicReviewNoteView(),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.exception("Could not post strategic review note header.")
            await interaction.followup.send(
                "Discord could not post the strategic review note. Please check my message and attachment permissions.",
                ephemeral=True,
            )
            return

        try:
            thread = await header_message.create_thread(
                name=clean_title,
                reason=f"Strategic review note created by {interaction.user} ({interaction.user.id})",
            )
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.exception("Could not create strategic review note thread.")
            try:
                await header_message.delete()
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.exception("Could not remove the incomplete strategic review header.")
            await interaction.followup.send(
                "Discord could not create the strategic review thread. Please check my Create Public Threads permission.",
                ephemeral=True,
            )
            return

        note.update(
            {
                "thread_id": thread.id,
                "header_message_id": header_message.id,
            }
        )
        async with self._state_lock:
            self.state["notes"][str(thread.id)] = note
            self._save_state()

        await interaction.followup.send(
            f"Created strategic review note {thread.mention} with {message_count} message(s) in its transcript.",
            ephemeral=True,
        )

    async def close_note(self, interaction: discord.Interaction) -> None:
        message = interaction.message
        if interaction.guild_id != MAIN_GUILD_ID or message is None:
            await interaction.response.send_message("This is not a tracked strategic review note.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self._state_lock:
            matching_key: str | None = None
            note: dict[str, Any] | None = None
            for key, candidate in self.state["notes"].items():
                if candidate.get("header_message_id") == message.id:
                    matching_key = key
                    note = candidate
                    break

            if matching_key is None or note is None:
                await interaction.followup.send("This note is not in the strategic review index.", ephemeral=True)
                return
            if note.get("status") == "closed":
                await interaction.followup.send("This strategic review note is already closed.", ephemeral=True)
                return

            thread_id = int(note["thread_id"])
            thread = self.bot.get_channel(thread_id)
            if thread is None:
                try:
                    thread = await self.bot.fetch_channel(thread_id)
                except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                    LOGGER.exception("Could not fetch strategic review thread %s.", thread_id)
                    await interaction.followup.send("I could not find or access this note's thread.", ephemeral=True)
                    return
            if not isinstance(thread, discord.Thread):
                await interaction.followup.send("The tracked discussion is no longer a thread.", ephemeral=True)
                return

            closed_at = datetime.now(timezone.utc)
            try:
                await thread.edit(
                    archived=True,
                    locked=True,
                    reason=f"Strategic review note closed by {interaction.user} ({interaction.user.id})",
                )
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.exception("Could not close strategic review thread %s.", thread.id)
                await interaction.followup.send(
                    "I could not close and lock this thread. Please check my Manage Threads permission.",
                    ephemeral=True,
                )
                return

            note["status"] = "closed"
            note["closed_at"] = closed_at.isoformat()
            note["closed_by"] = interaction.user.id
            self._save_state()

        try:
            await message.edit(
                embed=self._note_embed(note, closed=True),
                view=StrategicReviewNoteView(closed=True),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.exception("Closed note %s but could not update its header.", thread.id)

        await interaction.followup.send(f"Closed and locked **{note['title']}**.", ephemeral=True)

    def _digest_embeds(self, *, now_uk: datetime) -> list[discord.Embed]:
        notes = list(self.state["notes"].values())
        notes.sort(
            key=lambda item: (
                item.get("status") != "open",
                -datetime.fromisoformat(str(item["created_at"])).timestamp(),
            )
        )
        open_count = sum(note.get("status") == "open" for note in notes)
        closed_count = len(notes) - open_count
        lines: list[str] = []
        for note in notes:
            status = "OPEN" if note.get("status") == "open" else "CLOSED"
            created = datetime.fromisoformat(str(note["created_at"]))
            title = discord.utils.escape_markdown(str(note.get("title") or "Untitled"))
            if len(title) > 80:
                title = title[:77] + "..."
            lines.append(
                f"**{status}** - <#{note['thread_id']}> - {title} "
                f"(created <t:{int(created.timestamp())}:d>)"
            )

        if not lines:
            lines = ["No strategic review notes have been created yet."]

        pages: list[str] = []
        current = ""
        for line in lines:
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) > EMBED_DESCRIPTION_LIMIT and current:
                pages.append(current)
                current = line
            else:
                current = candidate
        if current:
            pages.append(current)

        embeds: list[discord.Embed] = []
        for index, page in enumerate(pages, start=1):
            title = "Strategic Review Notes - Weekly Summary"
            if len(pages) > 1:
                title += f" ({index}/{len(pages)})"
            embed = discord.Embed(
                title=title,
                description=page,
                color=discord.Color.blurple(),
                timestamp=now_uk,
            )
            embed.add_field(name="Open", value=str(open_count), inline=True)
            embed.add_field(name="Closed", value=str(closed_count), inline=True)
            embed.add_field(name="Total", value=str(len(notes)), inline=True)
            embed.set_footer(text="Posted every Monday at 09:00 UK time")
            embeds.append(embed)
        return embeds

    @tasks.loop(time=MONDAY_DIGEST_TIME)
    async def monday_digest(self) -> None:
        await self._post_monday_digest_if_due()

    async def _post_monday_digest_if_due(self) -> None:
        now_uk = datetime.now(UK_TIMEZONE)
        scheduled_today = now_uk.replace(
            hour=MONDAY_DIGEST_TIME.hour,
            minute=MONDAY_DIGEST_TIME.minute,
            second=0,
            microsecond=0,
        )
        if now_uk.weekday() != 0 or now_uk < scheduled_today:
            return
        week_key = f"{now_uk.isocalendar().year}-W{now_uk.isocalendar().week:02d}"
        async with self._digest_lock:
            if self.state.get("last_digest_week") == week_key:
                return
            channel = await self._target_channel()
            if channel is None:
                return
            embeds = self._digest_embeds(now_uk=now_uk)
            try:
                for start in range(0, len(embeds), 10):
                    await channel.send(
                        embeds=embeds[start : start + 10],
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.exception("Could not post the Monday strategic review summary.")
                return
            async with self._state_lock:
                self.state["last_digest_week"] = week_key
                self._save_state()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # Catch up if the bot was offline at the scheduled Monday posting time.
        await self._post_monday_digest_if_due()

    @monday_digest.before_loop
    async def before_monday_digest(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StrategicReviewNote(bot))
