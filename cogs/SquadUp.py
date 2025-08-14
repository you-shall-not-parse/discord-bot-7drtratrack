import discord
from discord import app_commands
from discord.ext import commands
import json
import os

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

class SquadButton(discord.ui.Button):
    def __init__(self, squad_name):
        super().__init__(label=f"Join {squad_name}", style=discord.ButtonStyle.primary)
        self.squad_name = squad_name

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        data = ensure_file_exists(POSTS_FILE, {})
        post = data.get(str(view.message_id))
        if not post or post.get("closed", False):
            await interaction.response.send_message("Signups are closed or not found.", ephemeral=True)
            return

        user_id = interaction.user.id
        squads = post["squads"]
        # Remove user from all squads first
        for sq in squads:
            if user_id in squads[sq]:
                squads[sq].remove(user_id)
        # Add user to selected squad if space is available
        if len(squads[self.squad_name]) < post["max_per_squad"]:
            squads[self.squad_name].append(user_id)
            await interaction.response.send_message(f"You joined {self.squad_name} squad!", ephemeral=True)
        else:
            await interaction.response.send_message(f"{self.squad_name} squad is full!", ephemeral=True)

        data[str(view.message_id)] = post
        save_json(POSTS_FILE, data)
        embed = view.bot.get_cog("SquadUp").build_embed(post)
        await interaction.message.edit(embed=embed, view=view)

class JoinButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Join", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        data = ensure_file_exists(POSTS_FILE, {})
        post = data.get(str(view.message_id))
        if not post or post.get("closed", False):
            await interaction.response.send_message("Signups are closed or not found.", ephemeral=True)
            return

        user_id = interaction.user.id
        # Remove from previous
        if user_id not in post["joined"]:
            post["joined"].append(user_id)
            await interaction.response.send_message("You joined the squad!", ephemeral=True)
        else:
            await interaction.response.send_message("You are already in the squad!", ephemeral=True)

        data[str(view.message_id)] = post
        save_json(POSTS_FILE, data)
        embed = view.bot.get_cog("SquadUp").build_embed(post)
        await interaction.message.edit(embed=embed, view=view)

class CloseButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🔒 Close Signups", style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        data = ensure_file_exists(POSTS_FILE, {})
        post = data.get(str(view.message_id))
        if not post:
            await interaction.response.send_message("Post not found.", ephemeral=True)
            return
        if interaction.user.id != post["op_id"]:
            await interaction.response.send_message("Only the OP can close signups.", ephemeral=True)
            return

        post["closed"] = True
        save_json(POSTS_FILE, data)
        for child in view.children:
            child.disabled = True
        await interaction.message.edit(view=view)
        await interaction.response.send_message("✅ Signups closed.", ephemeral=True)

class SquadSignupView(discord.ui.View):
    def __init__(self, bot, message_id, op_id, multi=False, squad_names=None):
        super().__init__(timeout=None)
        self.bot = bot
        self.message_id = message_id
        self.op_id = op_id
        self.multi = multi
        self.squad_names = squad_names or []

        if self.multi and self.squad_names:
            for squad in self.squad_names:
                self.add_item(SquadButton(squad))
        else:
            self.add_item(JoinButton())
        self.add_item(CloseButton())

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
            members = [f"<@{uid}>" for uid in post_data.get("joined", [])]
            embed.add_field(name=f"✅ Joined ({len(members)})", value="\n".join(members) or "—", inline=True)
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
            "joined": [],
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
        view = SquadSignupView(self.bot, None, interaction.user.id, multi=True, squad_names=squad_names)
        message = await interaction.channel.send(embed=embed, view=view)
        view.message_id = message.id

        self.posts_data[str(message.id)] = post_data
        save_json(POSTS_FILE, self.posts_data)
        await interaction.response.send_message("✅ Multi-squad post created.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(SquadUp(bot))
