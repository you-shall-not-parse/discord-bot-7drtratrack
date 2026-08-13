from __future__ import annotations

import asyncio
import io
import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from config import MAIN_GUILD_ID


LOGGER = logging.getLogger(__name__)
UK_TIMEZONE = ZoneInfo("Europe/London")
ALLOWED_USER_IDS = frozenset({1250569593609654316, 1109147750932676649})
PROTECTED_EMOJI = "✅"
CONFIRMATION_TIMEOUT_SECONDS = 120
TRANSCRIPT_CHUNK_BYTES = 7 * 1024 * 1024


def _is_allowed_user(interaction: discord.Interaction) -> bool:
    return interaction.user.id in ALLOWED_USER_IDS


def _parse_uk_window(
    date_value: str,
    from_value: str,
    to_value: str,
    *,
    now_utc: datetime | None = None,
) -> tuple[datetime | None, datetime | None, str | None]:
    """Parse one inclusive, same-day UK-time window into UTC bounds."""
    current_utc = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    current_uk = current_utc.astimezone(UK_TIMEZONE)
    raw_date = " ".join(date_value.strip().split())

    selected_date: date | None
    if raw_date.casefold() == "today":
        selected_date = current_uk.date()
    elif raw_date.casefold() == "yesterday":
        selected_date = current_uk.date() - timedelta(days=1)
    else:
        selected_date = None
        for date_format in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
            try:
                selected_date = datetime.strptime(raw_date, date_format).date()
                break
            except ValueError:
                continue

    if selected_date is None:
        return None, None, "Use a UK date such as `13/08/2026`, `2026-08-13`, `today`, or `yesterday`."

    parsed_times: list[time] = []
    for label, value in (("from", from_value), ("to", to_value)):
        match = re.fullmatch(r"([01]?\d|2[0-3])[:.]([0-5]\d)", value.strip())
        if match is None:
            return None, None, f"The `{label}` time must use UK 24-hour time, for example `18:30`."
        parsed_times.append(time(hour=int(match.group(1)), minute=int(match.group(2))))

    utc_values: list[datetime] = []
    for label, parsed_time in zip(("from", "to"), parsed_times):
        local_naive = datetime.combine(selected_date, parsed_time)
        first = local_naive.replace(tzinfo=UK_TIMEZONE, fold=0)
        second = local_naive.replace(tzinfo=UK_TIMEZONE, fold=1)
        round_trip = first.astimezone(timezone.utc).astimezone(UK_TIMEZONE).replace(tzinfo=None)
        if round_trip != local_naive:
            return None, None, f"The `{label}` UK time does not exist because of the daylight-saving change."
        if first.utcoffset() != second.utcoffset():
            return None, None, f"The `{label}` UK time is ambiguous because of the daylight-saving change. Choose another time."
        utc_values.append(first.astimezone(timezone.utc))

    start_utc, finish_utc = utc_values
    if start_utc >= finish_utc:
        return None, None, "The `from` time must be earlier than the `to` time."
    if finish_utc > current_utc:
        return None, None, "The `to` time cannot be in the future."
    return start_utc, finish_utc, None


def _has_protected_tick(message: discord.Message) -> bool:
    return any(str(reaction.emoji) == PROTECTED_EMOJI for reaction in message.reactions)


def _transcript_block(message: discord.Message) -> str:
    created = message.created_at.astimezone(UK_TIMEZONE)
    author_name = getattr(message.author, "display_name", str(message.author))
    author_id = getattr(message.author, "id", "unknown")
    lines = [
        f"[{created.strftime('%Y-%m-%d %H:%M:%S %Z')}] {author_name} (Discord ID: {author_id})",
        f"Message: {message.jump_url}",
        (message.content or "[No text content]").replace("\r\n", "\n").replace("\r", "\n"),
    ]
    lines.extend(f"[Attachment: {item.filename}] {item.url}" for item in message.attachments)
    lines.extend(f"[Sticker: {item.name}] {item.url}" for item in message.stickers)
    for embed in message.embeds:
        details = " | ".join(str(value) for value in (embed.title, embed.description, embed.url) if value)
        lines.append(f"[Embed] {details}" if details else "[Embed]")
    if message.reactions:
        lines.append("[Reactions] " + ", ".join(f"{reaction.emoji} x{reaction.count}" for reaction in message.reactions))
    return "\n".join(lines) + "\n\n"


def _transcript_files(messages: list[discord.Message], channel_name: str) -> list[discord.File]:
    header = (
        "Discord bulk deletion transcript\n"
        f"Channel: #{channel_name}\n"
        f"Deleted messages: {len(messages)}\n\n"
    ).encode("utf-8")
    chunks: list[bytes] = []
    current = bytearray(header)
    for message in messages:
        block = _transcript_block(message).encode("utf-8")
        if len(current) + len(block) > TRANSCRIPT_CHUNK_BYTES and len(current) > len(header):
            chunks.append(bytes(current))
            current = bytearray(header)
        current.extend(block)
    chunks.append(bytes(current))
    width = len(str(len(chunks)))
    return [
        discord.File(io.BytesIO(payload), filename=f"bulk-delete-transcript-{index:0{width}d}.txt")
        for index, payload in enumerate(chunks, start=1)
    ]


class BulkDeleteConfirmation(discord.ui.View):
    def __init__(
        self,
        cog: "BulkDelete",
        requester_id: int,
        channel: discord.abc.Messageable,
        start_utc: datetime,
        finish_utc: datetime,
    ) -> None:
        super().__init__(timeout=CONFIRMATION_TIMEOUT_SECONDS)
        self.cog = cog
        self.requester_id = requester_id
        self.channel = channel
        self.start_utc = start_utc
        self.finish_utc = finish_utc
        self.completed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("This confirmation belongs to another user.", ephemeral=True)
            return False
        return True

    def _disable(self) -> None:
        for child in self.children:
            child.disabled = True
        self.stop()

    @discord.ui.button(label="Delete messages", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        self.completed = True
        self._disable()
        await interaction.response.edit_message(content="Deletion confirmed. Re-checking ✅ protection and deleting…", view=self)
        await self.cog.execute_deletion(interaction, self.channel, self.start_utc, self.finish_utc)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        self.completed = True
        self._disable()
        await interaction.response.edit_message(content="Bulk deletion cancelled. Nothing was deleted.", view=self)


class BulkDelete(commands.Cog):
    """Restricted, transcripted deletion of messages in a selected time window."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._channel_locks: dict[int, asyncio.Lock] = {}

    @staticmethod
    async def _messages_in_window(
        channel: discord.abc.Messageable,
        start_utc: datetime,
        finish_utc: datetime,
    ) -> list[discord.Message]:
        # Discord's history bounds are exclusive. One minute is added so the
        # user-selected finish minute is inclusive through XX:XX:59.999999.
        after = start_utc - timedelta(microseconds=1)
        before = finish_utc + timedelta(minutes=1)
        return [
            message
            async for message in channel.history(
                limit=None,
                after=after,
                before=before,
                oldest_first=True,
            )
            if start_utc <= message.created_at < before
        ]

    @staticmethod
    def _bot_permissions(interaction: discord.Interaction) -> discord.Permissions | None:
        channel = interaction.channel
        guild = interaction.guild
        if channel is None or guild is None or not hasattr(channel, "permissions_for"):
            return None
        bot_member = guild.me or (guild.get_member(interaction.client.user.id) if interaction.client.user else None)
        return channel.permissions_for(bot_member) if bot_member else None

    @app_commands.command(name="bulkdelete", description="Delete unprotected messages in a UK date and time range")
    @app_commands.guilds(discord.Object(id=MAIN_GUILD_ID))
    @app_commands.guild_only()
    @app_commands.check(_is_allowed_user)
    @app_commands.rename(date_value="date", from_time="from", to_time="to")
    @app_commands.describe(
        date_value="UK date: DD/MM/YYYY, YYYY-MM-DD, today, or yesterday",
        from_time="Start time in UK 24-hour time, e.g. 18:00",
        to_time="Finish time in UK 24-hour time, e.g. 20:30",
    )
    async def bulkdelete(
        self,
        interaction: discord.Interaction,
        date_value: str,
        from_time: str,
        to_time: str,
    ) -> None:
        channel = interaction.channel
        if channel is None or not hasattr(channel, "history"):
            await interaction.response.send_message("This channel does not have a message history I can delete.", ephemeral=True)
            return

        permissions = self._bot_permissions(interaction)
        if permissions is None or not permissions.view_channel or not permissions.read_message_history or not permissions.manage_messages:
            await interaction.response.send_message(
                "I need View Channel, Read Message History, and Manage Messages in this channel.",
                ephemeral=True,
            )
            return

        start_utc, finish_utc, error = _parse_uk_window(date_value, from_time, to_time)
        if error or start_utc is None or finish_utc is None:
            await interaction.response.send_message(error or "Invalid time range.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            messages = await self._messages_in_window(channel, start_utc, finish_utc)
        except discord.HTTPException:
            LOGGER.exception("Could not inspect channel %s for bulk deletion", getattr(channel, "id", "unknown"))
            await interaction.followup.send("I could not read that channel's message history.", ephemeral=True)
            return

        if not messages:
            await interaction.followup.send("There are no messages in that date and time range.", ephemeral=True)
            return

        protected_count = sum(_has_protected_tick(message) for message in messages)
        delete_count = len(messages) - protected_count
        if delete_count == 0:
            await interaction.followup.send(
                f"All {protected_count} messages in that range have {PROTECTED_EMOJI}, so nothing can be deleted.",
                ephemeral=True,
            )
            return

        start_link = messages[0].jump_url
        finish_link = messages[-1].jump_url
        content = (
            "**Are you sure?**\n"
            f"Channel: {getattr(channel, 'mention', '#unknown')}\n"
            f"Range: <t:{int(start_utc.timestamp())}:F> to <t:{int(finish_utc.timestamp())}:F> (UK selection)\n"
            f"Boundary messages: [start]({start_link}) · [finish]({finish_link})\n"
            f"Will delete: **{delete_count}** · Will skip because of {PROTECTED_EMOJI}: **{protected_count}**\n\n"
            "Protection will be checked again immediately before deletion. This cannot be undone."
        )
        view = BulkDeleteConfirmation(self, interaction.user.id, channel, start_utc, finish_utc)
        await interaction.followup.send(content, view=view, ephemeral=True)

    async def execute_deletion(
        self,
        interaction: discord.Interaction,
        channel: discord.abc.Messageable,
        start_utc: datetime,
        finish_utc: datetime,
    ) -> None:
        channel_id = getattr(channel, "id", 0)
        lock = self._channel_locks.setdefault(channel_id, asyncio.Lock())
        async with lock:
            try:
                messages = await self._messages_in_window(channel, start_utc, finish_utc)
            except discord.HTTPException:
                LOGGER.exception("Could not re-check channel %s before deletion", channel_id)
                await interaction.followup.send("I could not re-check the messages, so nothing was deleted.", ephemeral=True)
                return

            protected = [message for message in messages if _has_protected_tick(message)]
            candidates = [message for message in messages if not _has_protected_tick(message)]
            deleted: list[discord.Message] = []
            failed = 0
            cutoff = discord.utils.utcnow() - timedelta(days=14)
            recent = [message for message in candidates if message.created_at > cutoff]
            old = [message for message in candidates if message.created_at <= cutoff]

            for index in range(0, len(recent), 100):
                batch = recent[index:index + 100]
                if len(batch) > 1 and not hasattr(channel, "delete_messages"):
                    for message in batch:
                        try:
                            await message.delete(reason=f"Bulk deletion confirmed by {interaction.user} ({interaction.user.id})")
                            deleted.append(message)
                        except discord.HTTPException:
                            failed += 1
                            LOGGER.exception("Could not delete message %s", message.id)
                    continue
                try:
                    if len(batch) == 1:
                        await batch[0].delete(reason=f"Bulk deletion confirmed by {interaction.user} ({interaction.user.id})")
                    else:
                        await channel.delete_messages(batch, reason=f"Bulk deletion confirmed by {interaction.user} ({interaction.user.id})")
                    deleted.extend(batch)
                except discord.HTTPException:
                    LOGGER.warning("Bulk delete batch failed in channel %s; retrying individually", channel_id)
                    for message in batch:
                        try:
                            await message.delete(reason=f"Bulk deletion confirmed by {interaction.user} ({interaction.user.id})")
                            deleted.append(message)
                        except discord.HTTPException:
                            failed += 1
                            LOGGER.exception("Could not delete message %s", message.id)

            for message in old:
                try:
                    await message.delete(reason=f"Bulk deletion confirmed by {interaction.user} ({interaction.user.id})")
                    deleted.append(message)
                except discord.HTTPException:
                    failed += 1
                    LOGGER.exception("Could not delete old message %s", message.id)

        deleted.sort(key=lambda item: item.created_at)
        dm_sent = False
        files = _transcript_files(deleted, getattr(channel, "name", str(channel))) if deleted else []
        try:
            for index, transcript_file in enumerate(files):
                summary = (
                    f"Bulk deletion transcript for {getattr(channel, 'mention', '#unknown')}: "
                    f"{len(deleted)} deleted, {len(protected)} skipped with {PROTECTED_EMOJI}, {failed} failed."
                    if index == 0 else None
                )
                await interaction.user.send(summary, file=transcript_file)
            dm_sent = bool(files)
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.exception("Could not DM bulk deletion transcript to user %s", interaction.user.id)

        result = (
            f"Deleted **{len(deleted)}** messages. Skipped **{len(protected)}** with {PROTECTED_EMOJI}."
            + (f" **{failed}** messages could not be deleted." if failed else "")
        )
        if dm_sent:
            result += " I sent the transcript to your DMs."
            await interaction.followup.send(result, ephemeral=True)
        elif files:
            result += " I could not DM you, so the transcript is attached here instead."
            await interaction.followup.send(result, ephemeral=True)
            for transcript_file in _transcript_files(deleted, getattr(channel, "name", str(channel))):
                await interaction.followup.send(file=transcript_file, ephemeral=True)
        else:
            await interaction.followup.send(result + " There were no deleted messages to transcribe.", ephemeral=True)

    @bulkdelete.error
    async def bulkdelete_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CheckFailure):
            message = "You are not authorised to use this command."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
            return
        LOGGER.exception("Unhandled /bulkdelete error", exc_info=error)
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BulkDelete(bot))
