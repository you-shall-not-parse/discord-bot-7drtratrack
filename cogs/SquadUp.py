import discord
from discord import app_commands
from discord.ext import commands
import json
import os

CONFIG_FILE = "squadup_config.json"
DATA_FILE = "squadup_data.json"

NATO_SQUAD_NAMES = [
    "Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot",
    "Golf", "Hotel", "India", "Juliet", "Kilo", "Lima",
    "Mike", "November", "Oscar", "Papa", "Quebec", "Romeo",
    "Sierra", "Tango", "Uniform", "Victor", "Whiskey", "X-ray",
    "Yankee", "Zulu"
]

def load_json(filename, default):
    if not os.path.exists(filename):
        with open(filename, "w") as f:
            json.dump(default, f, indent=4)
        return default
    with open(filename, "r") as f:
        return json.load(f)

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=4)

class SquadSignupView(discord.ui.View):
    def __init__(self, bot, message_id, op_id, multi=False):
        super().__init__(timeout=None)
        self.bot = bot
        self.message_id = message_id
        self.op_id = op_id
        self.multi = multi

    @discord.ui.button(label="✅ Yes", style=discord.ButtonStyle.success)
    async def yes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_signup(interaction, "yes")

    @discord.ui.button(label="🤔 Maybe", style=discord.ButtonStyle.secondary)
    async def maybe_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_signup(interaction, "maybe")

    @discord.ui.button(label="❌ No", style=discord.ButtonStyle.danger)
    async def no_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_signup(interaction, "no")

    @discord.ui.button(label="🔒 Close Signups", style=discord.ButtonStyle.danger)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.op_id:
            return await interaction.response.send_message("Only the OP can close signups.", ephemeral=True)

        data = load_json(DATA_FILE, {})
        if str(self.message_id) in data:
            data[str(self.message_id)]["closed"] = True
            save_json(DATA_FILE, data)

        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)
        await interaction.response.send_message("Signups closed.", ephemeral=True)

    async def update_signup(self, interaction: discord.Interaction, status):
        data = load_json(DATA_FILE, {})

        if str(self.message_id) not in data:
            return await interaction.response.send_message("Signup not found.", ephemeral=True)

        if data[str(self.message_id)]["closed"]:
            return await interaction.response.send_message("Signups are closed.", ephemeral=True)

        post_data = data[str(self.message_id)]

        if self.multi and status == "yes":
            squads = post_data["squads"]
            max_size = post_data["max_per_squad"]

            assigned = None
            for squad in squads:
                if interaction.user.id in squads[squad]:
                    assigned = squad
                    break

            if assigned:
                squads[assigned].remove(interaction.user.id)

            for squad in squads:
                if len(squads[squad]) < max_size:
                    squads[squad].append(interaction.user.id)
                    assigned = squad
                    break

            post_data["squads"] = squads
        else:
            post_data["signups"][str(interaction.user.id)] = status

        data[str(self.message_id)] = post_data
        save_json(DATA_FILE, data)

        embed = self.bot.get_cog("SquadUp").build_embed(post_data)
        await interaction.message.edit(embed=embed, view=self)
        await interaction.response.send_message(f"You selected {status}.", ephemeral=True)

class SquadUp(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = load_json(CONFIG_FILE, {"allowed_roles": []})
        self.data = load_json(DATA_FILE, {})

    def has_allowed_role(self, user):
        allowed_roles = self.config.get("allowed_roles", [])
        return any(role.id in allowed_roles for role in user.roles)

    def build_embed(self, post_data):
        embed = discord.Embed(title=post_data["title"], color=discord.Color.green())

        if post_data.get("multi"):
            squads = post_data["squads"]
            for squad, members in squads.items():
                names = [f"<@{uid}>" for uid in members]
                embed.add_field(name=f"{squad} ({len(members)}/{post_data['max_per_squad']})", value="\n".join(names) or "—", inline=True)
        else:
            yes = [f"<@{uid}>" for uid, s in post_data["signups"].items() if s == "yes"]
            maybe = [f"<@{uid}>" for uid, s in post_data["signups"].items() if s == "maybe"]
            no = [f"<@{uid}>" for uid, s in post_data["signups"].items() if s == "no"]

            embed.add_field(name=f"✅ Yes ({len(yes)})", value="\n".join(yes) or "—")
            embed.add_field(name=f"🤔 Maybe ({len(maybe)})", value="\n".join(maybe) or "—")
            embed.add_field(name=f"❌ No ({len(no)})", value="\n".join(no) or "—")

        if post_data.get("closed"):
            embed.set_footer(text="Signups closed.")
        return embed

    @app_commands.command(name="squadup", description="Create a simple one-squad signup")
    async def squadup(self, interaction: discord.Interaction, title: str):
        if not self.has_allowed_role(interaction.user):
            return await interaction.response.send_message("You are not allowed to use this command.", ephemeral=True)

        post_data = {
            "title": title,
            "signups": {},
            "multi": False,
            "closed": False
        }
        embed = self.build_embed(post_data)
        view = SquadSignupView(self.bot, None, interaction.user.id, multi=False)
        message = await interaction.channel.send(embed=embed, view=view)

        view.message_id = message.id
        self.data[str(message.id)] = post_data
        save_json(DATA_FILE, self.data)

    @app_commands.command(name="squadupmulti", description="Create multiple squad signups")
    async def squadupmulti(self, interaction: discord.Interaction, title: str, num_squads: int, players_per_squad: int = 6):
        if not self.has_allowed_role(interaction.user):
            return await interaction.response.send_message("You are not allowed to use this command.", ephemeral=True)

        squads = {NATO_SQUAD_NAMES[i]: [] for i in range(min(num_squads, len(NATO_SQUAD_NAMES)))}
        post_data = {
            "title": title,
            "squads": squads,
            "multi": True,
            "max_per_squad": players_per_squad,
            "closed": False
        }
        embed = self.build_embed(post_data)
        view = SquadSignupView(self.bot, None, interaction.user.id, multi=True)
        message = await interaction.channel.send(embed=embed, view=view)

        view.message_id = message.id
        self.data[str(message.id)] = post_data
        save_json(DATA_FILE, self.data)

async def setup(bot):
    await bot.add_cog(SquadUp(bot))
