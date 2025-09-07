import discord
from discord import app_commands
from discord.ext import commands
import json
import os
from typing import Optional
import io

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

# Creative base names for "Any Size" tanks. We append an index (e.g., "Big Boi 1", "Zoomer 3").
ANYSIZE_CREATIVE_NAMES = [
    "Big Boi", "Keith", "Big Chonker", "Autism Box", "Box of Mysteries", "Fuck Box",
    "Royston", "Badger", "Bandit", "Maverick", "Kevin", "Rogue",
    "Vanguard", "Warthog", "Coyote", "Mickey the Sticky", "Brawler", "Ghost",
    "Rascal", "Goblin", "Mongoose", "Thumper", "Spitfire", "Bulldog", "Viper"
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

def is_role_based(post: dict) -> bool:
    return post.get("role_based", False) or (
        post.get("multi") and post.get("squads") and isinstance(next(iter(post["squads"].values())), dict)
    )

class JoinButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Join", style=discord.ButtonStyle.success, custom_id="squadup_join")

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        data = ensure_file_exists(POSTS_FILE, {})
        post = data.get(str(view.message_id))
        if not post or post.get("closed", False):
            await interaction.response.send_message("Signups are closed or not found.", ephemeral=True)
            return

        user_id = interaction.user.id
        for k in ["yes", "maybe"]:
            if user_id in post[k]:
                post[k].remove(user_id)
        if user_id not in post["yes"]:
            post["yes"].append(user_id)
            await interaction.response.send_message("You joined the squad!", ephemeral=True)
        else:
            await interaction.response.send_message("You are already joined!", ephemeral=True)

        data[str(view.message_id)] = post
        save_json(POSTS_FILE, data)
        embed = view.bot.get_cog("SquadUp").build_embed(post)
        await interaction.message.edit(embed=embed, view=view)

class MaybeButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Maybe", style=discord.ButtonStyle.secondary, custom_id="squadup_maybe")

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        data = ensure_file_exists(POSTS_FILE, {})
        post = data.get(str(view.message_id))
        if not post or post.get("closed", False):
            await interaction.response.send_message("Signups are closed or not found.", ephemeral=True)
            return

        user_id = interaction.user.id
        for k in ["yes", "maybe"]:
            if user_id in post[k]:
                post[k].remove(user_id)
        if user_id not in post["maybe"]:
            post["maybe"].append(user_id)
            await interaction.response.send_message("You marked yourself as maybe!", ephemeral=True)
        else:
            await interaction.response.send_message("You are already marked as maybe!", ephemeral=True)

        data[str(view.message_id)] = post
        save_json(POSTS_FILE, data)
        embed = view.bot.get_cog("SquadUp").build_embed(post)
        await interaction.message.edit(embed=embed, view=view)

class RemoveMeButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Remove Me", style=discord.ButtonStyle.danger, custom_id="squadup_remove")

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        data = ensure_file_exists(POSTS_FILE, {})
        post = data.get(str(view.message_id))
        if not post or post.get("closed", False):
            await interaction.response.send_message("Signups are closed or not found.", ephemeral=True)
            return

        user_id = interaction.user.id

        if post.get("multi", False):
            if is_role_based(post):
                # Remove user from any role in any tank
                for sq in post.get("squads", {}):
                    roles = post["squads"][sq]
                    for role_name, uid in list(roles.items()):
                        if uid == user_id:
                            roles[role_name] = None
            else:
                for sq in post.get("squads", {}):
                    if user_id in post["squads"][sq]:
                        post["squads"][sq].remove(user_id)
        else:
            for k in ["yes", "maybe"]:
                if user_id in post[k]:
                    post[k].remove(user_id)

        data[str(view.message_id)] = post
        save_json(POSTS_FILE, data)
        embed = view.bot.get_cog("SquadUp").build_embed(post)
        await interaction.message.edit(embed=embed, view=view)
        await interaction.response.send_message("You have been removed from the signup.", ephemeral=True)

class RoleSelect(discord.ui.Select):
    def __init__(self, message_id: int, squad_name: str):
        options = [
            discord.SelectOption(label="Tank Commander"),
            discord.SelectOption(label="Gunner"),
            discord.SelectOption(label="Driver"),
        ]
        super().__init__(placeholder=f"Select role for {squad_name}", min_values=1, max_values=1, options=options, custom_id=f"squadup_role_select_{squad_name}")
        self.message_id = message_id
        self.squad_name = squad_name

    async def callback(self, interaction: discord.Interaction):
        chosen_role = self.values[0]
        data = ensure_file_exists(POSTS_FILE, {})
        post = data.get(str(self.message_id))
        if not post:
            await interaction.response.send_message("Post not found.", ephemeral=True)
            return
        if post.get("closed", False):
            await interaction.response.send_message("Signups are closed.", ephemeral=True)
            return
        if not is_role_based(post):
            await interaction.response.send_message("This post does not support role selection.", ephemeral=True)
            return

        user_id = interaction.user.id
        squads = post["squads"]

        # Remove user from any role in any squad
        for sq_name, roles in squads.items():
            for role_name, uid in list(roles.items()):
                if uid == user_id:
                    roles[role_name] = None

        # Try to claim the chosen role in the selected squad
        target_roles = squads.get(self.squad_name)
        if target_roles is None:
            await interaction.response.send_message("Squad not found.", ephemeral=True)
            return

        if target_roles.get(chosen_role) in (None, user_id):
            target_roles[chosen_role] = user_id
            data[str(self.message_id)] = post
            save_json(POSTS_FILE, data)

            # Update the original signup message's embed
            cog = interaction.client.get_cog("SquadUp")
            embed = cog.build_embed(post) if cog else None
            try:
                original_msg = await interaction.channel.fetch_message(self.message_id)
                if embed:
                    await original_msg.edit(embed=embed)
            except Exception:
                pass

            await interaction.response.send_message(f"You are now {chosen_role} in {self.squad_name}.", ephemeral=True)
        else:
            await interaction.response.send_message(f"{chosen_role} in {self.squad_name} is already taken.", ephemeral=True)

class RoleSelectView(discord.ui.View):
    def __init__(self, message_id: int, squad_name: str):
        super().__init__(timeout=60)
        self.message_id = message_id
        self.squad_name = squad_name
        self.add_item(RoleSelect(message_id, squad_name))

class SquadButton(discord.ui.Button):
    def __init__(self, squad_name):
        super().__init__(label=f"Join {squad_name}", style=discord.ButtonStyle.primary, custom_id=f"squadup_squad_{squad_name}")
        self.squad_name = squad_name

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        data = ensure_file_exists(POSTS_FILE, {})
        post = data.get(str(view.message_id))
        if not post or post.get("closed", False):
            await interaction.response.send_message("Signups are closed or not found.", ephemeral=True)
            return

        # If this is a role-based crewup, open role selection instead of direct join
        if is_role_based(post):
            sel_view = RoleSelectView(view.message_id, self.squad_name)
            await interaction.response.send_message(f"Choose your role in {self.squad_name}:", view=sel_view, ephemeral=True)
            return

        user_id = interaction.user.id
        squads = post["squads"]
        for sq in squads:
            if user_id in squads[sq]:
                squads[sq].remove(user_id)
        if len(squads[self.squad_name]) < post["max_per_squad"]:
            squads[self.squad_name].append(user_id)
            await interaction.response.send_message(f"You joined {self.squad_name} squad!", ephemeral=True)
        else:
            await interaction.response.send_message(f"{self.squad_name} squad is full!", ephemeral=True)

        data[str(view.message_id)] = post
        save_json(POSTS_FILE, data)
        embed = view.bot.get_cog("SquadUp").build_embed(post)
        await interaction.message.edit(embed=embed, view=view)

class CloseButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🔒 Close Signups", style=discord.ButtonStyle.danger, custom_id="squadup_close")

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

class AddMoreSquadsButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Add More Squads", style=discord.ButtonStyle.primary, custom_id="squadup_add_more_squads")
    
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        data = ensure_file_exists(POSTS_FILE, {})
        post = data.get(str(view.message_id))
        
        if not post:
            await interaction.response.send_message("Post not found.", ephemeral=True)
            return
            
        if interaction.user.id != post["op_id"]:
            await interaction.response.send_message("Only the organizer can add more squads.", ephemeral=True)
            return
            
        if post.get("closed", False):
            await interaction.response.send_message("Signups are closed.", ephemeral=True)
            return
            
        # Create modal based on post type
        if is_role_based(post):
            modal = AddMoreTanksModal(view.message_id)
        else:
            modal = AddMoreSquadsModal(view.message_id)
            
        await interaction.response.send_modal(modal)

class AddMoreSquadsModal(discord.ui.Modal, title="Add More Squads"):
    num_squads = discord.ui.TextInput(
        label="Number of Squads to Add",
        placeholder="Enter a number (1-10)",
        required=True,
        default="1",
        min_length=1,
        max_length=2
    )
    
    players_per_squad = discord.ui.TextInput(
        label="Players Per Squad",
        placeholder="Enter the number of slots per squad",
        required=True,
        default="6"
    )
    
    def __init__(self, message_id):
        super().__init__()
        self.message_id = message_id
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            num_to_add = int(self.num_squads.value)
            players_per = int(self.players_per_squad.value)
            
            if num_to_add < 1 or num_to_add > 10:
                return await interaction.response.send_message("Please enter a number between 1 and 10.", ephemeral=True)
                
            if players_per < 1:
                return await interaction.response.send_message("Players per squad must be at least 1.", ephemeral=True)
        except ValueError:
            return await interaction.response.send_message("Please enter valid numbers.", ephemeral=True)
            
        data = ensure_file_exists(POSTS_FILE, {})
        post = data.get(str(self.message_id))
        
        if not post:
            return await interaction.response.send_message("Post not found.", ephemeral=True)
            
        # Get current squads and find unused NATO squad names
        current_squads = list(post["squads"].keys())
        available_names = [name for name in NATO_SQUAD_NAMES if name not in current_squads]
        
        if len(available_names) < num_to_add:
            return await interaction.response.send_message(f"Cannot add {num_to_add} squads. Only {len(available_names)} names available.", ephemeral=True)
            
        # Add new squads
        for i in range(num_to_add):
            post["squads"][available_names[i]] = []
            
        post["max_per_squad"] = players_per
        data[str(self.message_id)] = post
        save_json(POSTS_FILE, data)
        
        # Update the message
        cog = interaction.client.get_cog("SquadUp")
        embed = cog.build_embed(post)
        
        # Create a new view with updated squad buttons
        new_view = SquadSignupView(
            interaction.client, 
            int(self.message_id), 
            post["op_id"], 
            multi=True, 
            squad_names=list(post["squads"].keys())
        )
        new_view.message_id = int(self.message_id)
        
        try:
            channel = interaction.channel
            message = await channel.fetch_message(int(self.message_id))
            await message.edit(embed=embed, view=new_view)
            await interaction.response.send_message(f"Added {num_to_add} new squads!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error updating message: {str(e)}", ephemeral=True)

class AddMoreTanksModal(discord.ui.Modal, title="Add More Tanks"):
    anysize = discord.ui.TextInput(
        label="Any Size Tanks",
        placeholder="Enter number to add",
        required=False,
        default="0"
    )
    
    lights = discord.ui.TextInput(
        label="Light Tanks",
        placeholder="Enter number to add",
        required=False,
        default="0"
    )
    
    mediums = discord.ui.TextInput(
        label="Medium Tanks",
        placeholder="Enter number to add",
        required=False,
        default="0"
    )
    
    heavies = discord.ui.TextInput(
        label="Heavy Tanks",
        placeholder="Enter number to add",
        required=False,
        default="0"
    )
    
    def __init__(self, message_id):
        super().__init__()
        self.message_id = message_id
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            anysize_count = int(self.anysize.value or "0")
            lights_count = int(self.lights.value or "0")
            mediums_count = int(self.mediums.value or "0")
            heavies_count = int(self.heavies.value or "0")
            
            total_to_add = anysize_count + lights_count + mediums_count + heavies_count
            
            if total_to_add < 1:
                return await interaction.response.send_message("Please add at least one tank.", ephemeral=True)
                
        except ValueError:
            return await interaction.response.send_message("Please enter valid numbers.", ephemeral=True)
            
        data = ensure_file_exists(POSTS_FILE, {})
        post = data.get(str(self.message_id))
        
        if not post:
            return await interaction.response.send_message("Post not found.", ephemeral=True)
            
        # Get current squads
        current_squads = list(post["squads"].keys())
        
        # Calculate new squad names
        new_squad_names = []
        
        # Count existing types to continue numbering
        existing_anysize = sum(1 for name in current_squads if any(creative in name for creative in ANYSIZE_CREATIVE_NAMES))
        existing_lights = sum(1 for name in current_squads if name.startswith("Light "))
        existing_mediums = sum(1 for name in current_squads if name.startswith("Medium "))
        existing_heavies = sum(1 for name in current_squads if name.startswith("Heavy "))
        
        # Add new Any Size tanks
        for idx in range(anysize_count):
            base_idx = existing_anysize + idx
            base = ANYSIZE_CREATIVE_NAMES[base_idx % len(ANYSIZE_CREATIVE_NAMES)]
            number = (base_idx // len(ANYSIZE_CREATIVE_NAMES)) + 1
            new_squad_names.append(f"{base} {number}")
            
        # Add new Light tanks
        for i in range(1, lights_count + 1):
            new_squad_names.append(f"Light {existing_lights + i}")
            
        # Add new Medium tanks
        for i in range(1, mediums_count + 1):
            new_squad_names.append(f"Medium {existing_mediums + i}")
            
        # Add new Heavy tanks
        for i in range(1, heavies_count + 1):
            new_squad_names.append(f"Heavy {existing_heavies + i}")
        
        # Check the total number of tanks after adding
        if len(current_squads) + len(new_squad_names) > 23:
            return await interaction.response.send_message(
                f"Cannot add {total_to_add} tanks. Maximum of 23 total tanks allowed (you have {len(current_squads)} already).", 
                ephemeral=True
            )
            
        # Add new tanks to the post
        for name in new_squad_names:
            post["squads"][name] = {"Tank Commander": None, "Gunner": None, "Driver": None}
            
        data[str(self.message_id)] = post
        save_json(POSTS_FILE, data)
        
        # Update the message
        cog = interaction.client.get_cog("SquadUp")
        embed = cog.build_embed(post)
        
        # Create a new view with updated squad buttons
        new_view = SquadSignupView(
            interaction.client, 
            int(self.message_id), 
            post["op_id"], 
            multi=True, 
            squad_names=list(post["squads"].keys())
        )
        new_view.message_id = int(self.message_id)
        
        try:
            channel = interaction.channel
            message = await channel.fetch_message(int(self.message_id))
            await message.edit(embed=embed, view=new_view)
            await interaction.response.send_message(f"Added {total_to_add} new tanks!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error updating message: {str(e)}", ephemeral=True)

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
            self.add_item(RemoveMeButton())
        else:
            self.add_item(JoinButton())
            self.add_item(MaybeButton())
            self.add_item(RemoveMeButton())
        self.add_item(CloseButton())
        
        # Add the "Add More Squads" button that only the OP can use
        if multi:
            self.add_item(AddMoreSquadsButton())

class SquadUp(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.posts_data = ensure_file_exists(POSTS_FILE, {})
        self.config_data = ensure_file_exists(CONFIG_FILE, {"allowed_roles": ["Squad Leader", "Admin"], "default_squad_size": 6})
        bot.loop.create_task(self._register_persistent_views())

    async def _register_persistent_views(self):
        await self.bot.wait_until_ready()
        data = ensure_file_exists(POSTS_FILE, {})
        for msg_id, post in data.items():
            if not post.get("closed", False):
                if post.get("multi"):
                    squad_names = list(post["squads"].keys())
                    view = SquadSignupView(self.bot, int(msg_id), post["op_id"], multi=True, squad_names=squad_names)
                else:
                    view = SquadSignupView(self.bot, int(msg_id), post["op_id"], multi=False)
                view.message_id = int(msg_id)
                self.bot.add_view(view, message_id=int(msg_id))

    def user_has_allowed_role(self, member):
        allowed_roles = self.config_data.get("allowed_roles", [])
        return any(role.name in allowed_roles for role in member.roles)

    def build_embed(self, post_data):
        embed = discord.Embed(
            title=post_data["title"],
            description=(post_data.get("description") or None),
            color=discord.Color.green()
        )
        
        # Add organizer field
        organizer_id = post_data.get("op_id")
        if organizer_id:
            embed.add_field(
                name="Organizer",
                value=f"<@{organizer_id}>",
                inline=False
            )
            
        if post_data.get("multi"):
            # multi-mode: could be list-based or role-based
            if is_role_based(post_data):
                for squad, roles in post_data["squads"].items():
                    role_lines = []
                    filled = 0
                    for r in ["Tank Commander", "Gunner", "Driver"]:
                        uid = roles.get(r)
                        if uid:
                            filled += 1
                        role_lines.append(f"{r}: {f'<@{uid}>' if uid else '—'}")
                    embed.add_field(
                        name=f"{squad} ({filled}/3)",
                        value="\n".join(role_lines),
                        inline=True
                    )
            else:
                for squad, members in post_data["squads"].items():
                    names = [f"<@{uid}>" for uid in members]
                    embed.add_field(name=f"{squad} ({len(members)}/{post_data['max_per_squad']})", value="\n".join(names) or "—", inline=True)
        else:
            for status in ["yes", "maybe"]:
                members = [f"<@{uid}>" for uid in post_data.get(status, [])]
                emoji = "✅" if status=="yes" else "🤔"
                embed.add_field(name=f"{emoji} {status.capitalize()} ({len(members)})", value="\n".join(members) or "—", inline=True)
                
        # Set image if there is one
        if post_data.get("image_url"):
            embed.set_image(url=post_data["image_url"])
            
        if post_data.get("closed"):
            embed.set_footer(text="Signups closed.")
        return embed

    @app_commands.command(name="squadup", description="Create a simple one-squad signup")
    @app_commands.describe(
        title="The title of your squad up",
        image="Optional image to attach to the squad up post"
    )
    async def squadup(self, interaction: discord.Interaction, title: str, image: Optional[discord.Attachment] = None):
        if not self.user_has_allowed_role(interaction.user):
            return await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)

        post_data = {
            "title": title,
            "op_id": interaction.user.id,
            "multi": False,
            "yes": [],
            "maybe": [],
            "closed": False
        }
        
        embed = self.build_embed(post_data)
        view = SquadSignupView(self.bot, None, interaction.user.id, multi=False)
        
        # Handle image attachment if provided
        if image:
            post_data["image_url"] = f"attachment://{image.filename}"
            file = await image.to_file()
            message = await interaction.channel.send(embed=embed, file=file, view=view)
        else:
            message = await interaction.channel.send(embed=embed, view=view)
            
        view.message_id = message.id

        self.posts_data[str(message.id)] = post_data
        save_json(POSTS_FILE, self.posts_data)
        await interaction.response.send_message("✅ SquadUp post created.", ephemeral=True)

    @app_commands.command(name="squadupmulti", description="Create multi-squad signup")
    @app_commands.describe(
        title="The title of your multi-squad up",
        number_of_squads="Number of squads to create",
        players_per_squad="Number of players per squad",
        details="Optional text shown under the title",
        image="Optional image to attach to the squad up post"
    )
    async def squadupmulti(self, 
                          interaction: discord.Interaction, 
                          title: str, 
                          number_of_squads: int, 
                          players_per_squad: int = 6, 
                          details: Optional[str] = None,
                          image: Optional[discord.Attachment] = None):
        if not self.user_has_allowed_role(interaction.user):
            return await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)

        squad_names = NATO_SQUAD_NAMES[:number_of_squads]
        squads = {name: [] for name in squad_names}

        post_data = {
            "title": title,
            "op_id": interaction.user.id,
            "multi": True,
            "squads": squads,
            "max_per_squad": players_per_squad,
            "closed": False,
            "description": details or ""
        }

        embed = self.build_embed(post_data)
        view = SquadSignupView(self.bot, None, interaction.user.id, multi=True, squad_names=squad_names)
        
        # Handle image attachment if provided
        if image:
            post_data["image_url"] = f"attachment://{image.filename}"
            file = await image.to_file()
            message = await interaction.channel.send(embed=embed, file=file, view=view)
        else:
            message = await interaction.channel.send(embed=embed, view=view)
            
        view.message_id = message.id

        self.posts_data[str(message.id)] = post_data
        save_json(POSTS_FILE, self.posts_data)
        await interaction.response.send_message("✅ Multi-squad post created.", ephemeral=True)

    @app_commands.command(name="crewup", description="Create tank crew signups where each tank has TC, Gunner, and Driver roles")
    @app_commands.describe(
        title="The title of your crew up",
        anysize="Number of tanks of any size (each has 3 slots)",
        lights="Number of light tanks (each has 3 slots)",
        mediums="Number of medium tanks (each has 3 slots)",
        heavies="Number of heavy tanks (each has 3 slots)",
        details="Optional text shown under the title",
        image="Optional image to attach to the crew up post"
    )
    async def crewup(
        self,
        interaction: discord.Interaction,
        title: str,
        anysize: app_commands.Range[int, 0, 23] = 0,
        lights: app_commands.Range[int, 0, 23] = 0,
        mediums: app_commands.Range[int, 0, 23] = 0,
        heavies: app_commands.Range[int, 0, 23] = 0,
        details: Optional[str] = None,
        image: Optional[discord.Attachment] = None
    ):
        if not self.user_has_allowed_role(interaction.user):
            return await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)

        total_squads = int(anysize) + int(lights) + int(mediums) + int(heavies)
        if total_squads == 0:
            return await interaction.response.send_message("Please specify at least one tank (anysize, light, medium, or heavy).", ephemeral=True)

        # Discord allows at most 25 components per message. We use +2 for Remove/Close buttons.
        if total_squads > 23:
            return await interaction.response.send_message("Too many tanks. Please keep the total number of tanks at 23 or fewer.", ephemeral=True)

        squad_names = []
        # Use creative names for "Any Size" tanks, cycling through the list and incrementing an index as needed.
        for idx in range(anysize):
            base = ANYSIZE_CREATIVE_NAMES[idx % len(ANYSIZE_CREATIVE_NAMES)]
            number = (idx // len(ANYSIZE_CREATIVE_NAMES)) + 1
            squad_names.append(f"{base} {number}")

        for i in range(1, lights + 1):
            squad_names.append(f"Light {i}")
        for i in range(1, mediums + 1):
            squad_names.append(f"Medium {i}")
        for i in range(1, heavies + 1):
            squad_names.append(f"Heavy {i}")

        # Initialize role-based squads
        squads = {name: {"Tank Commander": None, "Gunner": None, "Driver": None} for name in squad_names}

        post_data = {
            "title": title,
            "op_id": interaction.user.id,
            "multi": True,
            "squads": squads,  # role-based structure
            "max_per_squad": 3,  # still useful for display/back-compat
            "closed": False,
            "description": details or "",
            "role_based": True
        }

        embed = self.build_embed(post_data)
        view = SquadSignupView(self.bot, None, interaction.user.id, multi=True, squad_names=squad_names)
        
        # Handle image attachment if provided
        if image:
            post_data["image_url"] = f"attachment://{image.filename}"
            file = await image.to_file()
            message = await interaction.channel.send(embed=embed, file=file, view=view)
        else:
            message = await interaction.channel.send(embed=embed, view=view)
            
        view.message_id = message.id

        self.posts_data[str(message.id)] = post_data
        save_json(POSTS_FILE, self.posts_data)
        await interaction.response.send_message("✅ CrewUp post created.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(SquadUp(bot))
