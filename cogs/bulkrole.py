import os
import json
import discord
from discord.ext import commands
from discord import app_commands
from discord.utils import get

PRESET_FILE = "role_presets.json"
REQUIRED_ROLE_NAME = "Assistant"  # <-- set this to your required role name

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

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        if isinstance(message.channel, discord.DMChannel):
            guild = self.bot.get_guild(self.GUILD_ID)
            member = guild.get_member(message.author.id)
            if not member:
                await message.channel.send("❌ You must be a member of the server to use this command.")
                return

            # Check if member has the required role
            role = get(member.roles, name=REQUIRED_ROLE_NAME)
            if not role:
                await message.channel.send(f"❌ You need the `{REQUIRED_ROLE_NAME}` role to use this feature.")
                return

            parts = message.content.strip().split(" ", 3)
            if len(parts) >= 4 and parts[0] == "!addpreset":
                preset_name = parts[1]
                add_roles, remove_roles = [], []
                not_found = []

                # Case-insensitive role lookup
                for rname in parts[2].split(","):
                    role = discord.utils.find(lambda r: r.name.lower() == rname.strip().lower(), guild.roles)
                    if role:
                        add_roles.append(role.id)
                    else:
                        not_found.append(rname.strip())

                if parts[3].strip() == "*":
                    remove_roles = ["*"]
                else:
                    for rname in parts[3].split(","):
                        role = discord.utils.find(lambda r: r.name.lower() == rname.strip().lower(), guild.roles)
                        if role:
                            remove_roles.append(role.id)
                        else:
                            not_found.append(rname.strip())

                if not_found:
                    await message.channel.send(f"❌ These roles were not found: {', '.join(not_found)}")
                    return

                presets = load_presets()
                presets[preset_name] = {"add": add_roles, "remove": remove_roles}
                save_presets(presets)
                await message.channel.send(f"✅ Preset `{preset_name}` saved.")

            elif message.content.strip() == "!listpresets":
                presets = load_presets()
                if not presets:
                    await message.channel.send("📭 No presets saved.")
                    return

                def resolve_names(role_ids):
                    if role_ids == ["*"]:
                        return ["ALL ROLES"]
                    return [discord.utils.get(guild.roles, id=int(rid)).name for rid in role_ids if discord.utils.get(guild.roles, id=int(rid))]

                msg = "📋 **Presets:**\n"
                for pname, pdata in presets.items():
                    msg += f"🔹 `{pname}` — Add: {resolve_names(pdata['add'])} | Remove: {resolve_names(pdata['remove'])}\n"
                await message.channel.send(msg)

            elif message.content.strip().startswith("!delpreset "):
                preset_name = message.content.strip().split(" ", 1)[1]
                presets = load_presets()
                if preset_name in presets:
                    del presets[preset_name]
                    save_presets(presets)
                    await message.channel.send(f"🗑️ Preset `{preset_name}` deleted.")
                else:
                    await message.channel.send(f"❌ Preset `{preset_name}` not found.")

    @commands.Cog.listener()
    async def on_ready(self):
        await self.bot.wait_until_ready()
        try:
            synced = await self.bot.tree.sync()
            print(f"Synced {len(synced)} commands")
        except Exception as e:
            print(f"Failed to sync commands: {e}")

    @app_commands.command(name="bulk-role", description="Apply a bulk role preset to a user")
    @app_commands.describe(member="The user to apply the preset to", preset="The preset name")
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
