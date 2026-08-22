from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands


LOGGER = logging.getLogger(__name__)

ALERT_CHANNEL_ID = 1237437502248452227
TRAINEE_ROLE_NAME = "Infantry Trainee"
TRAINER_ROLE_NAME = "Infantry School Trainer"
JOIN_ROLE_WAIT_SECONDS = 10 * 60


def _has_role(member: object, role_name: str) -> bool:
    return any(getattr(role, "name", None) == role_name for role in getattr(member, "roles", ()))


def _can_test_alert(member: discord.Member) -> bool:
    return (
        member.guild_permissions.manage_guild
        or member.guild_permissions.administrator
        or _has_role(member, TRAINER_ROLE_NAME)
    )


def _alert_content(trainee_id: int, trainer_role_id: int, *, is_test: bool = False) -> str:
    prefix = "🧪 **TEST ALERT**\n" if is_test else ""
    return (
        f"{prefix}🎓 <@{trainee_id}> has joined with the **{TRAINEE_ROLE_NAME}** role.\n"
        f"<@&{trainer_role_id}>, please contact them to arrange their infantry training."
    )


class InfantryTraineeAlert(commands.Cog):
    """Notify infantry trainers when a new Infantry Trainee arrives."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._notified_this_membership: set[int] = set()
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

    async def _alert_channel(self) -> discord.TextChannel | None:
        channel = self.bot.get_channel(ALERT_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(ALERT_CHANNEL_ID)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                LOGGER.exception("Could not fetch infantry trainee alert channel %s.", ALERT_CHANNEL_ID)
                return None
        if not isinstance(channel, discord.TextChannel):
            LOGGER.error("Infantry trainee alert destination %s is not a text channel.", ALERT_CHANNEL_ID)
            return None
        return channel

    async def _send_alert(self, member: discord.Member, *, is_test: bool = False) -> discord.Message | None:
        trainer_role = discord.utils.get(member.guild.roles, name=TRAINER_ROLE_NAME)
        if trainer_role is None:
            LOGGER.error(
                "Cannot post trainee alert for %s: role %r was not found in guild %s.",
                member.id,
                TRAINER_ROLE_NAME,
                member.guild.id,
            )
            return None

        channel = await self._alert_channel()
        if channel is None:
            return None
        if channel.guild.id != member.guild.id:
            LOGGER.warning(
                "Ignoring trainee alert from guild %s because channel %s belongs to guild %s.",
                member.guild.id,
                channel.id,
                channel.guild.id,
            )
            return None

        try:
            return await channel.send(
                _alert_content(member.id, trainer_role.id, is_test=is_test),
                allowed_mentions=discord.AllowedMentions(users=True, roles=True, everyone=False),
            )
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.exception("Could not send infantry trainee alert for member %s.", member.id)
            return None

    async def _notify_once(self, member: discord.Member) -> None:
        if not _has_role(member, TRAINEE_ROLE_NAME):
            return

        async with self._notification_lock:
            if member.id in self._notified_this_membership:
                return
            message = await self._send_alert(member)
            if message is None:
                return
            self._notified_this_membership.add(member.id)
            self._close_join_window(member.id)

    @app_commands.command(
        name="test_infantry_trainee_alert",
        description="Post a test Infantry Trainee alert in the configured trainer channel",
    )
    @app_commands.guild_only()
    async def test_infantry_trainee_alert(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command can only be used in the server.", ephemeral=True)
            return
        if not _can_test_alert(interaction.user):
            await interaction.response.send_message(
                f"You need Manage Server permission or the **{TRAINER_ROLE_NAME}** role to run this test.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        message = await self._send_alert(interaction.user, is_test=True)
        if message is None:
            await interaction.followup.send(
                "The test alert could not be posted. Check the bot logs, channel access, and trainer role name.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(f"Test alert posted: {message.jump_url}", ephemeral=True)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        self._notified_this_membership.discard(member.id)
        self._open_join_window(member.id)
        await self._notify_once(member)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        had_role = _has_role(before, TRAINEE_ROLE_NAME)
        has_role = _has_role(after, TRAINEE_ROLE_NAME)
        if after.id in self._pending_joiners and has_role and not had_role:
            await self._notify_once(after)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        self._notified_this_membership.discard(member.id)
        self._close_join_window(member.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InfantryTraineeAlert(bot))
