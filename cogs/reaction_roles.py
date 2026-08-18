from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import sqlite3
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks

from config import MAIN_GUILD_ID
from data_paths import data_path
from state_io import atomic_json_dump


logger = logging.getLogger(__name__)

ROLE_SELECTOR_CHANNEL_ID = 1099248200776421406
STATE_PATH = Path(data_path("reaction_roles.json"))
BIRTHDAY_CHANNEL_ID = 1098333222540152944
BIRTHDAY_DB_PATH = data_path("birthdays.db")
BIRTHDAY_TIMEZONE = ZoneInfo("Europe/London")
BIRTHDAY_GIF_URLS = [
    "https://media.tenor.com/X185VU8GGAUAAAAC/everybody-dance-now-speaker.gif",
    "https://media.tenor.com/zID0voNWZeMAAAAC/the-office-its-your-birthday-period-happy-birthday.gif",
    "https://media.tenor.com/mW9Bne87qc0AAAAC/the-office.gif",
    "https://media.tenor.com/BiqWZ9UdZ8kAAAAC/surprised-theoffice.gif",
    "https://media.tenor.com/GzGo7jQeLB0AAAAd/happy-birthday-bon-anniversaire.gif",
    "https://media.tenor.com/9pu-un8ImGUAAAAC/action-drama.gif",
    "https://media.tenor.com/fHAJclG404oAAAAC/birthday-parks-and-rec.gif",
    "https://media.tenor.com/z2xPe5mCygcAAAAC/birthday-self-worth.gif",
    "https://media.tenor.com/CSMv9A3-HkoAAAAC/shocked-happy.gif",
]

RANK_ROLES: dict[str, tuple[str, int, str]] = {
    "rank_20_79": ("HLL Rank 20-79", 1401656461913882734, "🪖"),
    "rank_80_149": ("HLL Rank 80-149", 1401656734891905104, "👍"),
    "rank_150_199": ("HLL Rank 150-199", 1401656961703088148, "👊"),
    "rank_200_249": ("HLL Rank 200-249", 1401657567490605107, "👌"),
    "rank_250_349": ("HLL Rank 250-349", 1401657994525016216, "💦"),
    "rank_350_500": ("HLL Rank 350-500", 1401658356489256960, "☠️"),
}

PLAY_STYLE_ROLES: dict[str, tuple[str, int, str]] = {
    "defender": ("Prefer to Defend (Defender)", 1446567884221579345, "🛡️"),
    "attacker": ("Prefer to Attack (Attacker)", 1446567995198410793, "⚔️"),
    "flexi": ("Prefer to be Fluid and Meatgrind (Flexi)", 1446583412084310027, "🏃‍♂️"),
}

COMMUNITY_TEAM_ROLES: dict[str, tuple[str, int, str]] = {
    "registered_building_inspector": (
        "Registered Building Inspector",
        1103588562714251264,
        "🏗️",
    ),
}


def _load_state() -> dict[str, int]:
    if not STATE_PATH.exists():
        return {}

    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not load reaction-role message state.", exc_info=True)
        return {}

    if not isinstance(raw, dict):
        return {}

    state: dict[str, int] = {}
    for key in ("channel_id", "message_id"):
        value = raw.get(key)
        if isinstance(value, int):
            state[key] = value
    return state


def _parse_birthday_input(
    value: str,
    *,
    today: date | None = None,
) -> tuple[str | None, bool, str | None]:
    """Parse DD MM [YYYY]; a missing year is not stored."""
    raw = " ".join(value.strip().split())
    match = re.fullmatch(r"(\d{1,2})[\s/.-]+(\d{1,2})(?:[\s/.-]+(\d{4}))?", raw)
    if match is None:
        return None, False, "Enter your birthday as `DD MM YYYY`, or `DD MM` to keep your birth year private."

    day_value, month_value, year_text = match.groups()
    current_date = today or datetime.now(BIRTHDAY_TIMEZONE).date()
    year_is_public = year_text is not None
    year_value = int(year_text) if year_text else 2000
    if year_is_public and not 1900 <= year_value <= current_date.year:
        return None, False, f"Enter a birth year between 1900 and {current_date.year}, or leave it blank."

    try:
        birthday = date(year_value, int(month_value), int(day_value))
    except ValueError:
        return None, False, "That is not a valid calendar date."

    stored_value = birthday.strftime("%d/%m/%Y" if year_is_public else "%d/%m")
    return stored_value, year_is_public, None


def _birthday_parts(date_str: str) -> tuple[int, int, int | None]:
    parts = str(date_str).split("/")
    if len(parts) not in (2, 3):
        raise ValueError("Invalid stored birthday")
    day_value, month_value = int(parts[0]), int(parts[1])
    year_value = int(parts[2]) if len(parts) == 3 else None
    date(year_value or 2000, month_value, day_value)
    return day_value, month_value, year_value


def _birthday_display(date_str: str, *, show_age: bool, today: date) -> str:
    day_value, month_value, year_value = _birthday_parts(date_str)
    birthday = date(year_value or 2000, month_value, day_value)
    text = birthday.strftime("%d %B")
    if show_age and year_value is not None:
        age = today.year - year_value - ((today.month, today.day) < (month_value, day_value))
        text += f" ({age} years old)"
    return text


class RoleCategorySelect(discord.ui.Select):
    def __init__(
        self,
        cog: "ReactionRoles",
        *,
        category_name: str,
        role_choices: dict[str, tuple[str, int, str]],
        custom_id: str,
        placeholder: str,
    ) -> None:
        self.cog = cog
        self.category_name = category_name
        self.role_choices = role_choices

        options = [
            discord.SelectOption(
                label=label,
                value=value,
                emoji=emoji,
            )
            for value, (label, _role_id, emoji) in role_choices.items()
        ]
        options.append(
            discord.SelectOption(
                label=f"Remove my {category_name.lower()} role",
                value="remove",
                emoji="❌",
                description=f"Remove any {category_name.lower()} selection",
            )
        )

        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=options,
            custom_id=custom_id,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog.apply_selection(
            interaction,
            category_name=self.category_name,
            role_choices=self.role_choices,
            selected_value=self.values[0],
        )


class BirthdayModal(discord.ui.Modal, title="Add or update your birthday"):
    birthday = discord.ui.TextInput(
        label="Birthday (DD MM YYYY)",
        placeholder="Example: 15 06 1995 — or 15 06 to hide the year",
        min_length=5,
        max_length=10,
        required=True,
    )

    def __init__(self, cog: "ReactionRoles") -> None:
        super().__init__(timeout=300, custom_id="reaction_roles:birthday_modal")
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.save_birthday_from_modal(interaction, str(self.birthday.value))


class BirthdayActionSelect(discord.ui.Select):
    def __init__(self, cog: "ReactionRoles") -> None:
        self.cog = cog
        super().__init__(
            placeholder="Add, update, remove, or view birthdays…",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label="Add or update my birthday",
                    value="set",
                    emoji="🎂",
                    description="Open the DD MM YYYY birthday form",
                ),
                discord.SelectOption(
                    label="Remove my birthday",
                    value="remove",
                    emoji="❌",
                    description="Delete your saved birthday",
                ),
                discord.SelectOption(
                    label="View birthdays this month",
                    value="view_month",
                    emoji="📅",
                    description="Show this month's saved birthdays",
                ),
            ],
            custom_id="reaction_roles:birthday_actions",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0]
        if selected == "set":
            await interaction.response.send_modal(BirthdayModal(self.cog))
        elif selected == "remove":
            await self.cog.handle_birthday_removal(interaction)
        else:
            await self.cog.show_month_birthdays(interaction)


class ReactionRoleView(discord.ui.View):
    def __init__(self, cog: "ReactionRoles") -> None:
        super().__init__(timeout=None)
        self.add_item(
            RoleCategorySelect(
                cog,
                category_name="HLL Rank",
                role_choices=RANK_ROLES,
                custom_id="reaction_roles:hll_rank",
                placeholder="Choose or remove your HLL rank…",
            )
        )
        self.add_item(
            RoleCategorySelect(
                cog,
                category_name="Play Style",
                role_choices=PLAY_STYLE_ROLES,
                custom_id="reaction_roles:play_style",
                placeholder="Choose or remove your play style…",
            )
        )
        self.add_item(
            RoleCategorySelect(
                cog,
                category_name="Community Team",
                role_choices=COMMUNITY_TEAM_ROLES,
                custom_id="reaction_roles:community_team",
                placeholder="Join or leave a community team…",
            )
        )
        self.add_item(BirthdayActionSelect(cog))


class ReactionRoles(commands.Cog):
    """Persistent self-service roles and birthday manager."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.state = _load_state()
        self._sync_lock = asyncio.Lock()
        self._view = ReactionRoleView(self)
        self.bot.add_view(self._view)
        self._birthday_db = sqlite3.connect(BIRTHDAY_DB_PATH)
        self._birthday_db.execute(
            "CREATE TABLE IF NOT EXISTS birthdays ("
            "guild_id INTEGER, user_id INTEGER, date TEXT, display_age INTEGER DEFAULT 0, "
            "PRIMARY KEY (guild_id, user_id))"
        )
        self._birthday_db.commit()
        columns = {
            str(row[1])
            for row in self._birthday_db.execute("PRAGMA table_info(birthdays)").fetchall()
        }
        if "display_age" not in columns:
            self._birthday_db.execute(
                "ALTER TABLE birthdays ADD COLUMN display_age INTEGER DEFAULT 0"
            )
            self._birthday_db.commit()

    def cog_unload(self) -> None:
        if self.check_birthdays.is_running():
            self.check_birthdays.cancel()
        if self.post_monthly_birthday_summary.is_running():
            self.post_monthly_birthday_summary.cancel()
        self._birthday_db.close()

    @staticmethod
    def build_embed() -> discord.Embed:
        embed = discord.Embed(
            title="Role & Birthday Directory",
            color=discord.Color.dark_green(),
            description=(
                "Use the menus below to manage your HLL rank, infantry play style, community team, "
                "and birthday.\n\n"
                "**Your existing roles are preserved.** Nothing changes until you make a selection. "
                "Choosing a new option replaces only your role in that category."
            ),
        )
        embed.add_field(
            name="HLL1 (WW2) Rank",
            value=(
                "Choose the bracket matching your current HLL in-game rank. This is a custom role tag only "
                "and has no bearing on your milsim rank within the clan Discord. A HLLV rank selection will be release once the game has been released.\n\n"
                + "\n".join(
                    f"{emoji} <@&{role_id}>"
                    for label, role_id, emoji in RANK_ROLES.values()
                )
            ),
            inline=False,
        )
        embed.add_field(
            name="Play Style",
            value=(
                "For infantry, choose whether you prefer to defend, attack, or stay fluid in the "
                "meatgrind. This preference does not prevent you from playing any other role.\n\n"
                + "\n".join(
                    f"{emoji} <@&{role_id}>"
                    for label, role_id, emoji in PLAY_STYLE_ROLES.values()
                )
            ),
            inline=False,
        )
        embed.add_field(
            name="Community Teams",
            value=(
                "Would you like to join the team that inspects building-code compliance in HLL? "
                "Choose the role below to sign up.\n\n"
                + "\n".join(
                    f"{emoji} <@&{role_id}>"
                    for label, role_id, emoji in COMMUNITY_TEAM_ROLES.values()
                )
            ),
            inline=False,
        )
        embed.add_field(
            name="Birthday Manager",
            value=(
                "Use the birthday menu to add, update, remove, or view birthdays. Enter `DD MM YYYY`, "
                "or leave the year blank as `DD MM` to keep your birth year and age private."
            ),
            inline=False,
        )
        embed.set_footer(text="You may change your role selections or birthday at any time.")
        return embed

    async def _target_channel(self) -> discord.TextChannel | None:
        channel = self.bot.get_channel(ROLE_SELECTOR_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(ROLE_SELECTOR_CHANNEL_ID)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                logger.exception(
                    "Could not fetch reaction-role channel %s.",
                    ROLE_SELECTOR_CHANNEL_ID,
                )
                return None

        if not isinstance(channel, discord.TextChannel):
            logger.error(
                "Reaction-role channel %s is not a text channel.",
                ROLE_SELECTOR_CHANNEL_ID,
            )
            return None
        return channel

    async def sync_message(self) -> bool:
        async with self._sync_lock:
            channel = await self._target_channel()
            if channel is None:
                return False

            embed = self.build_embed()
            message: discord.Message | None = None
            message_id = self.state.get("message_id")
            if message_id and self.state.get("channel_id") == channel.id:
                try:
                    message = await channel.fetch_message(message_id)
                except discord.NotFound:
                    message = None
                except (discord.Forbidden, discord.HTTPException):
                    logger.exception(
                        "Could not fetch reaction-role message %s.",
                        message_id,
                    )
                    return False

            try:
                if message is None:
                    message = await channel.send(
                        embed=embed,
                        view=self._view,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                else:
                    await message.edit(
                        embed=embed,
                        view=self._view,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
            except (discord.Forbidden, discord.HTTPException):
                logger.exception("Could not publish the reaction-role selector.")
                return False

            self.state = {"channel_id": channel.id, "message_id": message.id}
            atomic_json_dump(STATE_PATH, self.state, indent=2)
            return True

    def _set_birthday(
        self,
        guild_id: int,
        user_id: int,
        date_str: str,
        display_age: bool,
    ) -> None:
        self._birthday_db.execute(
            "INSERT OR REPLACE INTO birthdays (guild_id, user_id, date, display_age) "
            "VALUES (?, ?, ?, ?)",
            (guild_id, user_id, date_str, int(display_age)),
        )
        self._birthday_db.commit()

    def _remove_birthday(self, guild_id: int, user_id: int) -> bool:
        cursor = self._birthday_db.execute(
            "DELETE FROM birthdays WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        self._birthday_db.commit()
        return cursor.rowcount > 0

    def _month_birthdays(self, guild_id: int, month: int) -> list[tuple[int, str, bool]]:
        rows = self._birthday_db.execute(
            "SELECT user_id, date, display_age FROM birthdays WHERE guild_id = ?",
            (guild_id,),
        ).fetchall()
        birthdays: list[tuple[int, str, bool]] = []
        for user_id, date_str, display_age in rows:
            try:
                _day, stored_month, stored_year = _birthday_parts(str(date_str))
                if stored_month == month:
                    birthdays.append(
                        (int(user_id), str(date_str), bool(display_age) and stored_year is not None)
                    )
            except (TypeError, ValueError):
                logger.warning("Ignoring invalid stored birthday for user %s: %r", user_id, date_str)
        return birthdays

    async def save_birthday_from_modal(
        self,
        interaction: discord.Interaction,
        value: str,
    ) -> None:
        if interaction.guild is None or interaction.guild.id != MAIN_GUILD_ID:
            await interaction.response.send_message(
                "Birthday settings can only be changed in the 7DR server.",
                ephemeral=True,
            )
            return

        date_str, display_age, error = _parse_birthday_input(value)
        if date_str is None:
            await interaction.response.send_message(error, ephemeral=True)
            return

        self._set_birthday(interaction.guild.id, interaction.user.id, date_str, display_age)
        day_value, month_value, year_value = _birthday_parts(date_str)
        birthday = date(year_value or 2000, month_value, day_value)
        saved_date = birthday.strftime("%d %B")
        if year_value is not None:
            saved_date += f" {year_value}"
        privacy = (
            "Your birth year is saved and your age will be shown on birthday posts."
            if display_age
            else "Your birth year was not stored and your age will not be shown."
        )
        await interaction.response.send_message(
            f"✅ Birthday saved as **{saved_date}**. {privacy}",
            ephemeral=True,
        )

    async def handle_birthday_removal(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.guild.id != MAIN_GUILD_ID:
            await interaction.response.send_message(
                "Birthday settings can only be changed in the 7DR server.",
                ephemeral=True,
            )
            return
        removed = self._remove_birthday(interaction.guild.id, interaction.user.id)
        message = "Your saved birthday has been removed." if removed else "You do not have a saved birthday."
        await interaction.response.send_message(message, ephemeral=True)

    async def show_month_birthdays(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.guild.id != MAIN_GUILD_ID:
            await interaction.response.send_message(
                "Birthdays can only be viewed in the 7DR server.",
                ephemeral=True,
            )
            return

        today = datetime.now(BIRTHDAY_TIMEZONE).date()
        birthdays = self._month_birthdays(interaction.guild.id, today.month)
        lines = self._birthday_lines(interaction.guild, birthdays, today=today)
        if not lines:
            await interaction.response.send_message("📭 No birthdays this month.", ephemeral=True)
            return
        embed = discord.Embed(
            title=f"🎉 Birthdays in {today.strftime('%B')} 🎉",
            description="\n".join(lines),
            color=discord.Color.purple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @staticmethod
    def _birthday_lines(
        guild: discord.Guild,
        birthdays: list[tuple[int, str, bool]],
        *,
        today: date,
    ) -> list[str]:
        lines: list[str] = []
        for user_id, date_str, display_age in sorted(
            birthdays,
            key=lambda item: (_birthday_parts(item[1])[1], _birthday_parts(item[1])[0]),
        ):
            member = guild.get_member(user_id)
            if member is None:
                continue
            display = _birthday_display(date_str, show_age=display_age, today=today)
            lines.append(f"🎂 {member.mention} — {display}")
        return lines

    async def apply_selection(
        self,
        interaction: discord.Interaction,
        *,
        category_name: str,
        role_choices: dict[str, tuple[str, int, str]],
        selected_value: str,
    ) -> None:
        if (
            interaction.guild is None
            or interaction.guild.id != MAIN_GUILD_ID
            or not isinstance(interaction.user, discord.Member)
        ):
            await interaction.response.send_message(
                "This selector can only be used in the 7DR server.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        member = interaction.user
        bot_member = guild.me
        if bot_member is None and self.bot.user is not None:
            bot_member = guild.get_member(self.bot.user.id)

        if bot_member is None or not bot_member.guild_permissions.manage_roles:
            await interaction.followup.send(
                "I cannot update roles because I do not have **Manage Roles**. Please contact staff.",
                ephemeral=True,
            )
            return

        configured_roles: dict[int, discord.Role] = {}
        for _label, role_id, _emoji in role_choices.values():
            role = guild.get_role(role_id)
            if role is not None:
                configured_roles[role_id] = role

        selected_role: discord.Role | None = None
        selected_label: str | None = None
        if selected_value != "remove":
            choice = role_choices.get(selected_value)
            if choice is None:
                await interaction.followup.send(
                    "That selection is no longer configured. Please try again.",
                    ephemeral=True,
                )
                return
            selected_label, selected_role_id, _emoji = choice
            selected_role = configured_roles.get(selected_role_id)
            if selected_role is None:
                await interaction.followup.send(
                    "That role no longer exists. Please contact staff.",
                    ephemeral=True,
                )
                return

        current_category_roles = [
            role for role in member.roles if role.id in configured_roles
        ]
        roles_to_remove = [
            role for role in current_category_roles if role != selected_role
        ]
        roles_to_change = roles_to_remove + (
            [selected_role]
            if selected_role is not None and selected_role not in member.roles
            else []
        )

        unmanageable = [
            role
            for role in roles_to_change
            if role.managed or role >= bot_member.top_role
        ]
        if unmanageable:
            await interaction.followup.send(
                "I cannot manage one or more of those roles because they are above my bot role. "
                "Please contact staff.",
                ephemeral=True,
            )
            return

        if not roles_to_change:
            if selected_role is None:
                message = f"You do not currently have a {category_name.lower()} role to remove."
            else:
                message = f"You already have **{selected_label}**."
            await interaction.followup.send(message, ephemeral=True)
            return

        reason = f"Self-service {category_name} selection by {member} ({member.id})"
        try:
            # Add first so a failed add never strips the member's existing selection.
            if selected_role is not None and selected_role not in member.roles:
                await member.add_roles(selected_role, reason=reason)
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason=reason)
        except (discord.Forbidden, discord.HTTPException):
            logger.exception(
                "Failed to update %s roles for member %s.",
                category_name,
                member.id,
            )
            await interaction.followup.send(
                "Discord could not complete that role update. Please try again or contact staff.",
                ephemeral=True,
            )
            return

        if selected_role is None:
            message = f"Removed your {category_name.lower()} role."
        else:
            message = f"Your {category_name.lower()} is now **{selected_label}**."
        await interaction.followup.send(message, ephemeral=True)

    @tasks.loop(time=time(hour=9, minute=0, tzinfo=BIRTHDAY_TIMEZONE))
    async def check_birthdays(self) -> None:
        today = datetime.now(BIRTHDAY_TIMEZONE).date()
        for guild in self.bot.guilds:
            birthdays = self._month_birthdays(guild.id, today.month)
            birthdays_today = [
                item
                for item in birthdays
                if _birthday_parts(item[1])[0] == today.day
            ]
            if not birthdays_today:
                continue
            channel = guild.get_channel(BIRTHDAY_CHANNEL_ID)
            if not isinstance(channel, discord.TextChannel):
                continue
            for user_id, date_str, display_age in birthdays_today:
                member = guild.get_member(user_id)
                if member is None:
                    continue
                display = _birthday_display(date_str, show_age=display_age, today=today)
                age_suffix = ""
                if display_age and "(" in display:
                    age_suffix = f" {display[display.index('('):]}"
                message = f"🎉 Happy Birthday to {member.mention}!{age_suffix}"
                gif_url = random.choice(BIRTHDAY_GIF_URLS) if BIRTHDAY_GIF_URLS else None
                await channel.send(f"{message}\n{gif_url}" if gif_url else message)

    @tasks.loop(time=time(hour=9, minute=5, tzinfo=BIRTHDAY_TIMEZONE))
    async def post_monthly_birthday_summary(self) -> None:
        today = datetime.now(BIRTHDAY_TIMEZONE).date()
        if today.day != 1:
            return
        for guild in self.bot.guilds:
            birthdays = self._month_birthdays(guild.id, today.month)
            lines = self._birthday_lines(guild, birthdays, today=today)
            if not lines:
                continue
            channel = guild.get_channel(BIRTHDAY_CHANNEL_ID)
            if not isinstance(channel, discord.TextChannel):
                continue
            embed = discord.Embed(
                title=f"📅 Birthdays in {today.strftime('%B')}",
                description="\n".join(lines),
                color=discord.Color.gold(),
            )
            await channel.send(embed=embed)

    async def _remove_legacy_birthday_manager(self) -> None:
        for guild in self.bot.guilds:
            channel = guild.get_channel(BIRTHDAY_CHANNEL_ID)
            if not isinstance(channel, discord.TextChannel):
                continue
            try:
                async for message in channel.history(limit=100):
                    if message.author != self.bot.user or not message.embeds:
                        continue
                    if message.embeds[0].title == "🎂 Birthday Manager 🎂":
                        await message.delete()
            except (discord.Forbidden, discord.HTTPException):
                logger.exception("Could not remove the legacy birthday-manager message.")

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self.sync_message()
        if not self.check_birthdays.is_running():
            self.check_birthdays.start()
        if not self.post_monthly_birthday_summary.is_running():
            self.post_monthly_birthday_summary.start()
        await self._remove_legacy_birthday_manager()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReactionRoles(bot))
