import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from data_paths import data_path

GUILD_ID = 1097913605082579024

OUT_OF_OFFICE_ROLE_ID = 1488294029757120613
LOA_ROLE_ID = 1099610910097686569
OUT_OF_OFFICE_SETUP_ROLE_ID = 333333333333333333

TIMEZONE_NAME = "Europe/London"
STATE_FILE = data_path("out_of_office_state.json")
REPLY_COOLDOWN_SECONDS = 900

LOCAL_TZ = ZoneInfo(TIMEZONE_NAME)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_local(value: datetime) -> str:
    return value.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M")


def parse_local_datetime(text: str) -> datetime | None:
    raw = text.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M"):
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


def _can_manage_out_of_office(interaction: discord.Interaction) -> bool:
    if interaction.guild_id != GUILD_ID:
        return False
    if not isinstance(interaction.user, discord.Member):
        return False
    return any(role.id == OUT_OF_OFFICE_SETUP_ROLE_ID for role in interaction.user.roles)


@app_commands.guilds(discord.Object(id=GUILD_ID))
class OutOfOffice(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = self._load_state()
        self.dm_sessions: dict[int, dict] = {}
        self.reply_cooldowns: dict[str, str] = {}
        self.reconcile_roles.start()

    def cog_unload(self) -> None:
        self.reconcile_roles.cancel()

    def _load_state(self) -> dict:
        try:
            if not os.path.exists(STATE_FILE):
                return {"version": 1, "users": {}}
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {"version": 1, "users": {}}
            data.setdefault("version", 1)
            data.setdefault("users", {})
            return data
        except Exception:
            return {"version": 1, "users": {}}

    def _save_state(self) -> None:
        self.state["updated_at"] = utc_now().isoformat()
        tmp_path = f"{STATE_FILE}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, STATE_FILE)

    def _user_entries(self, user_id: int) -> list[dict]:
        return self.state.setdefault("users", {}).setdefault(str(user_id), [])

    def _entry_duration(self, entry: dict) -> timedelta:
        if entry["kind"] == "one_off":
            return parse_iso_utc(entry["end_at"]) - parse_iso_utc(entry["start_at"])

        start_minutes = entry["start_hour"] * 60 + entry["start_minute"]
        end_minutes = entry["end_hour"] * 60 + entry["end_minute"]
        if end_minutes > start_minutes:
            return timedelta(minutes=end_minutes - start_minutes)
        return timedelta(minutes=(24 * 60 - start_minutes) + end_minutes)

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
            if start_local <= now_local < end_local:
                active.append(entry)

        return active

    def _role_for_active_entries(self, entries: list[dict]) -> int | None:
        if not entries:
            return None
        longest_duration = max((self._entry_duration(entry) for entry in entries), default=timedelta())
        if longest_duration > timedelta(days=1):
            return LOA_ROLE_ID
        return OUT_OF_OFFICE_ROLE_ID

    def _primary_entry(self, entries: list[dict]) -> dict | None:
        if not entries:
            return None
        return max(entries, key=self._entry_duration)

    def _entry_summary(self, entry: dict) -> str:
        if entry["kind"] == "one_off":
            role_name = "LOA" if self._entry_duration(entry) > timedelta(days=1) else "OOO"
            return (
                f"[{entry['id']}] one-off {role_name}: "
                f"{format_local(parse_iso_utc(entry['start_at']))} -> "
                f"{format_local(parse_iso_utc(entry['end_at']))} ({TIMEZONE_NAME})\n"
                f"Message: {entry['message']}"
            )

        return (
            f"[{entry['id']}] daily OOO: "
            f"{entry['start_hour']:02d}:{entry['start_minute']:02d} -> "
            f"{entry['end_hour']:02d}:{entry['end_minute']:02d} ({TIMEZONE_NAME})\n"
            f"Message: {entry['message']}"
        )

    def _build_auto_reply(self, member: discord.Member, entry: dict) -> str:
        if entry["kind"] == "one_off":
            status_label = "LOA" if self._entry_duration(entry) > timedelta(days=1) else "out of office"
            end_text = format_local(parse_iso_utc(entry["end_at"]))
            return (
                f"{member.display_name} is currently {status_label} until {end_text} {TIMEZONE_NAME}.\n"
                f"Message: {entry['message']}"
            )

        return (
            f"{member.display_name} is currently out of office on a daily schedule from "
            f"{entry['start_hour']:02d}:{entry['start_minute']:02d} to "
            f"{entry['end_hour']:02d}:{entry['end_minute']:02d} {TIMEZONE_NAME}.\n"
            f"Message: {entry['message']}"
        )

    def _build_generic_role_reply(self, member: discord.Member) -> str | None:
        role_ids = {role.id for role in member.roles}
        if LOA_ROLE_ID in role_ids:
            return (
                f"{member.display_name} is currently on LOA. "
                "They have not set a custom out-of-office message with the bot."
            )
        if OUT_OF_OFFICE_ROLE_ID in role_ids:
            return (
                f"{member.display_name} is currently out of office. "
                "They have not set a custom out-of-office message with the bot."
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
        ooo_role = guild.get_role(OUT_OF_OFFICE_ROLE_ID)
        loa_role = guild.get_role(LOA_ROLE_ID)
        to_add: list[discord.Role] = []
        to_remove: list[discord.Role] = []

        if desired_role_id == OUT_OF_OFFICE_ROLE_ID:
            if loa_role and loa_role in member.roles:
                to_remove.append(loa_role)
            if ooo_role and ooo_role not in member.roles:
                to_add.append(ooo_role)
        elif desired_role_id == LOA_ROLE_ID:
            if ooo_role and ooo_role in member.roles:
                to_remove.append(ooo_role)
            if loa_role and loa_role not in member.roles:
                to_add.append(loa_role)
        else:
            if ooo_role and ooo_role in member.roles:
                to_remove.append(ooo_role)
            if loa_role and loa_role in member.roles:
                to_remove.append(loa_role)

        if to_remove:
            try:
                await member.remove_roles(*to_remove, reason="Out of office reconciliation")
            except (discord.Forbidden, discord.HTTPException):
                pass

        if to_add:
            try:
                await member.add_roles(*to_add, reason="Out of office reconciliation")
            except (discord.Forbidden, discord.HTTPException):
                pass

    async def _send_dm_help(self, channel: discord.DMChannel) -> None:
        await channel.send(
            "Out of office setup is active in this DM.\n\n"
            "Commands while not in setup:\n"
            "- `list` to show your saved schedules\n"
            "- `delete <id>` to remove a schedule\n"
            "- `cancel` to exit a running setup\n\n"
            "Accepted date/time format for one-off schedules:\n"
            "- `2026-04-02 10:00`\n"
            "- `02/04/2026 10:00`\n\n"
            "Accepted time format for daily schedules:\n"
            "- `10:00`\n"
            "- `1pm`\n"
            "- `1:30pm`"
        )

    async def _start_dm_setup(self, user: discord.abc.User, *, reset: bool = True) -> bool:
        try:
            dm_channel = user.dm_channel or await user.create_dm()
        except discord.HTTPException:
            return False

        if reset or user.id not in self.dm_sessions:
            self.dm_sessions[user.id] = {"step": "kind", "draft": {}}

        await dm_channel.send(
            "Out-of-office setup started. Reply with `one` for a one-off schedule or `daily` for a recurring daily schedule."
        )
        return True

    async def _handle_dm_message(self, message: discord.Message) -> None:
        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
            await message.channel.send("The configured server is not available right now.")
            return

        content = message.content.strip()
        lowered = content.lower()

        if lowered in {"help", "/help"} and message.author.id not in self.dm_sessions:
            await self._send_dm_help(message.channel)
            return

        if lowered == "list" and message.author.id not in self.dm_sessions:
            entries = self.state.get("users", {}).get(str(message.author.id), [])
            if not entries:
                await message.channel.send("You do not have any saved out-of-office schedules.")
                return
            await message.channel.send("\n\n".join(self._entry_summary(entry) for entry in entries))
            return

        if lowered.startswith("delete ") and message.author.id not in self.dm_sessions:
            entry_id = content.split(" ", 1)[1].strip()
            entries = self.state.get("users", {}).get(str(message.author.id), [])
            remaining = [entry for entry in entries if entry["id"] != entry_id]
            if len(remaining) == len(entries):
                await message.channel.send(f"No schedule found with id `{entry_id}`.")
                return
            if remaining:
                self.state.setdefault("users", {})[str(message.author.id)] = remaining
            else:
                self.state.setdefault("users", {}).pop(str(message.author.id), None)
            self._save_state()
            await self._sync_member_roles(guild, message.author.id)
            await message.channel.send(f"Deleted out-of-office schedule `{entry_id}`.")
            return

        if message.author.id not in self.dm_sessions:
            return

        if lowered == "cancel":
            self.dm_sessions.pop(message.author.id, None)
            await message.channel.send("Out-of-office setup cancelled.")
            return

        session = self.dm_sessions[message.author.id]
        draft = session["draft"]

        if session["step"] == "kind":
            if lowered not in {"one", "daily"}:
                await message.channel.send("Reply with `one` or `daily`.")
                return

            draft["kind"] = "one_off" if lowered == "one" else "daily"
            if draft["kind"] == "one_off":
                session["step"] = "one_start"
                await message.channel.send(
                    "Send the local start date/time. Example: `2026-04-02 10:00`."
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
                await message.channel.send("Invalid date/time. Use `YYYY-MM-DD HH:MM` or `DD/MM/YYYY HH:MM`.")
                return
            draft["start_at"] = start_at.isoformat()
            session["step"] = "one_end"
            await message.channel.send("Send the local end date/time.")
            return

        if session["step"] == "one_end":
            end_at = parse_local_datetime(content)
            if end_at is None:
                await message.channel.send("Invalid date/time. Use `YYYY-MM-DD HH:MM` or `DD/MM/YYYY HH:MM`.")
                return

            start_at = parse_iso_utc(draft["start_at"])
            if end_at <= start_at:
                await message.channel.send("End time must be after the start time.")
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
                role_name = "LOA" if end_at - start_at > timedelta(days=1) else "OOO"
                preview = (
                    "One-off out-of-office preview\n"
                    f"Start: {format_local(start_at)} {TIMEZONE_NAME}\n"
                    f"End: {format_local(end_at)} {TIMEZONE_NAME}\n"
                    f"Role during period: {role_name}\n"
                    f"Message: {draft['message']}\n\n"
                    "Reply `confirm` to save or `cancel` to abort."
                )
            else:
                preview = (
                    "Daily out-of-office preview\n"
                    f"Time: {draft['start_hour']:02d}:{draft['start_minute']:02d} -> "
                    f"{draft['end_hour']:02d}:{draft['end_minute']:02d} {TIMEZONE_NAME}\n"
                    "Role during period: OOO\n"
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

            self._user_entries(message.author.id).append(entry)
            self._save_state()
            self.dm_sessions.pop(message.author.id, None)
            await self._sync_member_roles(guild, message.author.id)
            await message.channel.send(
                f"Saved out-of-office schedule `{entry['id']}`. Send `list` to review schedules or `delete <id>` to remove one."
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

    @app_commands.command(name="outofoffice", description="Start the out-of-office setup in DM.")
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
            "I sent you a DM to set up your out-of-office period.",
            ephemeral=True,
        )

    @outofoffice.error
    async def outofoffice_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CheckFailure):
            if interaction.response.is_done():
                await interaction.followup.send(
                    f"You need the configured role (`{OUT_OF_OFFICE_SETUP_ROLE_ID}`) to use `/outofoffice`.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"You need the configured role (`{OUT_OF_OFFICE_SETUP_ROLE_ID}`) to use `/outofoffice`.",
                    ephemeral=True,
                )
            return
        raise error

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        if isinstance(message.channel, discord.DMChannel):
            await self._handle_dm_message(message)
            return

        if not message.guild or message.guild.id != GUILD_ID:
            return

        if message.mention_everyone:
            return

        replies: list[str] = []
        seen_ids: set[int] = set()

        for member in message.mentions:
            if member.bot or member.id == message.author.id or member.id in seen_ids:
                continue
            seen_ids.add(member.id)

            if not self._cooldown_allows_reply(message.author.id, member.id, message.channel.id):
                continue

            active_entries = self._active_entries_for_user(member.id)
            if active_entries:
                entry = self._primary_entry(active_entries)
                if entry is None:
                    continue
                replies.append(self._build_auto_reply(member, entry))
                continue

            generic_reply = self._build_generic_role_reply(member)
            if generic_reply:
                replies.append(generic_reply)

        if replies:
            await message.reply(
                "\n\n".join(replies),
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OutOfOffice(bot))
            if lowered not in {"one", "daily"}:
                await message.channel.send("Reply with `one` or `daily`.")
                return

            draft["kind"] = "one_off" if lowered == "one" else "daily"

            if draft["kind"] == "one_off":
                session["step"] = "one_start"
                await message.channel.send(
                    "Send the local start date/time.\n"
                    "Example: `2026-04-02 10:00`"
                )
            else:
                session["step"] = "daily_start"
                await message.channel.send(
                    "Send the daily start time.\n"
                    "Examples: `10:00`, `1pm`, `1:30pm`"
                )
            return

        if session["step"] == "one_start":
            start_at = parse_local_datetime(content)
            if start_at is None:
                await message.channel.send("Invalid date/time. Use `YYYY-MM-DD HH:MM`.")
                return
            draft["start_at"] = start_at.isoformat()
            session["step"] = "one_end"
            await message.channel.send("Send the local end date/time.")
            return

        if session["step"] == "one_end":
            end_at = parse_local_datetime(content)
            if end_at is None:
                await message.channel.send("Invalid date/time. Use `YYYY-MM-DD HH:MM`.")
                return

            start_at = parse_iso_utc(draft["start_at"])
            if end_at <= start_at:
                await message.channel.send("End must be after start.")
                return

            draft["end_at"] = end_at.isoformat()
            session["step"] = "message"
            await message.channel.send("Send the custom away message people should receive.")
            return

        if session["step"] == "daily_start":
            parsed = parse_clock(content)
            if parsed is None:
                await message.channel.send("Invalid time. Try `10:00` or `1pm`.")
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
                await message.channel.send("Start and end cannot be the same.")
                return

            draft["end_hour"] = end_hour
            draft["end_minute"] = end_minute
            session["step"] = "message"
            await message.channel.send("Send the custom away message people should receive.")
            return

        if session["step"] == "message":
            if not content:
                await message.channel.send("Message cannot be empty.")
                return

            draft["message"] = content
            session["step"] = "confirm"

            if draft["kind"] == "one_off":
                preview = (
                    f"One-off schedule\n"
                    f"Start: {format_local(parse_iso_utc(draft['start_at']))} {TIMEZONE_NAME}\n"
                    f"End: {format_local(parse_iso_utc(draft['end_at']))} {TIMEZONE_NAME}\n"
                    f"Role during period: "
                    f"{'LOA' if (parse_iso_utc(draft['end_at']) - parse_iso_utc(draft['start_at'])) > timedelta(days=1) else 'OOO'}\n"
                    f"Message: {draft['message']}\n\n"
                    f"Reply `confirm` to save or `cancel`."
                )
            else:
                preview = (
                    f"Daily recurring schedule\n"
                    f"Time: {draft['start_hour']:02d}:{draft['start_minute']:02d} -> "
                    f"{draft['end_hour']:02d}:{draft['end_minute']:02d} {TIMEZONE_NAME}\n"
                    f"Role during period: OOO\n"
                    f"Message: {draft['message']}\n\n"
                    f"Reply `confirm` to save or `cancel`."
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

            self._user_entries(message.author.id).append(entry)
            self._save_state()
            self.dm_sessions.pop(message.author.id, None)

            await self._sync_member_roles(guild, message.author.id)
            await message.channel.send(
                f"Saved out-of-office schedule `{entry['id']}`.\n"
                "Send `list` any time to view schedules or `delete <id>` to remove one."
            )

    @tasks.loop(minutes=1)
    async def reconcile_roles(self):
        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
            return

        self._prune_expired_one_offs()

        user_ids = []
        for raw_user_id in self.state.get("users", {}):
            try:
                user_ids.append(int(raw_user_id))
            except ValueError:
                continue

        for user_id in user_ids:
            await self._sync_member_roles(guild, user_id)

    @reconcile_roles.before_loop
    async def before_reconcile_roles(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if isinstance(message.channel, discord.DMChannel):
            await self._handle_dm_message(message)
            return

        if not message.guild or message.guild.id != GUILD_ID:
            return

        if message.mention_everyone:
            return

        away_targets: list[str] = []

        seen_target_ids = set()
        for member in message.mentions:
            if member.bot or member.id == message.author.id or member.id in seen_target_ids:
                continue
            seen_target_ids.add(member.id)

            active_entries = self._active_entries_for_user(member.id)
            if not active_entries:
                continue

            if not self._cooldown_allows_reply(message.author.id, member.id, message.channel.id):
                continue

            entry = self._primary_active_entry(active_entries)
            if entry is None:
                continue

            away_targets.append(self._build_auto_reply(member, entry))

        if away_targets:
            await message.reply(
                "\n\n".join(away_targets),
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(OutOfOffice(bot))