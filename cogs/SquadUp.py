import discord
from discord import app_commands
from discord.ext import commands
import json
import os

# ---------- JSON Helpers ----------
DATA_FOLDER = "data"
POSTS_FILE = os.path.join(DATA_FOLDER, "squadup_posts.json")
CONFIG_FILE = os.path.join(DATA_FOLDER, "squadup_config.json")

NATO_SQUAD_NAMES = [
    "Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot",
    "Golf", "Hotel", "India", "Juliet", "Kilo", "Lima",
    "Mike", "November", "Oscar", "Papa", "Quebec", "Romeo",
    "Sierra", "Tango", "Uniform", "Victor", "Whiskey", "X-ray",
    "Yankee", "Zulu"
]

def ensure_file_exists(path, default_data):
    if not os.path.exists(DATA_FOLDER):
        os.makedirs(DATA_FOLDER)
    if not os.path.isfile(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default_data, f, indent=4)
        return default_data
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default_data, f, indent=4)
        return default_data

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

# ---------- Views & Buttons ----------
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
        data = ensure_file_exists(POSTS_FILE, {})
        if str(self.message_id) not in data:
            return await interaction.response.send_message("Post not found.", ephemeral=True)
        post = data[str(self.message_id)]
        if interaction.user.id != post["op_id"]:
            return await interaction.response.send_message("Only the OP can close signups.", ephemeral=True)

        post["closed"] = True
        save_json(POSTS_FILE, data)
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)
        await interaction.response.send_message("✅ Signups closed.", ephemeral=True)

    async def update_signup(self, interaction: discord.Interaction, status):
        data = ensure_file_exists(POSTS_FILE, {})
        if str(self.message_id) not in data:
            return await interaction.response.send_message("Post not found.", ephemeral=True)
        post = data[str(self.message_id)]
        if post.get("closed", False):
            return await interaction.response.send_message("Signups are closed.", ephemeral=True)

        user_id = interaction.user.id

        if post.get("multi"):
            squads = post["squads"]
            # Remove from other squads
            for sq in squads:
                if user_id in squads[sq]:
                    squads[sq].remove(user_id)
            # Assign to first squad with space
            for sq in squads:
                if len(squads[sq]) < post["max_per_squad"]:
                    squads[sq].append(user_id)
                    break
        else:
            # Remove from all previous
            for k in ["yes", "maybe", "no"]:
                if user_id in post.get(k, []):
                    post[k].remove(user_id)
            post.setdefault(status, []).append(user_id)

        data[str(self.message_id)] = post
        save_json(POSTS_FILE, data)

        embed = self.bot.get_cog("SquadUp").build_embed(post)
        await interaction.message.edit(embed=embed, view=self)
        await interaction.response.send_message(f"You selected **{status}**.", ephemeral=True)

# ---------- Cog ----------
class SquadUp(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.posts_data = ensure_file_exists(POSTS_FILE, {})
        self.config_data = ensure_file_exists(CONFIG_FILE, {"allowed_roles": ["Squad Leader", "Admin"], "default_squad_size": 6})

    def user_has_allowed_role(self, member):
        allowed_roles = self.config_data.get("allowed_roles", [])
        return any(role.name in allowed_roles for role in member.roles)

    def build_embed(self, post_data):
        embed = discord.Embed(title=post_data["title"], color=discord.Color.green())
        if post_data.get("multi"):
            for squad, members in post_data["squads"].items():
                names = [f"<@{uid}>" for uid in members]
                embed.add_field(name=f"{squad} ({len(members)}/{post_data['max_per_squad']})", value="\n".join(names) or "—", inline=True)
        else:
            for status in ["yes", "maybe", "no"]:
                members = [f"<@{uid}>" for uid in post_data.get(status, [])]
                emoji = "✅" if status=="yes" else "🤔" if status=="maybe" else "❌"
                embed.add_field(name=f"{emoji} {status.capitalize()} ({len(members)})", value="\n".join(members) or "—", inline=True)
        if post_data.get("closed"):
            embed.set_footer(text="Signups closed.")
        return embed

    @app_commands.command(name="squadup", description="Create a simple one-squad signup")
    async def squadup(self, interaction: discord.Interaction, title: str):
        if not self.user_has_allowed_role(interaction.user):
            return await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)

        post_data = {
            "title": title,
            "op_id": interaction.user.id,
            "multi": False,
            "yes": [],
            "maybe": [],
            "no": [],
            "closed": False
        }
        embed = self.build_embed(post_data)
        view = SquadSignupView(self.bot, None, interaction.user.id, multi=False)
        message = await interaction.channel.send(embed=embed, view=view)
        view.message_id = message.id

        self.posts_data[str(message.id)] = post_data
        save_json(POSTS_FILE, self.posts_data)
        await interaction.response.send_message("✅ SquadUp post created.", ephemeral=True)

    @app_commands.command(name="squadupmulti", description="Create multi-squad signup")
    async def squadupmulti(self, interaction: discord.Interaction, title: str, num_squads: int, players_per_squad: int = 6):
        if not self.user_has_allowed_role(interaction.user):
            return await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)

        squad_names = NATO_SQUAD_NAMES[:num_squads]
        squads = {name: [] for name in squad_names}

        post_data = {
            "title": title,
            "op_id": interaction.user.id,
            "multi": True,
            "squads": squads,
            "max_per_squad": players_per_squad,
            "closed": False
        }

        embed = self.build_embed(post_data)
        view = SquadSignupView(self.bot, None, interaction.user.id, multi=True)
        message = await interaction.channel.send(embed=embed, view=view)
        view.message_id = message.id

        self.posts_data[str(message.id)] = post_data
        save_json(POSTS_FILE, self.posts_data)
        await interaction.response.send_message("✅ Multi-squad post created.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(SquadUp(bot))
