import discord
from discord.ext import commands
from discord import app_commands

import os
import sys
import subprocess  

# --- Admin access (edit these) ---
GUILD_ID = 1097913605082579024
ADMIN_ROLE_ID = 1213495462632361994

# Optional: allow a specific owner user ID regardless of role
OWNER_ID = 1109147750932676649  # Replace with your Discord user ID (or set to 0 to disable)


def _is_admin(interaction: discord.Interaction) -> bool:
    if OWNER_ID and getattr(interaction.user, "id", None) == OWNER_ID:
        return True
    user = interaction.user
    if not isinstance(user, discord.Member):
        return False
    return any(role.id == ADMIN_ROLE_ID for role in user.roles)


@app_commands.guilds(discord.Object(id=GUILD_ID))
class BotAdmin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="shutdown", description="Shut down the bot (owner only)")
    @app_commands.check(_is_admin)
    async def shutdown(self, interaction: discord.Interaction):
        await interaction.response.send_message("Shutting down...")
        await self.bot.close()

    @app_commands.command(name="restart", description="Restart the bot (owner only)")
    @app_commands.check(_is_admin)
    async def restart(self, interaction: discord.Interaction):
        await interaction.response.send_message("Restarting...")
        await self.bot.close()
        python = sys.executable
        os.execl(python, python, *sys.argv)

    @app_commands.command(
        name="reload_cog",
        description="Reload a specific cog/extension (e.g. echo or cogs.echo).",
    )
    @app_commands.check(_is_admin)
    async def reload_cog(self, interaction: discord.Interaction, cog: str):
        await interaction.response.defer(ephemeral=True, thinking=True)

        extension = cog.strip()
        if not extension:
            await interaction.followup.send("Provide a cog name.", ephemeral=True)
            return
        if not extension.startswith("cogs."):
            extension = f"cogs.{extension}"

        try:
            await self.bot.reload_extension(extension)
            await interaction.followup.send(f"Reloaded `{extension}`.", ephemeral=True)
        except commands.ExtensionNotLoaded:
            try:
                await self.bot.load_extension(extension)
                await interaction.followup.send(
                    f"`{extension}` wasn't loaded; loaded it now.",
                    ephemeral=True,
                )
            except Exception as e:
                await interaction.followup.send(
                    f"Failed to load `{extension}`: {type(e).__name__}: {e}",
                    ephemeral=True,
                )
        except Exception as e:
            await interaction.followup.send(
                f"Failed to reload `{extension}`: {type(e).__name__}: {e}",
                ephemeral=True,
            )

    @reload_cog.autocomplete("cog")
    async def reload_cog_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        current_lower = (current or "").lower()

        suggestions: list[str] = []

        # Loaded extensions first
        for ext in self.bot.extensions.keys():
            suggestions.append(ext)

        # Then any .py files in cogs/ as potential extensions
        try:
            cogs_dir = os.path.dirname(__file__)
            for filename in os.listdir(cogs_dir):
                if not filename.endswith(".py"):
                    continue
                if filename.startswith("_"):
                    continue
                module = filename[:-3]
                if module.lower() == "__init__":
                    continue
                suggestions.append(f"cogs.{module}")
        except Exception:
            pass

        # De-dupe while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for item in suggestions:
            if item in seen:
                continue
            seen.add(item)
            unique.append(item)

        # Filter by what the user typed
        if current_lower:
            unique = [s for s in unique if current_lower in s.lower()]

        # Discord max: 25 choices
        unique = unique[:25]

        # Show friendly name but return full value
        choices: list[app_commands.Choice[str]] = []
        for value in unique:
            name = value.removeprefix("cogs.")
            choices.append(app_commands.Choice(name=name, value=value))
        return choices


async def setup(bot: commands.Bot):
    if not isinstance(GUILD_ID, int) or GUILD_ID <= 0:
        raise RuntimeError("Set GUILD_ID (non-zero int) at top of cogs/botadmin.py")
    if not isinstance(ADMIN_ROLE_ID, int) or ADMIN_ROLE_ID <= 0:
        raise RuntimeError("Set ADMIN_ROLE_ID (non-zero int) at top of cogs/botadmin.py")
    await bot.add_cog(BotAdmin(bot))