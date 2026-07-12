import json
import os

import discord
from discord.ext import commands

from config import MAIN_GUILD_ID
from data_paths import data_path


GUILD_ID = MAIN_GUILD_ID
DATA_FILE = data_path("supporters_embed.json")
EMBED_TITLE = "Our Supporters"
SUPPORTERS_CHANNEL_ID = 1525460056340955237
RAT_PATRON_ROLE_ID = 1525871943973081319
LT_COL_CRUMP_USER_ID = 1109147750932676649
WAR_DIARY_FORUM_CHANNEL_ID = 1489703502426018002
SUPPORTERS_IMAGE_PATH = data_path("ChatGPT Image Jul 12, 2026, 04_37_09 PM.png")
SUPPORTERS_IMAGE_FILENAME = "rat_patron.png"


def load_data() -> dict:
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r", encoding="utf-8") as file_handle:
        return json.load(file_handle)


def save_data(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as file_handle:
        json.dump(data, file_handle, indent=4)


class SupportersEmbed(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = load_data()

    def _guild(self) -> discord.Guild | None:
        return self.bot.get_guild(GUILD_ID)

    @staticmethod
    def _display_name(member: discord.Member) -> str:
        return member.nick or member.display_name or member.name

    def _role_lines(self, guild: discord.Guild, role_id: int) -> str:
        role = guild.get_role(role_id)
        if role is None:
            return "Role not found."

        members = sorted(role.members, key=lambda member: self._display_name(member).lower())
        if not members:
            return "None"

        return "\n".join(self._display_name(member) for member in members)

    def build_embed(self) -> discord.Embed | None:
        guild = self._guild()
        if guild is None:
            return None

        embed = discord.Embed(
            title="Support Our Clan",
            color=discord.Color.red(),
            description=(
                "If you would like to donate on a voluntary basis then you can click the Ko-Fi link below and choose "
                "donate on a **one-off basis** or **subscribe** on a minimum £1 per month basis.\n\n"
                "We ask that you do not commit to this if you cannot afford it and please do not donate too much, "
                "whether it be £5 one off, £1 per month, £3 per month or £5 per month you will recieve the "
                f"<@&{RAT_PATRON_ROLE_ID}> role.\n\n"
                "All of your contributions will go towards the running costs of our clan such as server costs, "
                "bot costs, Bifrost costs.\n\n"
                f"By becoming a <@&{RAT_PATRON_ROLE_ID}> you will gain exclusive access to our War Diary channel "
                f"<#{WAR_DIARY_FORUM_CHANNEL_ID}> which shows all of our clan match results and other exclusive "
                "perks as we release them!\n\n"
                "You must connect your discord account to your ko-fi account in order for it to give you the role! "
                f"If you experience any issues ask <@{LT_COL_CRUMP_USER_ID}>\n\n"
                "Link: https://ko-fi.com/7tharmoureddivisonclan"
            ),
        )
        if os.path.exists(SUPPORTERS_IMAGE_PATH):
            embed.set_image(url=f"attachment://{SUPPORTERS_IMAGE_FILENAME}")
        embed.add_field(
            name="Current Rat Patrons",
            value=self._role_lines(guild, RAT_PATRON_ROLE_ID),
            inline=False,
        )
        return embed

    def _image_file(self) -> discord.File | None:
        if not os.path.exists(SUPPORTERS_IMAGE_PATH):
            return None
        return discord.File(SUPPORTERS_IMAGE_PATH, filename=SUPPORTERS_IMAGE_FILENAME)

    async def _get_target_channel(self) -> discord.TextChannel | None:
        channel = self.bot.get_channel(SUPPORTERS_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(SUPPORTERS_CHANNEL_ID)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return None

        if not isinstance(channel, discord.TextChannel):
            return None

        return channel

    async def _delete_previous_message_if_needed(self) -> None:
        old_channel_id = self.data.get("channel_id")
        old_message_id = self.data.get("message_id")
        if not old_channel_id or not old_message_id:
            return

        if old_channel_id == SUPPORTERS_CHANNEL_ID:
            return

        old_channel = self.bot.get_channel(old_channel_id)
        if old_channel is None:
            try:
                old_channel = await self.bot.fetch_channel(old_channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return

        if not isinstance(old_channel, discord.TextChannel):
            return

        try:
            old_message = await old_channel.fetch_message(old_message_id)
            await old_message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

    async def _create_embed_message(self, channel: discord.TextChannel, embed: discord.Embed) -> discord.Message:
        await self._delete_previous_message_if_needed()
        image_file = self._image_file()
        if image_file is None:
            message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        else:
            message = await channel.send(
                embed=embed,
                file=image_file,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        self.data = {
            "channel_id": channel.id,
            "message_id": message.id,
        }
        save_data(self.data)
        return message

    async def sync_embed(self) -> bool:
        channel = await self._get_target_channel()
        if channel is None:
            return False

        embed = self.build_embed()
        if embed is None:
            return False

        message_id = self.data.get("message_id")
        if not message_id:
            await self._create_embed_message(channel, embed)
            return True

        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            await self._create_embed_message(channel, embed)
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

        if self.data.get("channel_id") != channel.id:
            self.data["channel_id"] = channel.id
            save_data(self.data)

        if message.embeds and message.embeds[0].to_dict() == embed.to_dict():
            return True

        image_file = self._image_file()
        if image_file is None:
            await message.edit(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        else:
            await message.edit(
                embed=embed,
                attachments=[image_file],
                allowed_mentions=discord.AllowedMentions.none(),
            )
        return True

    def _member_can_affect_embed(self, member: discord.Member) -> bool:
        tracked_role_ids = {RAT_PATRON_ROLE_ID}
        return any(role.id in tracked_role_ids for role in member.roles)

    @commands.Cog.listener()
    async def on_ready(self):
        await self.sync_embed()

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        tracked_role_ids = {RAT_PATRON_ROLE_ID}
        before_roles = {role.id for role in before.roles if role.id in tracked_role_ids}
        after_roles = {role.id for role in after.roles if role.id in tracked_role_ids}

        if before_roles != after_roles or before.display_name != after.display_name:
            if before_roles or after_roles:
                await self.sync_embed()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if self._member_can_affect_embed(member):
            await self.sync_embed()

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if self._member_can_affect_embed(member):
            await self.sync_embed()


async def setup(bot: commands.Bot):
    await bot.add_cog(SupportersEmbed(bot))