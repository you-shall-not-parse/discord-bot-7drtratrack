from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import discord
from discord.ext import commands

from data_paths import data_path


LOGGER = logging.getLogger("RaidSeed")
STATE_PATH = Path(data_path("raidseed_posts.json"))
PANEL_STATE_PATH = Path(data_path("raidseed_panel.json"))
RAIDSEED_CHANNEL_ID = 1528077898177839244
MAX_VISIBLE_RAIDERS = 40


def _safe_text(value: str, *, markdown: bool = False) -> str:
    value = discord.utils.escape_mentions(value.strip())
    return discord.utils.escape_markdown(value) if markdown else value


def _valid_stats_url(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


class RaidSeedModal(discord.ui.Modal, title="Initiate Raid / Seed"):
    clan_name = discord.ui.TextInput(
        label="Clan name",
        placeholder="7DR",
        min_length=1,
        max_length=80,
    )
    announcement = discord.ui.TextInput(
        label="Raid / seed message",
        placeholder="7DR server is seeding! Hop in for VIP!",
        style=discord.TextStyle.paragraph,
        min_length=1,
        max_length=500,
    )
    stats_link = discord.ui.TextInput(
        label="CRCON or Bifrost server stats link",
        placeholder="https://...",
        min_length=8,
        max_length=400,
    )

    def __init__(self, cog: "RaidSeed") -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "Raid / seed posts can only be created in a server channel.",
                ephemeral=True,
            )
            return

        stats_url = self.stats_link.value.strip()
        if not _valid_stats_url(stats_url):
            await interaction.response.send_message(
                "Please enter a complete CRCON or Bifrost `http://` or `https://` link.",
                ephemeral=True,
            )
            return

        await self.cog.create_post(
            interaction,
            clan_name=self.clan_name.value,
            announcement=self.announcement.value,
            stats_url=stats_url,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        LOGGER.exception("Raid / seed modal failed", exc_info=error)
        message = "Something went wrong while creating the raid / seed post."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class InitiateRaidSeedButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Initiate Raid / Seed",
            style=discord.ButtonStyle.danger,
            emoji="⚔️",
            custom_id="raidseed:initiate",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("RaidSeed")
        if not isinstance(cog, RaidSeed):
            await interaction.response.send_message("The raid / seed tool is unavailable.", ephemeral=True)
            return
        await interaction.response.send_modal(RaidSeedModal(cog))


class RaidSeedLauncherView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(InitiateRaidSeedButton())


class RaidSeedSignupView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Join Raid / Seed",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="raidseed:join",
    )
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = interaction.client.get_cog("RaidSeed")
        if not isinstance(cog, RaidSeed):
            await interaction.response.send_message("The raid / seed tool is unavailable.", ephemeral=True)
            return
        await cog.update_signup(interaction, joining=True)

    @discord.ui.button(
        label="Leave",
        style=discord.ButtonStyle.secondary,
        emoji="↩️",
        custom_id="raidseed:leave",
    )
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = interaction.client.get_cog("RaidSeed")
        if not isinstance(cog, RaidSeed):
            await interaction.response.send_message("The raid / seed tool is unavailable.", ephemeral=True)
            return
        await cog.update_signup(interaction, joining=False)

    @discord.ui.button(
        label="Initiate Raid / Seed",
        style=discord.ButtonStyle.danger,
        emoji="⚔️",
        custom_id="raidseed:initiate_from_post",
    )
    async def initiate(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = interaction.client.get_cog("RaidSeed")
        if not isinstance(cog, RaidSeed):
            await interaction.response.send_message("The raid / seed tool is unavailable.", ephemeral=True)
            return
        await interaction.response.send_modal(RaidSeedModal(cog))


class RaidSeed(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._lock = asyncio.Lock()
        self._posts = self._load_posts()
        bot.add_view(RaidSeedLauncherView())
        bot.add_view(RaidSeedSignupView())
        self._panel_task = bot.loop.create_task(self._ensure_panel())

    def cog_unload(self) -> None:
        self._panel_task.cancel()

    def _load_posts(self) -> dict[str, dict[str, object]]:
        if not STATE_PATH.exists():
            return {}
        try:
            with STATE_PATH.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            LOGGER.exception("Could not load %s; starting with empty raid / seed state", STATE_PATH)
            return {}

    def _save_posts(self) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = STATE_PATH.with_suffix(".tmp")
        with temporary_path.open("w", encoding="utf-8") as handle:
            json.dump(self._posts, handle, indent=2, ensure_ascii=False)
        temporary_path.replace(STATE_PATH)

    def _load_panel_message_id(self) -> int | None:
        if not PANEL_STATE_PATH.exists():
            return None
        try:
            with PANEL_STATE_PATH.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            return int(data["message_id"])
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            LOGGER.warning("Could not load the saved raid / seed panel message ID")
            return None

    def _save_panel_message_id(self, message_id: int) -> None:
        PANEL_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = PANEL_STATE_PATH.with_suffix(".tmp")
        with temporary_path.open("w", encoding="utf-8") as handle:
            json.dump({"channel_id": RAIDSEED_CHANNEL_ID, "message_id": message_id}, handle, indent=2)
        temporary_path.replace(PANEL_STATE_PATH)

    async def _ensure_panel(self) -> None:
        await self.bot.wait_until_ready()
        channel = self.bot.get_channel(RAIDSEED_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(RAIDSEED_CHANNEL_ID)
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.exception("Could not access raid / seed channel %s", RAIDSEED_CHANNEL_ID)
                return

        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            LOGGER.error("Raid / seed channel %s is not a text channel or thread", RAIDSEED_CHANNEL_ID)
            return

        message_id = self._load_panel_message_id()
        if message_id is not None:
            try:
                message = await channel.fetch_message(message_id)
                await message.edit(embed=self.build_launcher_embed(), view=RaidSeedLauncherView())
                return
            except discord.NotFound:
                pass
            except (discord.Forbidden, discord.HTTPException):
                LOGGER.exception("Could not refresh raid / seed panel message %s", message_id)
                return

        try:
            message = await channel.send(embed=self.build_launcher_embed(), view=RaidSeedLauncherView())
            self._save_panel_message_id(message.id)
        except (OSError, discord.Forbidden, discord.HTTPException):
            LOGGER.exception("Could not create the raid / seed panel in channel %s", RAIDSEED_CHANNEL_ID)

    def build_launcher_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Server Raiding & Seeding",
            description=(
                "Start a new server raid or seeding call below. You will be asked for the clan, "
                "an announcement, and the CRCON or Bifrost server stats link."
            ),
            colour=discord.Colour.red(),
        )
        embed.set_footer(text="Use the button to create a new signup post.")
        return embed

    def build_post_embed(self, post: dict[str, object]) -> discord.Embed:
        clan_name = _safe_text(str(post.get("clan_name", "Unknown clan")), markdown=True)
        announcement = _safe_text(str(post.get("announcement", "")))
        stats_url = str(post.get("stats_url", ""))
        initiator_id = int(post.get("initiator_id", 0))
        participant_ids = [int(user_id) for user_id in post.get("participants", [])]

        embed = discord.Embed(
            title=f"⚔️ {clan_name} Raid / Seed Call",
            description=announcement,
            colour=discord.Colour.orange(),
            timestamp=datetime.fromisoformat(str(post["created_at"])),
        )
        embed.add_field(name="Initiated by", value=f"<@{initiator_id}>", inline=True)
        embed.add_field(name="Server stats", value=f"[Open CRCON / Bifrost]({stats_url})", inline=True)

        visible = participant_ids[:MAX_VISIBLE_RAIDERS]
        raider_lines = [f"<@{user_id}>" for user_id in visible]
        hidden_count = len(participant_ids) - len(visible)
        if hidden_count:
            raider_lines.append(f"…and {hidden_count} more")
        embed.add_field(
            name=f"Raiders / Seeders ({len(participant_ids)})",
            value="\n".join(raider_lines) or "No one has joined yet.",
            inline=False,
        )
        embed.set_footer(text="Click Join Raid / Seed to add your name.")
        return embed

    async def create_post(
        self,
        interaction: discord.Interaction,
        *,
        clan_name: str,
        announcement: str,
        stats_url: str,
    ) -> None:
        created_at = datetime.now(timezone.utc).isoformat()
        post: dict[str, object] = {
            "guild_id": interaction.guild_id,
            "channel_id": interaction.channel_id,
            "initiator_id": interaction.user.id,
            "clan_name": clan_name.strip(),
            "announcement": announcement.strip(),
            "stats_url": stats_url,
            "participants": [interaction.user.id],
            "created_at": created_at,
        }
        await interaction.response.send_message(
            embed=self.build_post_embed(post),
            view=RaidSeedSignupView(),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        message = await interaction.original_response()
        post["message_id"] = message.id
        async with self._lock:
            self._posts[str(message.id)] = post
            self._save_posts()

    async def update_signup(self, interaction: discord.Interaction, *, joining: bool) -> None:
        if interaction.message is None:
            await interaction.response.send_message("I could not identify this signup post.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        message_id = str(interaction.message.id)
        changed = False

        async with self._lock:
            post = self._posts.get(message_id)
            if post is None:
                await interaction.followup.send("This raid / seed post is no longer active.", ephemeral=True)
                return

            participants = [int(user_id) for user_id in post.get("participants", [])]
            if joining and interaction.user.id not in participants:
                participants.append(interaction.user.id)
                changed = True
            elif not joining and interaction.user.id in participants:
                participants.remove(interaction.user.id)
                changed = True

            post["participants"] = participants
            if changed:
                self._save_posts()
            embed = self.build_post_embed(post)

        if changed:
            try:
                await interaction.message.edit(embed=embed, view=RaidSeedSignupView())
            except discord.HTTPException:
                LOGGER.exception("Could not update raid / seed message %s", message_id)
                await interaction.followup.send("Your signup was saved, but I could not refresh the embed.", ephemeral=True)
                return

        if joining:
            response = "You have joined this raid / seed." if changed else "You are already on this raid / seed."
        else:
            response = "You have left this raid / seed." if changed else "You were not signed up for this raid / seed."
        await interaction.followup.send(response, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RaidSeed(bot))
