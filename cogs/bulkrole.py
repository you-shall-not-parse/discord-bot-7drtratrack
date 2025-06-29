import os
import json
import discord
from discord.ext import commands
from discord import app_commands
from discord.utils import get
from typing import List 

PRESET_FILE = "role_presets.json"
REQUIRED_ROLE_NAME = "Assistant"  # Set to your server's assistant/admin role

if not os.path.exists(PRESET_FILE):
    with open(PRESET_FILE, "w") as f:
        json.dump({}, f)

def load_presets():
    with open(PRESET_FILE, "r") as f:
        return json.load(f)

def save_presets(presets):
    with open(PRESET_FILE, "w") as f:
        json.dump(presets, f, indent=2)

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
            await message.channel.send("❌ Bot couldn't access the configured server!")
            return

        member = guild.get_member(message.author.id)
        if not member:
            await message.channel.send("❌ You must be a member of the server to use this command.")
            return

        # Check role
        role = get(member.roles, name=REQUIRED_ROLE_NAME)
        if not role:
            await message.channel.send(f"❌ You need the `{REQUIRED_ROLE_NAME}` role to use this feature.")
            return

        # --- Universal exit command ---
        if message.content.strip().lower() == "exit":
            if message.author.id in self.dm_wizards:
                del self.dm_wizards[message.author.id]
                await message.channel.send("🚪 Exited the wizard/process. Type `!addpreset` to start again.")
            else:
                await message.channel.send("No process to exit. Type `!addpreset` to start a new one.")
            return

        # --- Force-reset wizard state ---
        if message.content.strip() == "!resetwizard":
            if message.author.id in self.dm_wizards:
                del self.dm_wizards[message.author.id]
                await message.channel.send("🧹 Wizard state reset.")
            else:
                await message.channel.send("No wizard state to reset.")
            return

        # --- Step-by-step wizard state ---
        if message.author.id in self.dm_wizards:
            state = self.dm_wizards[message.author.id]
            step = state["step"]

            if step == "preset_name":
                pname = message.content.strip()
                if not pname:
                    await message.channel.send("Preset name can't be empty. Please enter a name:")
                    return
                state["preset_name"] = pname
                state["step"] = "add_and_remove_roles"
                await message.channel.send(
                    "Now, reply with **two lines**:\n"
                    "**First line:** roles to add (comma-separated or 'none')\n"
                    "**Second line:** roles to remove (comma-separated, 'none', or '*')\n"
                    "Example:\n"
                    "Moderator, Subscriber\n"
                    "Muted, Banned\n\n"
                    "Or:\n"
                    "none\n"
                    "Staff\n"
                    "Available roles:\n" +
                    ", ".join([r.name for r in guild.roles if not r.managed and r != guild.default_role])
                )
                return

            if step == "add_and_remove_roles":
                lines = message.content.strip().split("\n")
                if len(lines) < 2:
                    await message.channel.send("Please provide two lines: first for roles to add, second for roles to remove.")
                    return

                add_field = lines[0].strip().lower()
                remove_field = lines[1].strip().lower()

                add_roles, add_not_found = [], []
                if add_field in ("none", ""):
                    add_roles = []
                else:
                    for rname in add_field.split(","):
                        rname = rname.strip()
                        if rname in ("none", ""):
                            continue
                        role = discord.utils.find(lambda r: r.name.lower() == rname.lower(), guild.roles)
                        if role:
                            add_roles.append(role.id)
                        else:
                            add_not_found.append(rname)

                remove_roles, remove_not_found = [], []
                if remove_field == "*":
                    remove_roles = ["*"]
                elif remove_field in ("none", ""):
                    remove_roles = []
                else:
                    for rname in remove_field.split(","):
                        rname = rname.strip()
                        if rname in ("none", ""):
                            continue
                        role = discord.utils.find(lambda r: r.name.lower() == rname.lower(), guild.roles)
                        if role:
                            remove_roles.append(role.id)
                        else:
                            remove_not_found.append(rname)

                not_found_msgs = []
                if add_not_found:
                    not_found_msgs.append(f"Add roles not found: {', '.join(add_not_found)}")
                if remove_not_found:
                    not_found_msgs.append(f"Remove roles not found: {', '.join(remove_not_found)}")
                if not_found_msgs:
                    await message.channel.send("❌ " + " | ".join(not_found_msgs) + "\nPlease try again, or type `none`.")
                    return

                state["add_roles"] = add_roles
                state["remove_roles"] = remove_roles

                # Show summary and ask for confirmation
                add_names = (
                    "None" if not add_roles else
                    ", ".join([discord.utils.get(guild.roles, id=rid).name for rid in add_roles])
                )
                if remove_roles == ["*"]:
                    remove_names = "ALL ROLES"
                elif not remove_roles:
                    remove_names = "None"
                else:
                    remove_names = ", ".join([discord.utils.get(guild.roles, id=rid).name for rid in remove_roles])
                state["step"] = "confirm"
                await message.channel.send(
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
                    await message.channel.send(f"✅ Preset `{state['preset_name']}` saved.")
                    del self.dm_wizards[message.author.id]
                    return
                elif answer == "cancel":
                    await message.channel.send("❌ Preset creation cancelled.")
                    del self.dm_wizards[message.author.id]
                    return
                else:
                    await message.channel.send("Please type `confirm` to save, or `cancel` to abort.")
                    return

        # --- Command triggers (non-wizard) ---
        if message.content.strip().startswith("!addpreset"):
            self.dm_wizards[message.author.id] = {"step": "preset_name"}
            await message.channel.send(
                "Let's create a new preset!\nWhat should the preset name be?"
            )
            return

        elif message.content.strip() == "!listpresets":
            presets = load_presets()
            if not presets:
                await message.channel.send("📭 No presets saved.")
                return

            def resolve_names(role_ids):
                if role_ids == ["*"]:
                    return ["ALL ROLES"]
                return [
                    discord.utils.get(guild.roles, id=int(rid)).name
                    for rid in role_ids if discord.utils.get(guild.roles, id=int(rid))
                ]

            msg = "📋 **Presets:**\n"
            for pname, pdata in presets.items():
                msg += f"🔹 `{pname}` — Add: {resolve_names(pdata['add'])} | Remove: {resolve_names(pdata['remove'])}\n"
            await message.channel.send(msg)
            return

        elif message.content.strip().startswith("!delpreset "):
            preset_name = message.content.strip().split(" ", 1)[1]
            presets = load_presets()
            if preset_name in presets:
                del presets[preset_name]
                save_presets(presets)
                await message.channel.send(f"🗑️ Preset `{preset_name}` deleted.")
            else:
                await message.channel.send(f"❌ Preset `{preset_name}` not found.")
            return

        elif message.content.strip().startswith("!"):
            await message.channel.send(
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
        await message.channel.send(
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
            print(f"Synced {len(synced)} commands")
        except Exception as e:
            print(f"Failed to sync commands: {e}")

    # --- Autocomplete for preset names ---
    async def preset_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> List[app_commands.Choice[str]]:
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
        presets = load_presets()
        if preset not in presets:
            await interaction.response.send_message(f"❌ Preset `{preset}` not found.", ephemeral=True)
            return

        guild = interaction.guild
        add_roles = [discord.utils.get(guild.roles, id=int(rid)) for rid in presets[preset]["add"]]
        if presets[preset]["remove"] == ["*"]:
            remove_roles = [role for role in member.roles if not role.managed and role != guild.default_role]
        else:
            remove_roles = [
                discord.utils.get(guild.roles, id=int(rid))
                for rid in presets[preset]["remove"]
                if discord.utils.get(guild.roles, id=int(rid))
            ]

        await member.remove_roles(*remove_roles)
        await member.add_roles(*add_roles)
        await interaction.response.send_message(f"✅ Applied preset `{preset}` to {member.mention}.")

async def setup(bot):
    await bot.add_cog(BulkRole(bot))
