import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import MAIN_GUILD_ID
from data_paths import data_path

GUILD_ID = MAIN_GUILD_ID

LOA_ROLE_ID = 1099610910097686569
OUT_OF_OFFICE_SETUP_ROLE_IDS = {
    1097946662942560407,
    1097946543065137183,
    1213495462632361994,
    1098342493461942372,
    1098342675389890670,
    1098342769468125214,
}

TIMEZONE_NAME = "Europe/London"
STATE_FILE = data_path("out_of_office_state.json")
REPLY_COOLDOWN_SECONDS = 900
OFFLINE_REPLY_DELAY = timedelta(hours=6)
MAX_SHORT_LOA_DURATION = timedelta(hours=10)
MIN_FORM_LOA_DURATION = timedelta(hours=10)
LOA_CHANNEL_ID = 1099608133267095612

LOCAL_TZ = ZoneInfo(TIMEZONE_NAME)
DEFAULT_WEEKDAYS = [0, 1, 2, 3, 4, 5, 6]
WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKDAY_ALIASES = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "weds": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_local(value: datetime) -> str:
    return value.astimezone(LOCAL_TZ).strftime("%d/%m/%Y %H:%M")


def parse_local_datetime(text: str) -> datetime | None:
    raw = text.strip()
    for fmt in ("%d/%m/%Y %H:%M", "%d %m %Y %H:%M"):
        try:
            parsed = datetime.strptime(raw, fmt)
            return parsed.replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def parse_clock(text: str) -> tuple[int, int] | None:
    raw = text.strip().lower().replace(" ", "")
    for fmt in ("%H:%M", "%H%M", "%I:%M%p", "%I%p"):
        try:
            parsed = datetime.strptime(raw, fmt)
            return parsed.hour, parsed.minute
        except ValueError:
            continue
    return None


def parse_weekdays(text: str) -> list[int] | None:
    raw = text.strip().lower()
    if not raw:
        return None

    compact = raw.replace(" ", "")
    if compact in {"everyday", "everydays", "daily", "alldays", "all"} or raw == "every day":
        return list(DEFAULT_WEEKDAYS)
    if compact == "weekdays":
        return [0, 1, 2, 3, 4]
    if compact == "weekends":
        return [5, 6]

    normalized = raw.replace("/", ",").replace("|", ",")
    tokens = [token.strip().lower() for token in normalized.split(",")]
    selected: list[int] = []

    for token in tokens:
        if not token:
            continue
        token = token.replace(" ", "")

        if "-" in token:
            start_name, end_name = token.split("-", 1)
            start_day = WEEKDAY_ALIASES.get(start_name)
            end_day = WEEKDAY_ALIASES.get(end_name)
            if start_day is None or end_day is None:
                return None

            day = start_day
            while True:
                if day not in selected:
                    selected.append(day)
                if day == end_day:
                    break
                day = (day + 1) % 7
            continue

        weekday = WEEKDAY_ALIASES.get(token)
        if weekday is None:
            return None
        if weekday not in selected:
            selected.append(weekday)

    return sorted(selected) if selected else None


def _can_manage_out_of_office(interaction: discord.Interaction) -> bool:
    if interaction.guild_id != GUILD_ID:
        return False
    if not isinstance(interaction.user, discord.Member):
        return False
    return any(role.id in OUT_OF_OFFICE_SETUP_ROLE_IDS for role in interaction.user.roles)


@app_commands.guilds(discord.Object(id=GUILD_ID))
class OutOfOffice(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = self._load_state()
        self.dm_sessions: dict[int, dict] = {}
        self.reply_cooldowns: dict[str, str] = {}
        self._guild_sync_done = False
        self.reconcile_roles.start()

    def cog_unload(self) -> None:
        self.reconcile_roles.cancel()

    def _load_state(self) -> dict:
        try:
            if not os.path.exists(STATE_FILE):
                return {"version": 1, "users": {}, "preferences": {}}
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {"version": 1, "users": {}, "preferences": {}}
            data.setdefault("version", 1)
            data.setdefault("users", {})
            data.setdefault("preferences", {})
            return data
        except Exception:
            return {"version": 1, "users": {}, "preferences": {}}

    def _save_state(self) -> None:
        self.state["updated_at"] = utc_now().isoformat()
        tmp_path = f"{STATE_FILE}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, STATE_FILE)

    def _user_entries(self, user_id: int) -> list[dict]:
        return self.state.setdefault("users", {}).setdefault(str(user_id), [])

    def _user_preferences(self, user_id: int) -> dict:
        return self.state.setdefault("preferences", {}).setdefault(str(user_id), {})

    def _prune_user_preferences(self, user_id: int) -> None:
        preferences = self.state.setdefault("preferences", {})
        raw_user_id = str(user_id)
        user_preferences = preferences.get(raw_user_id)
        if not user_preferences:
            preferences.pop(raw_user_id, None)

    def _responses_enabled(self, user_id: int) -> bool:
        user_preferences = self.state.setdefault("preferences", {}).get(str(user_id), {})
        return user_preferences.get("responses_enabled", True)

    def _set_responses_enabled(self, user_id: int, enabled: bool) -> bool:
        user_preferences = self._user_preferences(user_id)
        previous = user_preferences.get("responses_enabled", True)
        if previous == enabled:
            return False

        if enabled:
            user_preferences.pop("responses_enabled", None)
        else:
            user_preferences["responses_enabled"] = False

        self._prune_user_preferences(user_id)
        return True

    def _offline_since(self, user_id: int) -> datetime | None:
        user_preferences = self.state.setdefault("preferences", {}).get(str(user_id), {})
        raw_value = user_preferences.get("offline_since")
        if not isinstance(raw_value, str):
            return None

        try:
            return parse_iso_utc(raw_value)
        except ValueError:
            return None

    def _set_offline_since(self, user_id: int, value: datetime | None) -> bool:
        user_preferences = self._user_preferences(user_id)
        previous = user_preferences.get("offline_since")
        next_value = value.isoformat() if value is not None else None

        if previous == next_value:
            return False

        if next_value is None:
            user_preferences.pop("offline_since", None)
        else:
            user_preferences["offline_since"] = next_value

        self._prune_user_preferences(user_id)
        return True

    def _entry_duration(self, entry: dict) -> timedelta:
        if entry["kind"] == "one_off":
            return parse_iso_utc(entry["end_at"]) - parse_iso_utc(entry["start_at"])

        start_minutes = entry["start_hour"] * 60 + entry["start_minute"]
        end_minutes = entry["end_hour"] * 60 + entry["end_minute"]
        if end_minutes > start_minutes:
            return timedelta(minutes=end_minutes - start_minutes)
        return timedelta(minutes=(24 * 60 - start_minutes) + end_minutes)

    def _one_off_duration(self, start_at: datetime, end_at: datetime) -> timedelta:
        return end_at - start_at

    def _is_allowed_one_off_loa(self, start_at: datetime, end_at: datetime) -> bool:
        duration = self._one_off_duration(start_at, end_at)
        return duration <= MAX_SHORT_LOA_DURATION or duration > MIN_FORM_LOA_DURATION

    def _entry_weekdays(self, entry: dict) -> list[int]:
        raw_weekdays = entry.get("weekdays")
        if not isinstance(raw_weekdays, list):
            return list(DEFAULT_WEEKDAYS)

        cleaned = sorted({int(day) for day in raw_weekdays if isinstance(day, int) and 0 <= day <= 6})
        return cleaned or list(DEFAULT_WEEKDAYS)

    def _format_weekdays(self, entry: dict) -> str:
        weekdays = self._entry_weekdays(entry)
        if weekdays == DEFAULT_WEEKDAYS:
            return "Every day"
        if weekdays == [0, 1, 2, 3, 4]:
            return "Weekdays"
        if weekdays == [5, 6]:
            return "Weekends"
        return ", ".join(WEEKDAY_LABELS[day] for day in weekdays)

    def _daily_window(self, entry: dict, now_local: datetime) -> tuple[datetime, datetime]:
        start_local = now_local.replace(
            hour=entry["start_hour"], minute=entry["start_minute"], second=0, microsecond=0
        )
        end_local = now_local.replace(
            hour=entry["end_hour"], minute=entry["end_minute"], second=0, microsecond=0
        )

        if (entry["end_hour"], entry["end_minute"]) > (entry["start_hour"], entry["start_minute"]):
            return start_local, end_local

        if now_local.time() >= start_local.time():
            return start_local, end_local + timedelta(days=1)

        return start_local - timedelta(days=1), end_local

    def _active_entries_for_user(self, user_id: int) -> list[dict]:
        now_utc = utc_now()
        now_local = now_utc.astimezone(LOCAL_TZ)
        active: list[dict] = []

        for entry in self.state.get("users", {}).get(str(user_id), []):
            if not entry.get("enabled", True):
                continue

            if entry["kind"] == "one_off":
                start_at = parse_iso_utc(entry["start_at"])
                end_at = parse_iso_utc(entry["end_at"])
                if start_at <= now_utc < end_at:
                    active.append(entry)
                continue

            start_local, end_local = self._daily_window(entry, now_local)
            if start_local.weekday() in self._entry_weekdays(entry) and start_local <= now_local < end_local:
                active.append(entry)

        return active

    def _role_for_active_entries(self, entries: list[dict]) -> int | None:
        if not entries:
            return None
        return LOA_ROLE_ID

    def _primary_entry(self, entries: list[dict]) -> dict | None:
        if not entries:
            return None
        return max(entries, key=self._entry_duration)

    def _entry_is_long_loa(self, entry: dict) -> bool:
        return self._entry_duration(entry) > MIN_FORM_LOA_DURATION

    def _member_has_loa_role(self, member: discord.Member) -> bool:
        return any(role.id == LOA_ROLE_ID for role in member.roles)

    def _reply_delay_elapsed(self, member: discord.Member) -> bool:
        if member.status != discord.Status.offline:
            if self._set_offline_since(member.id, None):
                self._save_state()
            return False

        offline_since = self._offline_since(member.id)
        if offline_since is None:
            if self._set_offline_since(member.id, utc_now()):
                self._save_state()
            return False

        return utc_now() - offline_since >= OFFLINE_REPLY_DELAY

    def _entry_summary(self, entry: dict) -> str:
        if entry["kind"] == "one_off":
            role_name = "LOA"
            return (
                f"[{entry['id']}] one-off {role_name}: "
                f"{format_local(parse_iso_utc(entry['start_at']))} -> "
                f"{format_local(parse_iso_utc(entry['end_at']))} ({TIMEZONE_NAME})\n"
                f"Message: {entry['message']}"
            )

        return (
            f"[{entry['id']}] daily LOA: "
            f"{self._format_weekdays(entry)} | "
            f"{entry['start_hour']:02d}:{entry['start_minute']:02d} -> "
            f"{entry['end_hour']:02d}:{entry['end_minute']:02d} ({TIMEZONE_NAME})\n"
            f"Message: {entry['message']}"
        )

    def _build_auto_reply(self, member: discord.Member, entry: dict) -> str:
        if entry["kind"] == "one_off":
            status_label = "LOA"
            end_text = format_local(parse_iso_utc(entry["end_at"]))
            return (
                f"{member.display_name} is currently {status_label} until {end_text} {TIMEZONE_NAME}.\n"
                f"Message: {entry['message']}"
            )

        return (
            f"{member.display_name} is currently on LOA on a recurring schedule ({self._format_weekdays(entry)}) from "
            f"{entry['start_hour']:02d}:{entry['start_minute']:02d} to "
            f"{entry['end_hour']:02d}:{entry['end_minute']:02d} {TIMEZONE_NAME}.\n"
            f"Message: {entry['message']}"
        )

    def _build_generic_role_reply(self, member: discord.Member) -> str | None:
        if self._member_has_loa_role(member):
            return (
                f"{member.display_name} is currently away and has an LOA tag. "
            )
        return None

    def _cooldown_key(self, author_id: int, target_id: int, channel_id: int) -> str:
        return f"{author_id}:{target_id}:{channel_id}"

    def _cooldown_allows_reply(self, author_id: int, target_id: int, channel_id: int) -> bool:
        key = self._cooldown_key(author_id, target_id, channel_id)
        previous = self.reply_cooldowns.get(key)
        if not previous:
            self.reply_cooldowns[key] = utc_now().isoformat()
            return True

        previous_dt = parse_iso_utc(previous)
        if (utc_now() - previous_dt).total_seconds() >= REPLY_COOLDOWN_SECONDS:
            self.reply_cooldowns[key] = utc_now().isoformat()
            return True

        return False

    def _prune_expired_one_offs(self) -> None:
        now_utc = utc_now()
        changed = False

        for user_id in list(self.state.get("users", {})):
            entries = self.state["users"].get(user_id, [])
            kept: list[dict] = []

            for entry in entries:
                if entry.get("kind") != "one_off":
                    kept.append(entry)
                    continue

                if parse_iso_utc(entry["end_at"]) <= now_utc:
                    changed = True
                    continue

                kept.append(entry)

            if kept:
                self.state["users"][user_id] = kept
            else:
                self.state["users"].pop(user_id, None)
                changed = True

        if changed:
            self._save_state()

    async def _get_member(self, guild: discord.Guild, user_id: int) -> discord.Member | None:
        member = guild.get_member(user_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    async def _sync_member_roles(self, guild: discord.Guild, user_id: int) -> None:
        member = await self._get_member(guild, user_id)
        if member is None or member.bot:
            return

        desired_role_id = self._role_for_active_entries(self._active_entries_for_user(user_id))
        loa_role = guild.get_role(LOA_ROLE_ID)
        to_add: list[discord.Role] = []
        to_remove: list[discord.Role] = []

        if desired_role_id == LOA_ROLE_ID:
            if loa_role and loa_role not in member.roles:
                to_add.append(loa_role)
        else:
            if loa_role and loa_role in member.roles:
                to_remove.append(loa_role)

        if to_remove:
            try:
                await member.remove_roles(*to_remove, reason="LOA reconciliation")
            except (discord.Forbidden, discord.HTTPException):
                pass

        if to_add:
            try:
                await member.add_roles(*to_add, reason="LOA reconciliation")
            except (discord.Forbidden, discord.HTTPException):
                pass

    async def _post_loa_confirmation(self, guild: discord.Guild, user_id: int, entry: dict) -> None:
        channel = guild.get_channel(LOA_CHANNEL_ID) or self.bot.get_channel(LOA_CHANNEL_ID)
        if channel is None or not isinstance(channel, discord.abc.Messageable):
            return

        member = await self._get_member(guild, user_id)
        display_name = member.mention if member is not None else f"<@{user_id}>"

        await channel.send(
            "Automated LOA submission\n"
            f"Member: {display_name}\n"
            f"Start: {format_local(parse_iso_utc(entry['start_at']))} {TIMEZONE_NAME}\n"
            f"End: {format_local(parse_iso_utc(entry['end_at']))} {TIMEZONE_NAME}\n"
            f"Message: {entry['message']}"
        )

    async def _start_dm_setup(self, user: discord.abc.User, *, reset: bool = True) -> bool:
        try:
            dm_channel = user.dm_channel or await user.create_dm()
        except discord.HTTPException:
            return False

        if reset or user.id not in self.dm_sessions:
            self.dm_sessions[user.id] = {"step": "kind", "draft": {}}

        await dm_channel.send(
            "**LOA** :beach: can be set up here in two ways.\n\n"
            "**Short LOA**: this covers one-off, daily, or weekday-based absences that are **10 hours total or less**. Example: `8am - 12pm` starting `30/03/2026` every weekday. Short LOAs still use the **LOA role**, but they do **not** post a confirmation in <#1099608133267095612>.\n\n"
            "**Long LOA**: this covers a single continuous block that is **more than 10 hours** long. Example: `12pm 30/03/2026` until `1pm 01/05/2026`. Long LOAs **do** post a confirmation in <#1099608133267095612> for SNCO review, so treat this as an automated LOA form.\n\n"
            "**Manually adding/removing the LOA role** is still completely possible. You can add it to yourself or other people (on request), and the bot will send a generic message when that person is tagged if they have not set a custom away message. Automated replies only start once you have been offline for **6 hours**.\n\n"
            f"Automated responses are currently **{'on' if self._responses_enabled(user.id) else 'off'}**.\n\n"
            "Reply with `one` for a one-off schedule, `daily` for a recurring daily or weekday schedule, `off` to disable automated responses completely, or `on` to re-enable them."
        )
        return True

    async def _handle_dm_message(self, message: discord.Message) -> None:
        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
            await message.channel.send("The configured server is not available right now.")
            return

        content = message.content.strip()
        lowered = content.lower()

        if message.author.id not in self.dm_sessions:
            return

        if lowered == "cancel":
            self.dm_sessions.pop(message.author.id, None)
            await message.channel.send("LOA setup cancelled.")
            return

        session = self.dm_sessions[message.author.id]
        draft = session["draft"]

        if session["step"] == "kind":
            if lowered in {"off", "on"}:
                changed = self._set_responses_enabled(message.author.id, lowered == "on")
                if changed:
                    self._save_state()
                self.dm_sessions.pop(message.author.id, None)
                await message.channel.send(
                    "Automated LOA responses are now disabled."
                    if lowered == "off"
                    else "Automated LOA responses are now enabled."
                )
                return

            if lowered not in {"one", "daily"}:
                await message.channel.send("Reply with `one`, `daily`, `off`, or `on`.")
                return

            draft["kind"] = "one_off" if lowered == "one" else "daily"
            if draft["kind"] == "one_off":
                session["step"] = "one_start"
                await message.channel.send(
                    "Send the local start date/time. Example: `30/03/2026 23:28`."
                )
            else:
                session["step"] = "daily_start"
                await message.channel.send(
                    "Send the daily start time. Examples: `10:00`, `1pm`, `1:30pm`."
                )
            return

        if session["step"] == "one_start":
            start_at = parse_local_datetime(content)
            if start_at is None:
                await message.channel.send("Invalid date/time. Use `DD/MM/YYYY HH:MM`.")
                return
            draft["start_at"] = start_at.isoformat()
            session["step"] = "one_end"
            await message.channel.send("Send the local end date/time.")
            return

        if session["step"] == "one_end":
            end_at = parse_local_datetime(content)
            if end_at is None:
                await message.channel.send("Invalid date/time. Use `DD/MM/YYYY HH:MM`.")
                return

            start_at = parse_iso_utc(draft["start_at"])
            if end_at <= start_at:
                await message.channel.send("End time must be after the start time.")
                return

            if not self._is_allowed_one_off_loa(start_at, end_at):
                await message.channel.send(
                    "That period is not allowed. LOAs in this system must be 10 hours or less, or more than 10 hours."
                )
                return

            draft["end_at"] = end_at.isoformat()
            session["step"] = "message"
            await message.channel.send("Send the custom away message people should receive.")
            return

        if session["step"] == "daily_start":
            parsed = parse_clock(content)
            if parsed is None:
                await message.channel.send("Invalid time. Try `10:00`, `1pm`, or `1:30pm`.")
                return
            draft["start_hour"], draft["start_minute"] = parsed
            session["step"] = "daily_end"
            await message.channel.send("Send the daily end time.")
            return

        if session["step"] == "daily_end":
            parsed = parse_clock(content)
            if parsed is None:
                await message.channel.send("Invalid time. Try `13:00` or `6pm`.")
                return
            end_hour, end_minute = parsed
            if (end_hour, end_minute) == (draft["start_hour"], draft["start_minute"]):
                await message.channel.send("Start and end time cannot be the same.")
                return
            draft["end_hour"] = end_hour
            draft["end_minute"] = end_minute

            duration = self._entry_duration(
                {
                    "kind": "daily",
                    "start_hour": draft["start_hour"],
                    "start_minute": draft["start_minute"],
                    "end_hour": draft["end_hour"],
                    "end_minute": draft["end_minute"],
                }
            )
            if duration > MAX_SHORT_LOA_DURATION:
                await message.channel.send(
                    "Recurring LOAs must be 10 hours total length or less."
                )
                return

            session["step"] = "daily_weekdays"
            await message.channel.send(
                "Send the weekdays for this recurring schedule. Examples: `Mon, Tue, Wed`, `Mon-Fri`, `weekdays`, `weekends`, or `every day`."
            )
            return

        if session["step"] == "daily_weekdays":
            weekdays = parse_weekdays(content)
            if weekdays is None:
                await message.channel.send(
                    "Invalid weekdays. Use something like `Mon, Tue, Wed`, `Mon-Fri`, `weekdays`, `weekends`, or `every day`."
                )
                return
            draft["weekdays"] = weekdays
            session["step"] = "message"
            await message.channel.send("Send the custom away message people should receive.")
            return

        if session["step"] == "message":
            if not content:
                await message.channel.send("The away message cannot be empty.")
                return

            draft["message"] = content
            session["step"] = "confirm"

            if draft["kind"] == "one_off":
                start_at = parse_iso_utc(draft["start_at"])
                end_at = parse_iso_utc(draft["end_at"])
                preview = (
                    "One-off LOA preview\n"
                    f"Start: {format_local(start_at)} {TIMEZONE_NAME}\n"
                    f"End: {format_local(end_at)} {TIMEZONE_NAME}\n"
                    "Role during period: LOA\n"
                    f"Message: {draft['message']}\n\n"
                    "Reply `confirm` to save or `cancel` to abort."
                )
            else:
                weekday_labels = self._format_weekdays({"weekdays": draft.get("weekdays", DEFAULT_WEEKDAYS)})
                preview = (
                    "Daily LOA preview\n"
                    f"Days: {weekday_labels}\n"
                    f"Time: {draft['start_hour']:02d}:{draft['start_minute']:02d} -> "
                    f"{draft['end_hour']:02d}:{draft['end_minute']:02d} {TIMEZONE_NAME}\n"
                    "Role during period: LOA\n"
                    f"Message: {draft['message']}\n\n"
                    "Reply `confirm` to save or `cancel` to abort."
                )

            await message.channel.send(preview)
            return

        if session["step"] == "confirm":
            if lowered != "confirm":
                await message.channel.send("Reply `confirm` to save or `cancel` to abort.")
                return

            entry = {
                "id": uuid.uuid4().hex[:8],
                "kind": draft["kind"],
                "message": draft["message"],
                "enabled": True,
                "created_at": utc_now().isoformat(),
            }

            if draft["kind"] == "one_off":
                entry["start_at"] = draft["start_at"]
                entry["end_at"] = draft["end_at"]
            else:
                entry["start_hour"] = draft["start_hour"]
                entry["start_minute"] = draft["start_minute"]
                entry["end_hour"] = draft["end_hour"]
                entry["end_minute"] = draft["end_minute"]
                entry["weekdays"] = draft.get("weekdays", list(DEFAULT_WEEKDAYS))

            self._user_entries(message.author.id).append(entry)
            self._save_state()
            self.dm_sessions.pop(message.author.id, None)
            await self._sync_member_roles(guild, message.author.id)

            if draft["kind"] == "one_off":
                start_at = parse_iso_utc(entry["start_at"])
                end_at = parse_iso_utc(entry["end_at"])
                if self._one_off_duration(start_at, end_at) > MIN_FORM_LOA_DURATION:
                    await self._post_loa_confirmation(guild, message.author.id, entry)

            await message.channel.send(
                f"Saved LOA schedule `{entry['id']}`."
            )

    @tasks.loop(minutes=1)
    async def reconcile_roles(self) -> None:
        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
            return

        self._prune_expired_one_offs()

        for raw_user_id in list(self.state.get("users", {})):
            try:
                user_id = int(raw_user_id)
            except ValueError:
                continue
            await self._sync_member_roles(guild, user_id)

    @reconcile_roles.before_loop
    async def before_reconcile_roles(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._guild_sync_done:
            return

        try:
            await self.bot.tree.sync(guild=discord.Object(id=GUILD_ID))
            self._guild_sync_done = True
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member) -> None:
        if after.guild.id != GUILD_ID or after.bot:
            return

        if before.status == after.status:
            return

        changed = False
        if after.status == discord.Status.offline:
            changed = self._set_offline_since(after.id, utc_now())
        else:
            changed = self._set_offline_since(after.id, None)

        if changed:
            self._save_state()

    async def schedule_id_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        entries = self.state.get("users", {}).get(str(interaction.user.id), [])
        choices: list[app_commands.Choice[str]] = []

        for entry in entries:
            entry_id = str(entry.get("id", ""))
            if current.lower() not in entry_id.lower():
                continue
            choices.append(app_commands.Choice(name=self._entry_summary(entry)[:100], value=entry_id))

        return choices[:25]

    @app_commands.command(name="loa", description="Start the LOA setup in DM.")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.guild_only()
    @app_commands.check(_can_manage_out_of_office)
    async def outofoffice(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        started = await self._start_dm_setup(interaction.user)
        if not started:
            await interaction.followup.send(
                "I couldn't DM you. Enable DMs from this server and try again.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            "I sent you a DM to set up your LOA.",
            ephemeral=True,
        )

    @app_commands.command(name="loa-list", description="List your saved LOA schedules.")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.guild_only()
    async def outofoffice_list(self, interaction: discord.Interaction) -> None:
        entries = self.state.get("users", {}).get(str(interaction.user.id), [])
        if not entries:
            await interaction.response.send_message(
                "You do not have any saved LOA schedules.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "\n\n".join(self._entry_summary(entry) for entry in entries),
            ephemeral=True,
        )

    @app_commands.command(name="loa_responseoff", description="Disable your automated LOA replies.")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.guild_only()
    async def outofoffice_responseoff(self, interaction: discord.Interaction) -> None:
        changed = self._set_responses_enabled(interaction.user.id, False)
        if changed:
            self._save_state()

        await interaction.response.send_message(
            "Your automated LOA replies are now disabled.",
            ephemeral=True,
        )

    @app_commands.command(name="loa_responseon", description="Re-enable your automated LOA replies.")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.guild_only()
    async def outofoffice_responseon(self, interaction: discord.Interaction) -> None:
        changed = self._set_responses_enabled(interaction.user.id, True)
        if changed:
            self._save_state()

        await interaction.response.send_message(
            "Your automated LOA replies are now enabled. Replies will resume once you have been offline for 6 hours.",
            ephemeral=True,
        )

    @app_commands.command(name="loa-delete", description="Delete one of your saved LOA schedules.")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.guild_only()
    @app_commands.autocomplete(entry_id=schedule_id_autocomplete)
    async def outofoffice_delete(self, interaction: discord.Interaction, entry_id: str) -> None:
        entries = self.state.get("users", {}).get(str(interaction.user.id), [])
        remaining = [entry for entry in entries if entry.get("id") != entry_id]

        if len(remaining) == len(entries):
            await interaction.response.send_message(
                f"No schedule found with id `{entry_id}`.",
                ephemeral=True,
            )
            return

        if remaining:
            self.state.setdefault("users", {})[str(interaction.user.id)] = remaining
        else:
            self.state.setdefault("users", {}).pop(str(interaction.user.id), None)

        self._save_state()

        guild = interaction.guild or self.bot.get_guild(GUILD_ID)
        if guild is not None:
            await self._sync_member_roles(guild, interaction.user.id)

        await interaction.response.send_message(
            f"Deleted LOA schedule `{entry_id}`.",
            ephemeral=True,
        )

    @outofoffice.error
    async def outofoffice_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CheckFailure):
            if interaction.response.is_done():
                await interaction.followup.send(
                    "You need one of the configured setup roles to use `/loa`.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "You need one of the configured setup roles to use `/loa`.",
                    ephemeral=True,
                )
            return
        raise error

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        if isinstance(message.channel, discord.DMChannel):
            if message.author.id in self.dm_sessions:
                await self._handle_dm_message(message)
            return

        if not message.guild or message.guild.id != GUILD_ID:
            return

        if message.mention_everyone:
            return

        replies: list[str] = []
        seen_ids: set[int] = set()

        for member in message.mentions:
            if member.bot or member.id in seen_ids:
                continue
            seen_ids.add(member.id)

            active_entries = self._active_entries_for_user(member.id)
            generic_reply = self._build_generic_role_reply(member)

            if not active_entries and generic_reply is None:
                continue

            if not self._responses_enabled(member.id):
                continue

            if not self._reply_delay_elapsed(member):
                continue

            if active_entries:
                entry = self._primary_entry(active_entries)
                if entry is None:
                    continue
                if not self._cooldown_allows_reply(message.author.id, member.id, message.channel.id):
                    continue
                replies.append(self._build_auto_reply(member, entry))
                continue

            if generic_reply:
                if not self._cooldown_allows_reply(message.author.id, member.id, message.channel.id):
                    continue
                replies.append(generic_reply)

        if replies:
            await message.reply(
                "\n\n".join(replies),
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OutOfOffice(bot))