import os
import json
import logging
import discord
from discord.ext import commands
from discord import app_commands, Embed
from discord.utils import get

from data_paths import data_path

PRESET_FILE = data_path("role_presets.json")
REQUIRED_ROLE_NAME = ("Infantry School Admin")
GUILD_ID = 1097913605082579024  # Set your guild/server ID here

logging.basicConfig(level=logging.INFO)

if not os.path.exists(PRESET_FILE):
    with open(PRESET_FILE, "w") as f:
        json.dump({}, f)

def load_presets():
    try:
        with open(PRESET_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Error loading presets: {e}")
        return {}

def save_presets(presets):
    try:
        with open(PRESET_FILE, "w") as f:
            json.dump(presets, f, indent=2)
    except Exception as e:
        logging.error(f"Error saving presets: {e}")

def role_dict(guild):
    return {role.name.lower(): role for role in guild.roles}

def parse_roles(guild, names):
    lookup = role_dict(guild)
    found, not_found = [], []
    for name in [n.strip() for n in names if n.strip()]:
        role = lookup.get(name.lower())
        if role:
            found.append(role.id)
        else:
            not_found.append(name)
    return found, not_found

async def send_embed(channel, title, description, color=discord.Color.blue()):
    await channel.send(embed=Embed(title=title, description=description, color=color))

class BulkRole(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.dm_wizards = {}

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not isinstance(message.channel, discord.DMChannel):
            return

        guild = self.bot.get_guild(GUILD_ID)
        member = guild.get_member(message.author.id) if guild else None
        content = message.content.strip()

        # Only respond to /bulkrole or if user is in wizard session
        in_wizard = message.author.id in self.dm_wizards
        is_bulkrole_trigger = content.lower() == "/bulkrole"

        if not (is_bulkrole_trigger or in_wizard):
            # Ignore all other DMs (including recruitform answers, ! commands, etc.)
            return

        # Permission check: only for bulkrole trigger or wizard
        if not guild:
            await send_embed(message.channel, "Error", "❌ Bot couldn't access the configured server!", discord.Color.red())
            return
        if not member:
            await send_embed(message.channel, "Error", "❌ You must be a member of the server to use this command.", discord.Color.red())
            return
        if not get(member.roles, name=REQUIRED_ROLE_NAME):
            await send_embed(
                message.channel,
                "Permission Denied",
                f"❌ You need the `{REQUIRED_ROLE_NAME}` role to use this feature.",
                discord.Color.red()
            )
            return

        # If /bulkrole, start wizard
        if is_bulkrole_trigger:
            self.dm_wizards[message.author.id] = {"step": "preset_name"}
            await send_embed(
                message.channel,
                "Create Preset",
                "Let's create a new preset!\nWhat should the preset name be?"
            )
            return

        # Wizard state machine (only active if in_wizard)
        if in_wizard:
            state = self.dm_wizards[message.author.id]
            if content.lower() == "exit":
                self.dm_wizards.pop(message.author.id, None)
                await send_embed(message.channel, "Exited", "🚪 Exited the wizard/process. Type `/bulkrole` to start again.", discord.Color.orange())
                return
            if content == "!resetwizard":
                self.dm_wizards.pop(message.author.id, None)
                await send_embed(message.channel, "Reset", "🧹 Wizard state reset.", discord.Color.orange())
                return

            if state["step"] == "preset_name":
                pname = content
                if not pname:
                    await send_embed(message.channel, "Invalid", "Preset name can't be empty. Please enter a name:", discord.Color.red())
                    return
                state["preset_name"] = pname
                state["step"] = "add_and_remove_roles"
                await send_embed(
                    message.channel,
                    "Roles for Preset",
                    "Now, reply with **two lines**:\n**First line:** roles to add (comma-separated or 'none')\n**Second line:** roles to remove (comma-separated, 'none', or '*')"
                )
                return
            elif state["step"] == "add_and_remove_roles":
                lines = content.split("\n")
                if len(lines) < 2:
                    await send_embed(message.channel, "Invalid", "Please provide two lines: first for roles to add, second for roles to remove.", discord.Color.red())
                    return
                add_roles, add_nf = parse_roles(guild, lines[0].split(","))
                remove_roles, remove_nf = (["*"], []) if lines[1].strip() == "*" else parse_roles(guild, lines[1].split(","))
                not_found = []
                if add_nf: not_found.append(f"Add roles not found: {', '.join(add_nf)}")
                if remove_nf: not_found.append(f"Remove roles not found: {', '.join(remove_nf)}")
                if not_found:
                    await send_embed(message.channel, "Roles Not Found", "❌ " + " | ".join(not_found) + "\nPlease try again, or type `none`.", discord.Color.red())
                    return
                state["add_roles"], state["remove_roles"] = add_roles, remove_roles
                add_names = "None" if not add_roles else ", ".join([get(guild.roles, id=rid).name for rid in add_roles])
                remove_names = (
                    "ALL ROLES" if remove_roles == ["*"] else
                    "None" if not remove_roles else
                    ", ".join([get(guild.roles, id=rid).name for rid in remove_roles])
                )
                state["step"] = "confirm"
                await send_embed(
                    message.channel,
                    "Confirm Preset",
                    f"**Preset name:** `{state['preset_name']}`\n"
                    f"**Will add:** {add_names}\n"
                    f"**Will remove:** {remove_names}\n"
                    "Type `confirm` to save, or `cancel` to abort."
                )
                return
            elif state["step"] == "confirm":
                answer = content.lower()
                if answer == "confirm":
                    presets = load_presets()
                    presets[state["preset_name"]] = {
                        "add": state["add_roles"],
                        "remove": state["remove_roles"]
                    }
                    save_presets(presets)
                    await send_embed(message.channel, "Preset Saved", f"✅ Preset `{state['preset_name']}` saved.", discord.Color.green())
                    self.dm_wizards.pop(message.author.id, None)
                elif answer == "cancel":
                    await send_embed(message.channel, "Cancelled", "❌ Preset creation cancelled.", discord.Color.orange())
                    self.dm_wizards.pop(message.author.id, None)
                else:
                    await send_embed(message.channel, "Confirm", "Please type `confirm` to save, or `cancel` to abort.", discord.Color.orange())
                return

    @commands.Cog.listener()
    async def on_ready(self):
        await self.bot.wait_until_ready()
        try:
            synced = await self.bot.tree.sync()
            logging.info(f"Synced {len(synced)} commands")
        except Exception as e:
            logging.error(f"Failed to sync commands: {e}")

    async def preset_autocomplete(self, interaction: discord.Interaction, current: str):
        presets = load_presets()
        return [
            app_commands.Choice(name=name, value=name)
            for name in presets if current.lower() in name.lower()
        ][:25]

    @app_commands.command(name="bulk-role", description="Apply a bulk role preset to a user")
    @app_commands.describe(member="The user to apply the preset to", preset="The preset name")
    @app_commands.autocomplete(preset=preset_autocomplete)
    async def bulk_role(self, interaction: discord.Interaction, member: discord.Member, preset: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        if not guild:
            await interaction.followup.send("This command must be used in a server.", ephemeral=True)
            return
        if not guild.me.guild_permissions.manage_roles:
            await interaction.followup.send("❌ I lack the `Manage Roles` permission.", ephemeral=True)
            return
        user_member = guild.get_member(interaction.user.id)
        if not user_member or not get(user_member.roles, name=REQUIRED_ROLE_NAME):
            await interaction.followup.send(f"❌ You need the `{REQUIRED_ROLE_NAME}` role to use this command.", ephemeral=True)
            return
        presets = load_presets()
        if preset not in presets:
            await interaction.followup.send(f"❌ Preset `{preset}` not found.", ephemeral=True)
            return

        add_roles = [get(guild.roles, id=int(rid)) for rid in presets[preset]["add"]]
        add_roles = [r for r in add_roles if r]
        if presets[preset]["remove"] == ["*"]:
            remove_roles = [role for role in member.roles if not role.managed and role != guild.default_role]
        else:
            remove_roles = [get(guild.roles, id=int(rid)) for rid in presets[preset]["remove"]]
            remove_roles = [r for r in remove_roles if r]

        add_names = "None" if not add_roles else ", ".join([r.name for r in add_roles])
        if presets[preset]["remove"] == ["*"]:
            remove_names = "ALL ROLES"
        elif not remove_roles:
            remove_names = "None"
        else:
            remove_names = ", ".join([r.name for r in remove_roles])

        try:
            await member.remove_roles(*remove_roles, reason=f"Bulk role preset '{preset}' (by {interaction.user})")
            await member.add_roles(*add_roles, reason=f"Bulk role preset '{preset}' (by {interaction.user})")

            await interaction.channel.send(
                f"✅ {member.mention} had roles updated via bulk preset `{preset}` by {interaction.user.mention}.\n"
                f"**Added:** {add_names}\n"
                f"**Removed:** {remove_names}"
            )
            await interaction.followup.send("Bulk role change applied and posted publicly.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Error updating roles: {e}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(BulkRole(bot))
