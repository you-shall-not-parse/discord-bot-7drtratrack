import re
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import MAIN_GUILD_ID

GUILD_ID = MAIN_GUILD_ID
APPLY_ROLE_ADMIN_ID = 1213495462632361994  # change if needed


def _is_admin(interaction: discord.Interaction) -> bool:
    user = interaction.user
    return isinstance(user, discord.Member) and any(r.id == APPLY_ROLE_ADMIN_ID for r in user.roles)


class ApplyRoleToMessage(commands.Cog):
    """Admin command to apply a role to users who reacted with an emoji on a message."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("ApplyRoleToMessage")

    @app_commands.command(name="applyroletomessage", description="Apply a role to users who reacted to a message with an emoji")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.check(_is_admin)
    @app_commands.describe(message_link="Link to the message (paste message link)", emoji="Emoji to match (unicode or <:name:id>)", role="Role to apply")
    async def applyroletomessage(self, interaction: discord.Interaction, message_link: str, emoji: str, role: discord.Role):
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in the server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        # Parse message link: https://discord.com/channels/<guild>/<channel>/<message>
        m = re.search(r"/channels/\d+/(\d+)/(\d+)", message_link)
        if not m:
            await interaction.followup.send("Couldn't parse message link. Use the full message URL.", ephemeral=True)
            return

        channel_id = int(m.group(1))
        message_id = int(m.group(2))

        try:
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        except Exception as e:
            self.logger.exception("Failed to fetch channel %s: %s", channel_id, e)
            await interaction.followup.send("Failed to fetch channel for that link.", ephemeral=True)
            return

        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            await interaction.followup.send("Target channel is not a text channel or thread.", ephemeral=True)
            return

        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            await interaction.followup.send("Message not found.", ephemeral=True)
            return
        except Exception as e:
            self.logger.exception("Failed to fetch message %s in %s: %s", message_id, channel_id, e)
            await interaction.followup.send("Failed to fetch message (exception).", ephemeral=True)
            return

        # Normalize emoji input
        emoji = emoji.strip()

        def emoji_matches(reaction_emoji, provided: str) -> bool:
            # reaction_emoji can be str (unicode) or PartialEmoji/Emoji
            try:
                if isinstance(reaction_emoji, str):
                    return reaction_emoji == provided
                # provided may be like '<:name:id>' or '<a:name:id>'
                m = re.match(r"^<a?:\w+:(\d+)>$", provided)
                if m:
                    return getattr(reaction_emoji, "id", None) == int(m.group(1))
                # provided could be just the name of custom emoji 'name' (rare)
                if hasattr(reaction_emoji, "name") and reaction_emoji.name == provided:
                    return True
                # fallback compare string form
                return str(reaction_emoji) == provided
            except Exception:
                return False

        # Find matching reaction
        target_reaction: Optional[discord.Reaction] = None
        for react in message.reactions:
            if emoji_matches(react.emoji, emoji):
                target_reaction = react
                break

        if target_reaction is None:
            await interaction.followup.send("No matching reaction found on that message.", ephemeral=True)
            return

        # Permission checks
        guild = interaction.guild
        me = guild.me
        if me is None:
            me = guild.get_member(self.bot.user.id)

        if not guild.me.guild_permissions.manage_roles:
            await interaction.followup.send("I don't have Manage Roles permission in this server.", ephemeral=True)
            return

        if role.managed:
            await interaction.followup.send("That role is managed by an integration and cannot be assigned.", ephemeral=True)
            return

        # Check role hierarchy
        bot_top = guild.me.top_role.position if guild.me else 0
        if bot_top <= role.position:
            await interaction.followup.send("I cannot assign that role because it is higher or equal to my top role.", ephemeral=True)
            return

        # Collect users who reacted (note: users() is an async iterator)
        users = []
        try:
            async for u in target_reaction.users():
                # skip the bot itself
                if u.id == self.bot.user.id:
                    continue
                users.append(u)
        except Exception as e:
            self.logger.exception("Failed to iterate reaction users: %s", e)
            await interaction.followup.send("Failed to read reaction users.", ephemeral=True)
            return

        if not users:
            await interaction.followup.send("No users found who reacted with that emoji.", ephemeral=True)
            return

        applied = 0
        failed = []
        for u in users:
            try:
                member = guild.get_member(u.id)
                if member is None:
                    try:
                        member = await guild.fetch_member(u.id)
                    except Exception:
                        member = None

                if member is None:
                    failed.append((u.id, "not in guild"))
                    continue

                if role in member.roles:
                    continue

                await member.add_roles(role, reason=f"Applied by {interaction.user} via applyroletomessage")
                applied += 1
            except Exception as e:
                self.logger.exception("Failed to add role to %s: %s", getattr(u, "id", u), e)
                failed.append((getattr(u, "id", u), str(e)))

        summary = f"Applied role to {applied} user(s)."
        if failed:
            summary += f" {len(failed)} failures." 

        await interaction.followup.send(summary, ephemeral=True)

    @applyroletomessage.error
    async def applyroletomessage_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            if interaction.response.is_done():
                await interaction.followup.send("You need the configured admin role to use this command.", ephemeral=True)
            else:
                await interaction.response.send_message("You need the configured admin role to use this command.", ephemeral=True)
            return
        self.logger.exception("applyroletomessage command error: %s", error)
        raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(ApplyRoleToMessage(bot))
