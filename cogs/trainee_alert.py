from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands


LOGGER = logging.getLogger(__name__)
JOIN_ROLE_WAIT_SECONDS = 10 * 60


@dataclass(frozen=True)
class TraineeAlertConfig:
    key: str
    label: str
    training_name: str
    alert_channel_id: int
    trainee_role_id: int | None = None
    trainee_role_name: str | None = None
    trainer_role_id: int | None = None
    trainer_role_name: str | None = None


INFANTRY_ALERT = TraineeAlertConfig(
    key="infantry",
    label="Infantry Trainee",
    training_name="infantry",
    alert_channel_id=1237437502248452227,
    trainee_role_name="Infantry Trainee",
    trainer_role_name="Infantry School Trainer",
)

TANK_CREW_ALERT = TraineeAlertConfig(
    key="tank_crew",
    label="Tank Crew Trainee",
    training_name="tank crew",
    alert_channel_id=1334213005055102977,
    trainee_role_id=1099615408518070313,
    trainer_role_id=1337743860532645930,
)

ALERTS = (INFANTRY_ALERT, TANK_CREW_ALERT)
ALERTS_BY_KEY = {config.key: config for config in ALERTS}


def _has_role(member: object, *, role_id: int | None = None, role_name: str | None = None) -> bool:
    for role in getattr(member, "roles", ()):
        if role_id is not None and getattr(role, "id", None) == role_id:
            return True
        if role_name is not None and getattr(role, "name", None) == role_name:
            return True
    return False


def _has_trainee_role(member: object, config: TraineeAlertConfig) -> bool:
    return _has_role(member, role_id=config.trainee_role_id, role_name=config.trainee_role_name)


def _can_test_alert(member: discord.Member, config: TraineeAlertConfig) -> bool:
    return (
        member.guild_permissions.manage_guild
        or member.guild_permissions.administrator
        or _has_role(member, role_id=config.trainer_role_id, role_name=config.trainer_role_name)
    )


def _alert_content(
    trainee_id: int,
    trainer_role_id: int,
    config: TraineeAlertConfig,
    *,
    is_test: bool = False,
) -> str:
    prefix = "🧪 **TEST ALERT**\n" if is_test else ""
    return (
        f"{prefix}🎓 <@{trainee_id}> has joined with the **{config.label}** role.\n"
        f"<@&{trainer_role_id}>, please contact them to arrange their {config.training_name} training."
    )


class TraineeAlert(commands.Cog):
    """Notify the appropriate trainers when a new trainee arrives."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._notified_this_membership: set[tuple[int, str]] = set()
        self._pending_joiners: set[int] = set()
        self._join_expiry_tasks: dict[int, asyncio.Task[None]] = {}
        self._notification_lock = asyncio.Lock()

    def cog_unload(self) -> None:
        for task in self._join_expiry_tasks.values():
            task.cancel()
        self._join_expiry_tasks.clear()

    def _close_join_window(self, member_id: int) -> None:
        self._pending_joiners.discard(member_id)
        task = self._join_expiry_tasks.pop(member_id, None)
        if task is not None and not task.done():
            task.cancel()

    async def _expire_join_window(self, member_id: int) -> None:
        try:
            await asyncio.sleep(JOIN_ROLE_WAIT_SECONDS)
            self._pending_joiners.discard(member_id)
        except asyncio.CancelledError:
            pass
        finally:
            current = asyncio.current_task()
            if self._join_expiry_tasks.get(member_id) is current:
                self._join_expiry_tasks.pop(member_id, None)

    def _open_join_window(self, member_id: int) -> None:
        self._close_join_window(member_id)
        self._pending_joiners.add(member_id)
        self._join_expiry_tasks[member_id] = asyncio.create_task(self._expire_join_window(member_id))

    async def _alert_channel(self, config: TraineeAlertConfig) -> discord.TextChannel | None:
        channel = self.bot.get_channel(config.alert_channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(config.alert_channel_id)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                LOGGER.exception("Could not fetch %s trainee alert channel %s.", config.key, config.alert_channel_id)
                return None
        if not isinstance(channel, discord.TextChannel):
            LOGGER.error("Trainee alert destination %s is not a text channel.", config.alert_channel_id)
            return None
        return channel

    @staticmethod
    def _trainer_role(guild: discord.Guild, config: TraineeAlertConfig) -> discord.Role | None:
        if config.trainer_role_id is not None:
            return guild.get_role(config.trainer_role_id)
        if config.trainer_role_name is not None:
            return discord.utils.get(guild.roles, name=config.trainer_role_name)
        return None

    async def _send_alert(
        self,
        member: discord.Member,
        config: TraineeAlertConfig,
        *,
        is_test: bool = False,
    ) -> discord.Message | None:
        trainer_role = self._trainer_role(member.guild, config)
        if trainer_role is None:
            LOGGER.error("Cannot post %s trainee alert: trainer role was not found in guild %s.", config.key, member.guild.id)
            return None

        channel = await self._alert_channel(config)
        if channel is None:
            return None
        if channel.guild.id != member.guild.id:
            LOGGER.warning(
                "Ignoring %s trainee alert from guild %s because channel %s belongs to guild %s.",
                config.key,
                member.guild.id,
                channel.id,
                channel.guild.id,
            )
            return None

        try:
            return await channel.send(
                _alert_content(member.id, trainer_role.id, config, is_test=is_test),
                allowed_mentions=discord.AllowedMentions(users=True, roles=True, everyone=False),
            )
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.exception("Could not send %s trainee alert for member %s.", config.key, member.id)
            return None

    async def _notify_once(self, member: discord.Member, config: TraineeAlertConfig) -> None:
        if not _has_trainee_role(member, config):
            return

        notification_key = (member.id, config.key)
        async with self._notification_lock:
            if notification_key in self._notified_this_membership:
                return
            message = await self._send_alert(member, config)
            if message is not None:
                self._notified_this_membership.add(notification_key)

    @app_commands.command(
        name="test_trainee_alert",
        description="Post a test trainee alert in the configured trainer channel",
    )
    @app_commands.describe(track="The trainee alert to test")
    @app_commands.choices(
        track=[
            app_commands.Choice(name="Infantry", value="infantry"),
            app_commands.Choice(name="Tank Crew", value="tank_crew"),
        ]
    )
    @app_commands.guild_only()
    async def test_trainee_alert(self, interaction: discord.Interaction, track: str = "infantry") -> None:
        config = ALERTS_BY_KEY.get(track)
        if config is None:
            await interaction.response.send_message("That trainee alert is not configured.", ephemeral=True)
            return
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command can only be used in the server.", ephemeral=True)
            return
        if not _can_test_alert(interaction.user, config):
            trainer = f"<@&{config.trainer_role_id}>" if config.trainer_role_id else f"**{config.trainer_role_name}**"
            await interaction.response.send_message(
                f"You need Manage Server permission or the {trainer} role to run this test.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        message = await self._send_alert(interaction.user, config, is_test=True)
        if message is None:
            await interaction.followup.send(
                "The test alert could not be posted. Check the bot logs, channel access, and trainer role.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(f"Test alert posted: {message.jump_url}", ephemeral=True)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        self._notified_this_membership = {
            notification for notification in self._notified_this_membership if notification[0] != member.id
        }
        self._open_join_window(member.id)
        for config in ALERTS:
            await self._notify_once(member, config)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if after.id not in self._pending_joiners:
            return
        for config in ALERTS:
            if _has_trainee_role(after, config) and not _has_trainee_role(before, config):
                await self._notify_once(after, config)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        self._notified_this_membership = {
            notification for notification in self._notified_this_membership if notification[0] != member.id
        }
        self._close_join_window(member.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TraineeAlert(bot))
