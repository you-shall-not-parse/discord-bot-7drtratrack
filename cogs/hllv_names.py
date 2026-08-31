from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from pathlib import Path

import discord
from discord.ext import commands

from config import MAIN_GUILD_ID
from data_paths import data_path
from state_io import atomic_json_dump


logger = logging.getLogger(__name__)
HLLV_NAMES_CHANNEL_ID = 1544114339609575475
DB_PATH = data_path("hllv_names.db")
STATE_PATH = Path(data_path("hllv_names_message.json"))
HLLV_IMAGE_PATH = data_path("map_images", "HLLV Image2.webp")
HLLV_IMAGE_FILENAME = "hllv-name-directory.webp"
MAX_HLLV_NAME_LENGTH = 64


def _load_state() -> dict[str, object]:
    if not STATE_PATH.exists():
        return {}
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not load the HLLV-name message state.", exc_info=True)
        return {}
    return raw if isinstance(raw, dict) else {}


def _normalise_name(value: str) -> tuple[str | None, str | None]:
    name = " ".join(value.split())
    if not name:
        return None, "Enter your HLLV name."
    if len(name) > MAX_HLLV_NAME_LENGTH:
        return None, f"HLLV names can be no longer than {MAX_HLLV_NAME_LENGTH} characters."
    if any(ord(character) < 32 for character in name):
        return None, "HLLV names cannot contain control characters."
    return name, None


def _safe_embed_text(value: str) -> str:
    return discord.utils.escape_mentions(discord.utils.escape_markdown(value))


class HLLVNameModal(discord.ui.Modal):
    def __init__(self, cog: "HLLVNames", current_name: str | None) -> None:
        super().__init__(
            title="Add or update your HLLV name",
            timeout=300,
            custom_id="hllv_names:name_modal",
        )
        self.cog = cog
        self.name_input = discord.ui.TextInput(
            label="HLLV name",
            placeholder="Type your in-game HLLV name",
            default=current_name,
            min_length=1,
            max_length=MAX_HLLV_NAME_LENGTH,
            required=True,
        )
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.save_name_from_modal(interaction, str(self.name_input.value))


class HLLVNameActionSelect(discord.ui.Select):
    def __init__(self, cog: "HLLVNames") -> None:
        self.cog = cog
        super().__init__(
            placeholder="Add, update, or delete your HLLV name…",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label="Add or update my HLLV name",
                    value="set",
                    emoji="✏️",
                    description="Type the name you use in HLLV",
                ),
                discord.SelectOption(
                    label="Delete my HLLV name",
                    value="delete",
                    emoji="🗑️",
                    description="Remove your name from the directory",
                ),
            ],
            custom_id="hllv_names:actions",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not self.cog.is_valid_interaction(interaction):
            await interaction.response.send_message(
                "This menu can only be used in the HLLV names channel.", ephemeral=True
            )
            return
        if self.values[0] == "set":
            current_name = self.cog.get_name(interaction.guild.id, interaction.user.id)
            await interaction.response.send_modal(HLLVNameModal(self.cog, current_name))
        else:
            await self.cog.delete_name(interaction)


class HLLVSearchModal(discord.ui.Modal):
    def __init__(self, cog: "HLLVNames") -> None:
        super().__init__(
            title="Search the HLLV name directory",
            timeout=300,
            custom_id="hllv_names:search_modal",
        )
        self.cog = cog
        self.query_input = discord.ui.TextInput(
            label="User or HLLV name",
            placeholder="Nickname, username, user ID, or HLLV name",
            min_length=1,
            max_length=64,
            required=True,
        )
        self.add_item(self.query_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.show_search_results(interaction, str(self.query_input.value))


class HLLVSearchButton(discord.ui.Button):
    def __init__(self, cog: "HLLVNames") -> None:
        super().__init__(
            label="Search users",
            emoji="🔎",
            style=discord.ButtonStyle.secondary,
            custom_id="hllv_names:search",
            row=1,
        )
        self.cog = cog

    async def callback(self, interaction: discord.Interaction) -> None:
        if not self.cog.is_valid_interaction(interaction):
            await interaction.response.send_message(
                "Search can only be used in the HLLV names channel.", ephemeral=True
            )
            return
        await interaction.response.send_modal(HLLVSearchModal(self.cog))


class HLLVNameView(discord.ui.View):
    def __init__(self, cog: "HLLVNames") -> None:
        super().__init__(timeout=None)
        self.add_item(HLLVNameActionSelect(cog))
        self.add_item(HLLVSearchButton(cog))


class HLLVNames(commands.Cog):
    """Self-service directory mapping Discord users to their HLLV names."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.state = _load_state()
        self._sync_lock = asyncio.Lock()
        self._view = HLLVNameView(self)
        self.bot.add_view(self._view)
        self._db = sqlite3.connect(DB_PATH)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS hllv_names ("
            "guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, name TEXT NOT NULL, "
            "PRIMARY KEY (guild_id, user_id))"
        )
        self._db.commit()

    def cog_unload(self) -> None:
        self._db.close()

    @staticmethod
    def is_valid_interaction(interaction: discord.Interaction) -> bool:
        return (
            interaction.guild is not None
            and interaction.guild.id == MAIN_GUILD_ID
            and interaction.channel_id == HLLV_NAMES_CHANNEL_ID
        )

    def get_name(self, guild_id: int, user_id: int) -> str | None:
        row = self._db.execute(
            "SELECT name FROM hllv_names WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        return str(row[0]) if row else None

    def _set_name(self, guild_id: int, user_id: int, name: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO hllv_names (guild_id, user_id, name) VALUES (?, ?, ?)",
            (guild_id, user_id, name),
        )
        self._db.commit()

    def _delete_name(self, guild_id: int, user_id: int) -> bool:
        cursor = self._db.execute(
            "DELETE FROM hllv_names WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )
        self._db.commit()
        return cursor.rowcount > 0

    def _records(self, guild_id: int) -> list[tuple[int, str]]:
        return [
            (int(user_id), str(name))
            for user_id, name in self._db.execute(
                "SELECT user_id, name FROM hllv_names WHERE guild_id = ?", (guild_id,)
            ).fetchall()
        ]

    @staticmethod
    def _directory_lines(guild: discord.Guild, records: list[tuple[int, str]]) -> list[str]:
        def sort_key(record: tuple[int, str]) -> tuple[str, str, int]:
            user_id, hllv_name = record
            member = guild.get_member(user_id)
            discord_name = member.display_name if member else str(user_id)
            return discord_name.casefold(), hllv_name.casefold(), user_id

        lines: list[str] = []
        for user_id, hllv_name in sorted(records, key=sort_key):
            member = guild.get_member(user_id)
            nickname = member.display_name if member else "Unknown member"
            lines.append(
                f"<@{user_id}> - {_safe_embed_text(nickname)} - {_safe_embed_text(hllv_name)}"
            )
        return lines

    @staticmethod
    def _search_records(
        guild: discord.Guild,
        records: list[tuple[int, str]],
        query: str,
    ) -> list[tuple[int, str]]:
        needle = query.strip().casefold()
        if not needle:
            return []
        matches: list[tuple[int, str]] = []
        for user_id, hllv_name in records:
            member = guild.get_member(user_id)
            searchable = [str(user_id), hllv_name]
            if member is not None:
                searchable.extend(
                    filter(
                        None,
                        (
                            member.display_name,
                            getattr(member, "name", None),
                            getattr(member, "global_name", None),
                        ),
                    )
                )
            if any(needle in str(value).casefold() for value in searchable):
                matches.append((user_id, hllv_name))
        return sorted(
            matches,
            key=lambda record: (
                (guild.get_member(record[0]).display_name if guild.get_member(record[0]) else str(record[0])).casefold(),
                record[1].casefold(),
            ),
        )

    @classmethod
    def build_embeds(
        cls, guild: discord.Guild, records: list[tuple[int, str]]
    ) -> list[discord.Embed]:
        lines = cls._directory_lines(guild, records)
        chunks: list[list[str]] = []
        current: list[str] = []
        current_length = 0
        for line in lines:
            added_length = len(line) + (1 if current else 0)
            if current and current_length + added_length > 3400:
                chunks.append(current)
                current = []
                current_length = 0
                added_length = len(line)
            current.append(line)
            current_length += added_length
        chunks.append(current)

        embeds: list[discord.Embed] = []
        for page_number, chunk in enumerate(chunks, start=1):
            title = "HLLV Name Directory"
            if len(chunks) > 1:
                title += f" ({page_number}/{len(chunks)})"
            listing = "\n".join(chunk) if chunk else "*No HLLV names have been added yet.*"
            if page_number == 1:
                description = (
                    "Use the dropdown below to add, update, or delete your HLLV name. "
                    "Use **Search users** to find someone by Discord or HLLV name. "
                    f"Changes appear here automatically.\n\n{listing}"
                )
            else:
                description = listing
            embed = discord.Embed(
                title=title, description=description, color=discord.Color.dark_green()
            )
            if page_number == 1:
                embed.set_image(url=f"attachment://{HLLV_IMAGE_FILENAME}")
            embed.set_footer(text=f"{len(records)} registered HLLV name(s)")
            embeds.append(embed)
        return embeds

    async def _target_channel(self) -> discord.TextChannel | None:
        channel = self.bot.get_channel(HLLV_NAMES_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(HLLV_NAMES_CHANNEL_ID)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                logger.exception("Could not fetch HLLV names channel %s.", HLLV_NAMES_CHANNEL_ID)
                return None
        if not isinstance(channel, discord.TextChannel):
            logger.error("HLLV names channel %s is not a text channel.", HLLV_NAMES_CHANNEL_ID)
            return None
        return channel

    async def sync_messages(self) -> bool:
        async with self._sync_lock:
            channel = await self._target_channel()
            if channel is None:
                return False
            embeds = self.build_embeds(channel.guild, self._records(channel.guild.id))
            raw_ids = self.state.get("message_ids", [])
            if not isinstance(raw_ids, list):
                raw_ids = []
            existing: list[discord.Message] = []
            if self.state.get("channel_id") == channel.id:
                for raw_id in raw_ids:
                    try:
                        existing.append(await channel.fetch_message(int(raw_id)))
                    except (TypeError, ValueError, discord.NotFound):
                        continue
                    except (discord.Forbidden, discord.HTTPException):
                        logger.exception("Could not fetch an HLLV directory message.")
                        return False

            published: list[discord.Message] = []
            try:
                for index, embed in enumerate(embeds):
                    view = self._view if index == 0 else None
                    if index < len(existing):
                        message = existing[index]
                        attachments = (
                            [discord.File(HLLV_IMAGE_PATH, filename=HLLV_IMAGE_FILENAME)]
                            if index == 0
                            else []
                        )
                        await message.edit(
                            embed=embed,
                            view=view,
                            attachments=attachments,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                    else:
                        if index == 0:
                            message = await channel.send(
                                embed=embed,
                                view=view,
                                file=discord.File(
                                    HLLV_IMAGE_PATH, filename=HLLV_IMAGE_FILENAME
                                ),
                                allowed_mentions=discord.AllowedMentions.none(),
                            )
                        else:
                            message = await channel.send(
                                embed=embed,
                                view=view,
                                allowed_mentions=discord.AllowedMentions.none(),
                            )
                    published.append(message)
                for obsolete in existing[len(embeds):]:
                    await obsolete.delete()
            except (discord.Forbidden, discord.HTTPException):
                logger.exception("Could not publish the HLLV name directory.")
                return False

            self.state = {
                "channel_id": channel.id,
                "message_ids": [message.id for message in published],
            }
            atomic_json_dump(STATE_PATH, self.state, indent=2)
            return True

    async def save_name_from_modal(
        self, interaction: discord.Interaction, value: str
    ) -> None:
        if not self.is_valid_interaction(interaction):
            await interaction.response.send_message(
                "HLLV names can only be changed in the HLLV names channel.", ephemeral=True
            )
            return
        name, error = _normalise_name(value)
        if name is None:
            await interaction.response.send_message(error, ephemeral=True)
            return

        self._set_name(interaction.guild.id, interaction.user.id, name)
        await interaction.response.defer(ephemeral=True, thinking=True)
        updated = await self.sync_messages()
        suffix = "" if updated else " The directory could not refresh; please contact staff."
        await interaction.followup.send(
            f"Your HLLV name is now **{_safe_embed_text(name)}**.{suffix}", ephemeral=True
        )

    async def show_search_results(
        self, interaction: discord.Interaction, query: str
    ) -> None:
        if not self.is_valid_interaction(interaction):
            await interaction.response.send_message(
                "Search can only be used in the HLLV names channel.", ephemeral=True
            )
            return

        clean_query = " ".join(query.split())
        if not clean_query:
            await interaction.response.send_message(
                "Enter a nickname, username, user ID, or HLLV name.", ephemeral=True
            )
            return

        records = self._records(interaction.guild.id)
        matches = self._search_records(interaction.guild, records, clean_query)
        if not matches:
            await interaction.response.send_message(
                f"No registered users matched **{_safe_embed_text(clean_query)}**.",
                ephemeral=True,
            )
            return

        shown_matches = matches[:25]
        description = "\n".join(self._directory_lines(interaction.guild, shown_matches))
        if len(matches) > len(shown_matches):
            description += f"\n\n*Showing the first {len(shown_matches)} of {len(matches)} matches.*"
        embed = discord.Embed(
            title=f"HLLV search: {_safe_embed_text(clean_query)}",
            description=description,
            color=discord.Color.dark_green(),
        )
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def delete_name(self, interaction: discord.Interaction) -> None:
        removed = self._delete_name(interaction.guild.id, interaction.user.id)
        await interaction.response.defer(ephemeral=True, thinking=True)
        if removed:
            updated = await self.sync_messages()
            suffix = "" if updated else " The directory could not refresh; please contact staff."
            message = f"Your HLLV name has been deleted.{suffix}"
        else:
            message = "You do not have a saved HLLV name."
        await interaction.followup.send(message, ephemeral=True)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self.sync_messages()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HLLVNames(bot))
