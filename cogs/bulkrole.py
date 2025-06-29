import os
import json
import logging
import discord
from discord.ext import commands
from discord import app_commands, Embed, Interaction, ui
from discord.utils import get
from typing import List

# Setup logging
logging.basicConfig(level=logging.INFO)

PRESET_FILE = "role_presets.json"
REQUIRED_ROLE_NAME = "Assistant"  # Set to your server's assistant/admin role

if not os.path.exists(PRESET_FILE):
    with open(PRESET_FILE, "w") as f:
        json.dump({}, f)

def load_presets():
    """Load role presets from the file, or return empty dict if error."""
    try:
        with open(PRESET_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logging.error(f"Error loading presets: {e}")
        return {}

def save_presets(presets):
    """Save role presets to the file."""
    try:
        with open(PRESET_FILE, "w") as f:
            json.dump(presets, f, indent=2)
    except Exception as e:
        logging.error(f"Error saving presets: {e}")

def role_dict(guild):
    """Return a dict mapping lowercased role names to Role objects."""
    return {role.name.lower(): role for role in guild.roles}

def parse_roles(guild, names):
    """Find roles matching the given list of names. Returns ([Role], [not_found])."""
    lookup = role_dict(guild)
    found, not_found = [], []
    for name in [n.strip() for n in names if n.strip()]:
        r = lookup.get(name.lower())
        if r:
            found.append(r.id)
        else:
            not_found.append(name)
    return found, not_found

async def send_embed(channel, title, description, color=discord.Color.blue()):
    """Send an embed message to a channel."""
    embed = Embed(title=title, description=description, color=color)
    await channel.send(embed=embed)

class ConfirmView(ui.View):
    def __init__(self, member, add_roles, remove_roles, callback):
        super().__init__(timeout=60)
        self.member = member
        self.add_roles = add_roles
        self.remove_roles = remove_roles
        self.callback = callback
        self.value = None
        self.message = None  # Store the message for timeout handling

    @ui.button(label="Confirm", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: Interaction, button: ui.Button):
        await self.callback(interaction, True)
        self.stop()

    @ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: Interaction, button: ui.Button):
        await self.callback(interaction, False)
        self.stop()

    async def on_timeout(self):
        # Disable all buttons when the view times out
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass  # Message may have been deleted or already edited

class BulkRole(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.GUILD_ID = 1097913605082579024  # Set your guild/server ID here
        self.dm_wizards = {}  # user_id -> state dict

    # --- DM step-by-step preset creation and onboarding ---
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        # Only care about DMs
        if not isinstance(message.channel, discord.DMChannel):
            return

        guild = self.bot.get_guild(self.GUILD_ID)
        if not guild:
            await send_embed(message.channel, "Error", "❌ Bot couldn't access the configured server!", discord.Color.red())
            return

        member = guild.get_member(message.author.id)
        if not member:
            await send_embed(message.channel, "Error", "❌ You must be a member of the server to use this command.", discord.Color.red())
            return

        # Check role
        role = get(member.roles, name=REQUIRED_ROLE_NAME)
        if not role:
            await send_embed(message.channel, "Permission Denied", f"❌ You need the `{REQUIRED_ROLE_NAME}` role to use this feature.", discord.Color.red())
            return

        # --- Universal exit command ---
        if message.content.strip().lower() == "exit":
            if message.author.id in self.dm_wizards:
                del self.dm_wizards[message.author.id]
                await send_embed(message.channel, "Exited", "🚪 Exited the wizard/process. Type `!addpreset` to start again.", discord.Color.orange())
            else:
                await send_embed(message.channel, "No Process", "No process to exit. Type `!addpreset` to start a new one.", discord.Color.orange())
            return

        # --- Force-reset wizard state ---
        if message.content.strip() == "!resetwizard":
            if message.author.id in self.dm_wizards:
                del self.dm_wizards[message.author.id]
                await send_embed(message.channel, "Reset", "🧹 Wizard state reset.", discord.Color.orange())
            else:
                await send_embed(message.channel, "No State", "No wizard state to reset.", discord.Color.orange())
            return

        # --- Step-by-step wizard state ---
        if message.author.id in self.dm_wizards:
            state = self.dm_wizards[message.author.id]
            step = state["step"]

            if step == "preset_name":
                pname = message.content.strip()
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

            if step == "add_and_remove_roles":
                lines = message.content.strip().split("\n")
                if len(lines) < 2:
                    await send_embed(message.channel, "Invalid", "Please provide two lines: first for roles to add, second for roles to remove.", discord.Color.red())
                    return

                add_field = lines[0].strip().lower()
                remove_field = lines[1].strip().lower()
                add_roles, add_not_found = [], []
                if add_field not in ("none", ""):
                    add_roles, add_not_found = parse_roles(guild, add_field.split(","))
                remove_roles, remove_not_found = [], []
                if remove_field == "*":
                    remove_roles = ["*"]
                elif remove_field not in ("none", ""):
                    remove_roles, remove_not_found = parse_roles(guild, remove_field.split(","))
                
                not_found_msgs = []
                if add_not_found:
                    not_found_msgs.append(f"Add roles not found: {', '.join(add_not_found)}")
                if remove_not_found:
                    not_found_msgs.append(f"Remove roles not found: {', '.join(remove_not_found)}")
                if not_found_msgs:
                    await send_embed(message.channel, "Roles Not Found", "❌ " + " | ".join(not_found_msgs) + "\nPlease try again, or type `none`.", discord.Color.red())
                    return

                state["add_roles"] = add_roles
                state["remove_roles"] = remove_roles

                add_names = "None" if not add_roles else ", ".join([get(guild.roles, id=rid).name for rid in add_roles])
                if remove_roles == ["*"]:
                    remove_names = "ALL ROLES"
                elif not remove_roles:
                    remove_names = "None"
                else:
                    remove_names = ", ".join([get(guild.roles, id=rid).name for rid in remove_roles])

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

            if step == "confirm":
                answer = message.content.strip().lower()
                if answer == "confirm":
                    presets = load_presets()
                    presets[state["preset_name"]] = {
                        "add": state["add_roles"],
                        "remove": state["remove_roles"]
                    }
                    save_presets(presets)
                    await send_embed(message.channel, "Preset Saved", f"✅ Preset `{state['preset_name']}` saved.", discord.Color.green())
                    del self.dm_wizards[message.author.id]
                    return
                elif answer == "cancel":
                    await send_embed(message.channel, "Cancelled", "❌ Preset creation cancelled.", discord.Color.orange())
                    del self.dm_wizards[message.author.id]
                    return
                else:
                    await send_embed(message.channel, "Confirm", "Please type `confirm` to save, or `cancel` to abort.", discord.Color.orange())
                    return

        # --- Command triggers (non-wizard) ---
        if message.content.strip().startswith("!addpreset"):
            self.dm_wizards[message.author.id] = {"step": "preset_name"}
            await send_embed(
                message.channel,
                "Create Preset",
                "Let's create a new preset!\nWhat should the preset name be?"
            )
            return

        elif message.content.strip() == "!listpresets":
            presets = load_presets()
            if not presets:
                await send_embed(message.channel, "No Presets", "📭 No presets saved.", discord.Color.orange())
                return

            def resolve_names(role_ids):
                if role_ids == ["*"]:
                    return ["ALL ROLES"]
                return [
                    get(guild.roles, id=int(rid)).name
                    for rid in role_ids if get(guild.roles, id=int(rid))
                ]

            msg = ""
            for pname, pdata in presets.items():
                msg += f"🔹 `{pname}` — Add: {', '.join(resolve_names(pdata['add']))} | Remove: {', '.join(resolve_names(pdata['remove']))}\n"
            await send_embed(message.channel, "Presets", msg)
            return

        elif message.content.strip().startswith("!delpreset "):
            preset_name = message.content.strip().split(" ", 1)[1]
            presets = load_presets()
            if preset_name in presets:
                del presets[preset_name]
                save_presets(presets)
                await send_embed(message.channel, "Deleted", f"🗑️ Preset `{preset_name}` deleted.", discord.Color.green())
            else:
                await send_embed(message.channel, "Not Found", f"❌ Preset `{preset_name}` not found.", discord.Color.red())
            return

        elif message.content.strip().startswith("!"):
            await send_embed(
                message.channel,
                "Commands",
                "Commands:\n"
                "`!addpreset` — interactive preset creation\n"
                "`!listpresets` — list all presets\n"
                "`!delpreset <preset_name>` — delete a preset\n"
                "`!resetwizard` — force-reset the wizard if you’re stuck\n"
                "`exit` — exit any wizard/process at any time\n"
                "In the wizard, type `none` for no roles or `*` to remove all roles."
            )
            return

        # --- Onboarding/help message for any other DM ---
        await send_embed(
            message.channel,
            "Welcome",
            "👋 **Welcome! Here’s what you can do via DM:**\n"
            "• `!addpreset` — interactive preset creation wizard\n"
            "• `!listpresets` — list all saved presets\n"
            "• `!delpreset <preset_name>` — delete a preset\n"
            "• `!resetwizard` — force-reset the wizard if you’re stuck\n"
            "• `exit` — exit any wizard/process at any time\n"
            "In the wizard, type `none` for no roles or `*` to remove all roles.\n"
            "Just type a command above to get started!"
        )

    # --- Slash command sync ---
    @commands.Cog.listener()
    async def on_ready(self):
        await self.bot.wait_until_ready()
        try:
            synced = await self.bot.tree.sync()
            logging.info(f"Synced {len(synced)} commands")
        except Exception as e:
            logging.error(f"Failed to sync commands: {e}")

    # --- Autocomplete for preset names ---
    async def preset_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        presets = load_presets()
        return [
            app_commands.Choice(name=name, value=name)
            for name in presets
            if current.lower() in name.lower()
        ][:25]

    # --- Slash command ---
    @app_commands.command(name="bulk-role", description="Apply a bulk role preset to a user")
    @app_commands.describe(member="The user to apply the preset to", preset="The preset name")
    @app_commands.autocomplete(preset=preset_autocomplete)
    async def bulk_role(self, interaction: discord.Interaction, member: discord.Member, preset: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        if not guild:
            await interaction.followup.send(embed=Embed(title="Error", description="This command must be used in a server.", color=discord.Color.red()), ephemeral=True)
            return

        # Permission check
        if not guild.me.guild_permissions.manage_roles:
            await interaction.followup.send(embed=Embed(title="Permission Error", description="❌ I lack the `Manage Roles` permission.", color=discord.Color.red()), ephemeral=True)
            return

        # Check invoking user has required role
        user_member = guild.get_member(interaction.user.id)
        if not user_member or not get(user_member.roles, name=REQUIRED_ROLE_NAME):
            await interaction.followup.send(embed=Embed(title="Permission Denied", description=f"❌ You need the `{REQUIRED_ROLE_NAME}` role to use this command.", color=discord.Color.red()), ephemeral=True)
            return

        presets = load_presets()
        if preset not in presets:
            await interaction.followup.send(embed=Embed(title="Not Found", description=f"❌ Preset `{preset}` not found.", color=discord.Color.red()), ephemeral=True)
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

        description = (
            f"**Target:** {member.mention}\n"
            f"**Preset:** `{preset}`\n"
            f"**Will add:** {add_names}\n"
            f"**Will remove:** {remove_names}\n\n"
            "Do you want to proceed?"
        )
        embed = Embed(title="Confirm Bulk Role Action", description=description, color=discord.Color.orange())

        async def confirmed_callback(inter: Interaction, confirmed: bool):
            if inter.user.id != interaction.user.id:
                await inter.response.send_message("You can't respond to this confirmation.", ephemeral=True)
                return
            if not confirmed:
                try:
                    await inter.response.edit_message(
                        embed=Embed(title="Cancelled", description="❌ Action cancelled.", color=discord.Color.red()), view=None)
                except discord.NotFound:
                    await inter.followup.send("This action is no longer valid (confirmation expired).", ephemeral=True)
                return
            try:
                await member.remove_roles(*remove_roles, reason=f"Bulk role preset '{preset}' (by {interaction.user})")
                await member.add_roles(*add_roles, reason=f"Bulk role preset '{preset}' (by {interaction.user})")
                try:
                    await inter.response.edit_message(
                        embed=Embed(title="Success", description=f"✅ Applied preset `{preset}` to {member.mention}.", color=discord.Color.green()),
                        view=None
                    )
                except discord.NotFound:
                    await inter.followup.send(
                        f"✅ Applied preset `{preset}` to {member.mention}, but the confirmation message expired.", ephemeral=True)
            except discord.DiscordException as e:
                try:
                    await inter.response.edit_message(
                        embed=Embed(title="Error", description=f"❌ Error updating roles: {e}", color=discord.Color.red()), view=None)
                except discord.NotFound:
                    await inter.followup.send(
                        f"❌ Error updating roles: {e} (confirmation expired)", ephemeral=True)

        view = ConfirmView(member, add_roles, remove_roles, confirmed_callback)
        msg = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        view.message = msg

async def setup(bot):
    await bot.add_cog(BulkRole(bot))
