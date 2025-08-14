import discord
from discord.ext import commands
from discord import app_commands
import json
import os

DATA_FILE = "squadup_data.json"

# NATO squad names
NATO_NAMES = [
    "Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel",
    "India", "Juliett", "Kilo", "Lima", "Mike", "November", "Oscar", "Papa",
    "Quebec", "Romeo", "Sierra", "Tango", "Uniform", "Victor", "Whiskey",
    "X-ray", "Yankee", "Zulu"
]

# Load or init JSON data
def load_data():
    if not os.path.exists(DATA_FILE):
        return {"posts": {}, "allowed_roles": []}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

class SquadButtons(discord.ui.View):
    def __init__(self, post_id, is_multi=False, squads=None, max_per_squad=6, author_id=None):
        super().__init__(timeout=None)
        self.post_id = post_id
        self.is_multi = is_multi
        self.squads = squads
        self.max_per_squad = max_per_squad
        self.author_id = author_id

        if is_multi:
            for squad in squads:
                self.add_item(SquadJoinButton(label=squad, squad_name=squad, post_id=post_id, max_per_squad=max_per_squad))
        else:
            self.add_item(SingleJoinButton(label="✅ Yes", style=discord.ButtonStyle.success, post_id=post_id))
            self.add_item(SingleJoinButton(label="🤔 Maybe", style=discord.ButtonStyle.secondary, post_id=post_id))
            self.add_item(SingleJoinButton(label="❌ No", style=discord.ButtonStyle.danger, post_id=post_id))

        self.add_item(CloseButton(post_id=post_id, author_id=author_id))

class SingleJoinButton(discord.ui.Button):
    def __init__(self, label, style, post_id):
        super().__init__(label=label, style=style)
        self.post_id = post_id

    async def callback(self, interaction: discord.Interaction):
        data = load_data()
        post = data["posts"].get(str(self.post_id))
        if not post or post.get("closed"):
            return await interaction.response.send_message("❌ Sign-ups are closed.", ephemeral=True)

        choice = self.label
        user_id = str(interaction.user.id)

        # Remove from other lists
        for key in ["✅ Yes", "🤔 Maybe", "❌ No"]:
            if user_id in post.get(key, []):
                post[key].remove(user_id)

        # Add to chosen list
        post.setdefault(choice, []).append(user_id)

        save_data(data)
        await interaction.response.edit_message(embed=make_embed(post), view=SquadButtons(
            post_id=self.post_id,
            author_id=post["author_id"]
        ))

class SquadJoinButton(discord.ui.Button):
    def __init__(self, label, squad_name, post_id, max_per_squad):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.squad_name = squad_name
        self.post_id = post_id
        self.max_per_squad = max_per_squad

    async def callback(self, interaction: discord.Interaction):
        data = load_data()
        post = data["posts"].get(str(self.post_id))
        if not post or post.get("closed"):
            return await interaction.response.send_message("❌ Sign-ups are closed.", ephemeral=True)

        user_id = str(interaction.user.id)

        # Remove from other squads
        for squad in post["squads"]:
            if user_id in post["squads"][squad]:
                post["squads"][squad].remove(user_id)

        # Add to chosen squad if not full
        if len(post["squads"][self.squad_name]) < self.max_per_squad:
            post["squads"][self.squad_name].append(user_id)
        else:
            return await interaction.response.send_message(f"⚠️ {self.squad_name} is full!", ephemeral=True)

        save_data(data)
        await interaction.response.edit_message(embed=make_embed(post), view=SquadButtons(
            post_id=self.post_id,
            is_multi=True,
            squads=list(post["squads"].keys()),
            max_per_squad=self.max_per_squad,
            author_id=post["author_id"]
        ))

class CloseButton(discord.ui.Button):
    def __init__(self, post_id, author_id):
        super().__init__(label="Close Sign-Ups", style=discord.ButtonStyle.danger)
        self.post_id = post_id
        self.author_id = author_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ Only the post creator can close sign-ups.", ephemeral=True)

        data = load_data()
        post = data["posts"].get(str(self.post_id))
        if not post:
            return await interaction.response.send_message("❌ Post not found.", ephemeral=True)

        post["closed"] = True
        save_data(data)

        await interaction.response.edit_message(embed=make_embed(post, closed=True), view=None)
        await interaction.followup.send("✅ Sign-ups closed.", ephemeral=True)

def make_embed(post, closed=False):
    embed = discord.Embed(title=post["title"], color=discord.Color.blue())
    if closed:
        embed.description = "🚪 **Sign-ups closed**"
    else:
        embed.description = "React using the buttons below to join."

    if "squads" in post:
        for squad, members in post["squads"].items():
            names = [f"<@{m}>" for m in members]
            embed.add_field(name=f"{squad} ({len(members)})", value="\n".join(names) if names else "-", inline=True)
    else:
        for status in ["✅ Yes", "🤔 Maybe", "❌ No"]:
            members = post.get(status, [])
            names = [f"<@{m}>" for m in members]
            embed.add_field(name=f"{status} ({len(members)})", value="\n".join(names) if names else "-", inline=True)

    return embed

class SquadUp(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def has_allowed_role(self, interaction):
        data = load_data()
        allowed_roles = data.get("allowed_roles", [])
        return any(role.id in allowed_roles for role in interaction.user.roles)

    @app_commands.command(name="squadup", description="Create a simple squad signup post.")
    async def squadup(self, interaction: discord.Interaction, title: str):
        if not self.has_allowed_role(interaction):
            return await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)

        post_id = interaction.id
        data = load_data()
        data["posts"][str(post_id)] = {
            "title": title,
            "author_id": interaction.user.id,
            "✅ Yes": [],
            "🤔 Maybe": [],
            "❌ No": [],
            "closed": False
        }
        save_data(data)

        embed = make_embed(data["posts"][str(post_id)])
        view = SquadButtons(post_id=post_id, author_id=interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="squadupmulti", description="Create a multi-squad signup post.")
    async def squadupmulti(self, interaction: discord.Interaction, title: str, num_squads: int, max_per_squad: int = 6):
        if not self.has_allowed_role(interaction):
            return await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)

        if num_squads < 1 or num_squads > len(NATO_NAMES):
            return await interaction.response.send_message(f"❌ Number of squads must be 1-{len(NATO_NAMES)}.", ephemeral=True)

        post_id = interaction.id
        squads = {NATO_NAMES[i]: [] for i in range(num_squads)}
        data = load_data()
        data["posts"][str(post_id)] = {
            "title": title,
            "author_id": interaction.user.id,
            "squads": squads,
            "max_per_squad": max_per_squad,
            "closed": False
        }
        save_data(data)

        embed = make_embed(data["posts"][str(post_id)])
        view = SquadButtons(post_id=post_id, is_multi=True, squads=list(squads.keys()), max_per_squad=max_per_squad, author_id=interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(SquadUp(bot))
