from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import discord
from discord.ext import commands

from config import MAIN_GUILD_ID
from data_paths import data_path
from state_io import atomic_json_dump


logger = logging.getLogger(__name__)

ROLE_SELECTOR_CHANNEL_ID = 1099248200776421406
STATE_PATH = Path(data_path("reaction_roles.json"))

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


class ReactionRoles(commands.Cog):
    """Persistent, self-service HLL rank and play-style role selectors."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.state = _load_state()
        self._sync_lock = asyncio.Lock()
        self._view = ReactionRoleView(self)
        self.bot.add_view(self._view)

    @staticmethod
    def build_embed() -> discord.Embed:
        embed = discord.Embed(
            title="HLL Role Directory",
            color=discord.Color.dark_green(),
            description=(
                "Use the menus below to choose or remove your HLL rank and infantry play-style roles.\n\n"
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
        embed.set_footer(text="You may change either selection at any time.")
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

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self.sync_message()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReactionRoles(bot))
