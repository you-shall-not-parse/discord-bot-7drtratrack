import discord
from discord import app_commands
from discord.ext import commands

from clan_t17_lookup import ClanT17Lookup

GUILD_ID = 1097913605082579024
T17_ADMIN_ROLE_ID = 1213495462632361994


def _can_manage_t17(interaction: discord.Interaction) -> bool:
    user = interaction.user
    return isinstance(user, discord.Member) and any(role.id == T17_ADMIN_ROLE_ID for role in user.roles)


class T17Lookup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.lookup = ClanT17Lookup()

    @app_commands.command(name="t17_overwrite", description="Override a member's shared clan T17 ID")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.check(_can_manage_t17)
    async def t17_overwrite(self, interaction: discord.Interaction, member: discord.Member, t17_id: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        clean_t17_id = t17_id.strip()
        if not clean_t17_id:
            await interaction.response.send_message("Provide a non-empty T17 ID.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        self.lookup.set_manual_override(interaction.guild.id, member.id, clean_t17_id, updated_by=interaction.user.id)

        refresh_failures: list[str] = []

        hellor_cog = self.bot.get_cog("HellorLeaderboard")
        if hellor_cog is not None and hasattr(hellor_cog, "refresh_member_override"):
            try:
                await hellor_cog.refresh_member_override(member)
            except Exception as exc:
                refresh_failures.append(f"hellor leaderboard: {exc}")

        roster_cog = self.bot.get_cog("Rosterizer")
        if roster_cog is not None and hasattr(roster_cog, "refresh_member_override"):
            try:
                await roster_cog.refresh_member_override(member)
            except Exception as exc:
                refresh_failures.append(f"rosterizer: {exc}")

        if refresh_failures:
            await interaction.followup.send(
                "Stored the shared T17 override, but some dependent refreshes failed:\n" + "\n".join(refresh_failures),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"Stored shared T17 override for {member.display_name} -> {clean_t17_id}.",
            ephemeral=True,
        )

    @t17_overwrite.error
    async def t17_overwrite_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CheckFailure):
            if interaction.response.is_done():
                await interaction.followup.send("You need the configured T17 admin role to use this command.", ephemeral=True)
            else:
                await interaction.response.send_message("You need the configured T17 admin role to use this command.", ephemeral=True)
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(T17Lookup(bot))