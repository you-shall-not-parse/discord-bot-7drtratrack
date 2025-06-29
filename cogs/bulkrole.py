# ---------------- BULK ROLE PRESET HANDLER ----------------
import json
from discord import app_commands

PRESET_FILE = "role_presets.json"
if not os.path.exists(PRESET_FILE):
    with open(PRESET_FILE, "w") as f:
        json.dump({}, f)

def load_presets():
    with open(PRESET_FILE, "r") as f:
        return json.load(f)

def save_presets(presets):
    with open(PRESET_FILE, "w") as f:
        json.dump(presets, f, indent=2)

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if isinstance(message.channel, discord.DMChannel):
        parts = message.content.strip().split(" ", 3)
        if len(parts) >= 4 and parts[0] == "!addpreset":
            preset_name = parts[1]
            guild = bot.get_guild(GUILD_ID)
            add_roles, remove_roles = [], []
            not_found = []

            for rname in parts[2].split(","):
                role = discord.utils.get(guild.roles, name=rname.strip())
                if role:
                    add_roles.append(role.id)
                else:
                    not_found.append(rname.strip())

            if parts[3].strip() == "*":
                remove_roles = ["*"]
            else:
                for rname in parts[3].split(","):
                    role = discord.utils.get(guild.roles, name=rname.strip())
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

            guild = bot.get_guild(GUILD_ID)
            def resolve_names(role_ids):
                if role_ids == ["*"]:
                    return ["ALL ROLES"]
                return [discord.utils.get(guild.roles, id=int(rid)).name for rid in role_ids if discord.utils.get(guild.roles, id=int(rid))]

            msg = "📋 **Presets:**\n"
            for pname, pdata in presets.items():
                msg += f"🔹 `{pname}` — Add: {resolve_names(pdata['add'])} | Remove: {resolve_names(pdata['remove'])}\n"
            await message.channel.send(msg)

@app_commands.command(name="bulk-role", description="Apply a bulk role preset to a user")
@app_commands.describe(member="The user to apply the preset to", preset="The preset name")
async def bulk_role(interaction: discord.Interaction, member: discord.Member, preset: str):
    presets = load_presets()
    if preset not in presets:
        await interaction.response.send_message(f"❌ Preset `{preset}` not found.", ephemeral=True)
        return

    guild = interaction.guild
    add_roles = [discord.utils.get(guild.roles, id=int(rid)) for rid in presets[preset]["add"]]

    if presets[preset]["remove"] == ["*"]:
        remove_roles = [role for role in member.roles if not role.managed and role != guild.default_role]
    else:
        remove_roles = [discord.utils.get(guild.roles, id=int(rid)) for rid in presets[preset]["remove"] if discord.utils.get(guild.roles, id=int(rid))]

    await member.remove_roles(*remove_roles)
    await member.add_roles(*add_roles)
    await interaction.response.send_message(f"✅ Applied preset `{preset}` to {member.mention}.")

@bot.event
async def on_ready():
    await bot.wait_until_ready()
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
