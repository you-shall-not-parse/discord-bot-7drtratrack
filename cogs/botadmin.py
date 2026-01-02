import discord
from discord.ext import commands
from discord import app_commands

import asyncio
import os
import sys
import subprocess  

# --- Admin access (edit these) ---
GUILD_ID = 1097913605082579024

# Owner-only: only this Discord user ID can use these commands
OWNER_ID = 1109147750932676649  # Replace with your Discord user ID


def _is_admin(interaction: discord.Interaction) -> bool:
    return getattr(interaction.user, "id", None) == OWNER_ID


def _build_extension_map(bot: commands.Bot) -> dict[str, str]:
    """Maps lowercase aliases -> canonical extension name.

    Prevents case mismatches like `cogs.embedmanager` vs `cogs.EmbedManager`.
    """
    mapping: dict[str, str] = {}

    def _add(candidate: str) -> None:
        if not candidate:
            return
        mapping.setdefault(candidate.lower(), candidate)
        short = candidate.removeprefix("cogs.")
        mapping.setdefault(short.lower(), candidate)

    # Loaded extensions are authoritative
    for ext in bot.extensions.keys():
        _add(ext)

    # Also include any files under cogs/
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
            _add(f"cogs.{module}")
    except Exception:
        pass

    return mapping


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
        await interaction.response.send_message("Restarting...", ephemeral=True)

        python = sys.executable
        argv = [python, *sys.argv]

        async def _do_restart():
            # Don't await close() here (it can hang); start it and proceed.
            try:
                asyncio.create_task(self.bot.close())
            except Exception:
                pass

            # Give the response a moment to flush before replacing the process.
            await asyncio.sleep(1)

            try:
                os.execv(python, argv)
            except Exception:
                os._exit(1)

        asyncio.create_task(_do_restart())

    @app_commands.command(
        name="reload_cog",
        description="Reload a specific cog/extension (e.g. echo or cogs.echo).",
    )
    @app_commands.check(_is_admin)
    async def reload_cog(self, interaction: discord.Interaction, cog: str):
        await interaction.response.defer(ephemeral=True, thinking=True)

        raw = cog.strip()
        if not raw:
            await interaction.followup.send("Provide a cog name.", ephemeral=True)
            return

        extension_map = _build_extension_map(self.bot)
        normalized = raw if raw.startswith("cogs.") else f"cogs.{raw}"
        extension = (
            extension_map.get(normalized.lower())
            or extension_map.get(raw.lower())
            or normalized
        )

        if not extension.startswith("cogs."):
            extension = f"cogs.{extension}"

        try:
            await self.bot.reload_extension(extension)
            # Re-sync slash commands for this guild so any app_command edits appear immediately.
            try:
                if interaction.guild_id:
                    await self.bot.tree.sync(guild=discord.Object(id=interaction.guild_id))
            except Exception:
                pass

            # Some cogs do most visible work on startup events/timers.
            # After reload, force an immediate refresh for known cogs.
            post_actions: list[str] = []
            try:
                if extension.lower() == "cogs.calendarcog":
                    calendar_cog = self.bot.get_cog("CalendarCog")
                    if calendar_cog and interaction.guild_id:
                        guild = self.bot.get_guild(interaction.guild_id)
                        if guild:
                            await calendar_cog.update_calendar(guild)
                            post_actions.append("calendar refreshed")
                elif extension.lower() == "cogs.recruitform":
                    recruit_cog = self.bot.get_cog("RecruitFormCog")
                    if recruit_cog:
                        # Re-post the embed (reload alone won't re-trigger on_ready)
                        await recruit_cog.on_ready()
                        post_actions.append("recruit form embed reposted")
            except Exception as e:
                post_actions.append(f"post-action failed: {type(e).__name__}: {e}")

            suffix = f" ({', '.join(post_actions)})" if post_actions else ""
            await interaction.followup.send(f"Reloaded `{extension}`{suffix}.", ephemeral=True)
        except commands.ExtensionNotLoaded:
            try:
                await self.bot.load_extension(extension)
                try:
                    if interaction.guild_id:
                        await self.bot.tree.sync(guild=discord.Object(id=interaction.guild_id))
                except Exception:
                    pass

                await interaction.followup.send(
                    f"`{extension}` wasn't loaded; loaded it now.",
                    ephemeral=True,
                )
            except Exception as e:
                import logging
                logging.exception("Failed to load extension %s", extension)
                await interaction.followup.send(
                    f"Failed to load `{extension}`: {type(e).__name__}: {e}",
                    ephemeral=True,
                )
        except Exception as e:
            import logging
            logging.exception("Failed to reload extension %s", extension)
            await interaction.followup.send(
                f"Failed to reload `{extension}`: {type(e).__name__}: {e}",
                ephemeral=True,
            )

    @app_commands.command(
        name="git_pull",
        description="Run `git pull --ff-only` on the bot repo (admin only).",
    )
    @app_commands.check(_is_admin)
    async def git_pull(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"  # fail fast if auth would prompt

        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "pull",
                "--ff-only",
                cwd=repo_root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError:
            await interaction.followup.send(
                "`git` is not available on PATH on the machine running the bot.",
                ephemeral=True,
            )
            return

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=45)
        except TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            await interaction.followup.send(
                "`git pull` timed out (likely waiting on network/auth).",
                ephemeral=True,
            )
            return

        output = (stdout or b"") + (stderr or b"")
        text = output.decode(errors="replace").strip() or "(no output)"

        if len(text) > 1800:
            text = text[:1800] + "\n... (truncated)"

        if proc.returncode == 0:
            await interaction.followup.send(f"```\n{text}\n```", ephemeral=True)
        else:
            await interaction.followup.send(
                f"`git pull` failed (exit {proc.returncode}):\n```\n{text}\n```",
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
    if not isinstance(OWNER_ID, int) or OWNER_ID <= 0:
        raise RuntimeError("Set OWNER_ID (non-zero int) at top of cogs/botadmin.py")
    await bot.add_cog(BotAdmin(bot))