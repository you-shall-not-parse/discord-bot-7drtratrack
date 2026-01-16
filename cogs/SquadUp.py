import discord
from discord import app_commands
from discord.ext import commands
import json
import os
from typing import Optional, Dict, List, Union
import asyncio
import time

from data_paths import data_path

DATA_FOLDER = data_path()
POSTS_FILE = data_path("squadup_posts.json")
CONFIG_FILE = data_path("squadup_config.json")

# Cache to reduce file I/O
POST_CACHE = {}
CONFIG_CACHE = None
SAVE_INTERVAL = 60  # seconds between writes to disk
last_save_time = 0
dirty_cache = False  # Flag to track if cache has unsaved changes

NATO_SQUAD_NAMES = [
    "Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot",
    "Golf", "Hotel", "India", "Juliet", "Kilo", "Lima",
    "Mike", "November", "Oscar", "Papa", "Quebec", "Romeo",
    "Sierra", "Tango", "Uniform", "Victor", "Whiskey", "X-ray",
    "Yankee", "Zulu"
]

# Creative base names for "Any Size" tanks. We append an index (e.g., "Big Boi 1", "Zoomer 3").
ANYSIZE_CREATIVE_NAMES = [
    "Anvil", "The Flying Scotsman", "Big Chonker", "Autism Box", "Box of Mysteries", "Lord Crump III",
    "Royston", "Badger", "Coleslaw", "Machete", "Kevin", "Rogue",
    "Vanguard", "Warthog", "Coyote", "Mickey the Sticky", "Brawler", "Gauntlet",
    "Rascal", "Goblin", "Mongoose", "Thumper", "Spitfire", "Bulldog", "Viper"
]

# View cache to reduce recreation of views
VIEW_CACHE = {}

def ensure_file_exists(path, default_data):
    """Initialize files and load into cache if needed"""
    global CONFIG_CACHE
    
    if path == CONFIG_FILE and CONFIG_CACHE is not None:
        return CONFIG_CACHE
        
    if not os.path.exists(DATA_FOLDER):
        os.makedirs(DATA_FOLDER)
        
    if not os.path.isfile(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default_data, f, indent=4)
        
        if path == CONFIG_FILE:
            CONFIG_CACHE = default_data
        return default_data
        
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if path == CONFIG_FILE:
                CONFIG_CACHE = data
            return data
    except (json.JSONDecodeError, FileNotFoundError):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default_data, f, indent=4)
        
        if path == CONFIG_FILE:
            CONFIG_CACHE = default_data
        return default_data

def get_post_data(message_id: str) -> dict:
    """Get post data from cache or load from file"""
    global POST_CACHE
    
    message_id = str(message_id)  # Ensure string format
    
    if message_id in POST_CACHE:
        return POST_CACHE[message_id]
        
    # Load all posts into cache if not already done
    if not POST_CACHE:
        POST_CACHE = ensure_file_exists(POSTS_FILE, {})
    
    return POST_CACHE.get(message_id, None)

def update_post_data(message_id: str, post_data: dict):
    """Update post in cache and schedule save to disk"""
    global POST_CACHE, dirty_cache
    
    message_id = str(message_id)  # Ensure string format
    POST_CACHE[message_id] = post_data
    dirty_cache = True  # Mark cache as needing save

def check_and_save_posts():
    """Check if posts need saving and save if necessary"""
    global last_save_time, dirty_cache
    
    current_time = time.time()
    if dirty_cache and current_time - last_save_time > SAVE_INTERVAL:
        save_all_posts()
        last_save_time = current_time
        dirty_cache = False

def save_all_posts():
    """Force save all cached posts to disk"""
    global POST_CACHE
    
    if not POST_CACHE:
        return  # Nothing to save
    
    with open(POSTS_FILE, "w", encoding="utf-8") as f:
        json.dump(POST_CACHE, f, indent=4)

def is_role_based(post: dict) -> bool:
    return post.get("role_based", False) or (
        post.get("multi") and post.get("squads") and isinstance(next(iter(post["squads"].values())), dict)
    )

def get_or_create_view(bot, message_id, op_id, multi=False, squad_names=None, post_data=None):
    """Get cached view or create a new one"""
    cache_key = f"{message_id}"
    
    # Check if view exists and is still valid
    if cache_key in VIEW_CACHE:
        view = VIEW_CACHE[cache_key]
        # Update the view if squad names changed
        if multi and squad_names and view.squad_names != squad_names:
            # Need to create a new view since we can't easily modify existing one
            view = SquadSignupView(bot, message_id, op_id, multi, squad_names)
            VIEW_CACHE[cache_key] = view
        return view
    
    # Create new view if not in cache
    view = SquadSignupView(bot, message_id, op_id, multi, squad_names)
    VIEW_CACHE[cache_key] = view
    return view

class JoinButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Join", style=discord.ButtonStyle.success, custom_id="squadup_join")

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        post = get_post_data(view.message_id)
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

        update_post_data(view.message_id, post)
        embed = view.bot.get_cog("SquadUp").build_embed(post)
        await interaction.message.edit(embed=embed)

class MaybeButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Maybe", style=discord.ButtonStyle.secondary, custom_id="squadup_maybe")

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        post = get_post_data(view.message_id)
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

        update_post_data(view.message_id, post)
        embed = view.bot.get_cog("SquadUp").build_embed(post)
        await interaction.message.edit(embed=embed)

class RemoveMeButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Remove Me", style=discord.ButtonStyle.danger, custom_id="squadup_remove")

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        post = get_post_data(view.message_id)
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

        update_post_data(view.message_id, post)
        embed = view.bot.get_cog("SquadUp").build_embed(post)
        await interaction.message.edit(embed=embed)
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
        post = get_post_data(self.message_id)
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
            update_post_data(self.message_id, post)

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
        post = get_post_data(view.message_id)
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

        update_post_data(view.message_id, post)
        embed = view.bot.get_cog("SquadUp").build_embed(post)
        await interaction.message.edit(embed=embed)

class CloseButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🔒 Close Signups", style=discord.ButtonStyle.danger, custom_id="squadup_close")

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        post = get_post_data(view.message_id)
        if not post:
            await interaction.response.send_message("Post not found.", ephemeral=True)
            return
        if interaction.user.id != post["op_id"]:
            await interaction.response.send_message("Only the OP can close signups.", ephemeral=True)
            return

        post["closed"] = True
        update_post_data(view.message_id, post)
        save_all_posts()  # Immediately save closed posts
        
        for child in view.children:
            child.disabled = True
        await interaction.message.edit(view=view)
        await interaction.response.send_message("✅ Signups closed.", ephemeral=True)

class AddMoreSquadsButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Add More Squads", style=discord.ButtonStyle.primary, custom_id="squadup_add_more_squads")
    
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        post = get_post_data(view.message_id)
        
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
            
        post = get_post_data(self.message_id)
        
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
        update_post_data(self.message_id, post)
        
        # Update the message
        cog = interaction.client.get_cog("SquadUp")
        embed = cog.build_embed(post)
        
        # Get or create updated view with new squads
        new_view = get_or_create_view(
            interaction.client, 
            self.message_id, 
            post["op_id"], 
            multi=True, 
            squad_names=list(post["squads"].keys())
        )
        
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
            
        post = get_post_data(self.message_id)
        
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
            
        update_post_data(self.message_id, post)
        
        # Update the message
        cog = interaction.client.get_cog("SquadUp")
        embed = cog.build_embed(post)
        
        # Get or create updated view with new squads
        new_view = get_or_create_view(
            interaction.client, 
            self.message_id, 
            post["op_id"], 
            multi=True, 
            squad_names=list(post["squads"].keys())
        )
        
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
        
        # Add the "Add More Squads" button that only the OP can see
        if multi:
            self.add_item(AddMoreSquadsButton())

class SquadUp(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Initialize caches
        self.config_data = ensure_file_exists(CONFIG_FILE, {"allowed_roles": ["Squad Leader", "Admin"], "default_squad_size": 6})
        
        # Load all posts into cache for faster access
        global POST_CACHE
        POST_CACHE = ensure_file_exists(POSTS_FILE, {})
        
        # Set up periodic tasks
        self.bg_task = bot.loop.create_task(self._background_tasks())
        
    async def _background_tasks(self):
        """Background task to handle periodic saving and view registration"""
        await self.bot.wait_until_ready()
        
        # Register persistent views
        for msg_id, post in POST_CACHE.items():
            if not post.get("closed", False):
                if post.get("multi"):
                    squad_names = list(post["squads"].keys())
                    view = get_or_create_view(self.bot, int(msg_id), post["op_id"], multi=True, squad_names=squad_names)
                else:
                    view = get_or_create_view(self.bot, int(msg_id), post["op_id"], multi=False)
                self.bot.add_view(view, message_id=int(msg_id))
        
        # Periodic save task
        while not self.bot.is_closed():
            check_and_save_posts()
            await asyncio.sleep(10)  # Check every 10 seconds

    def cog_unload(self):
        """Called when cog is unloaded"""
        # Save all data before unloading
        save_all_posts()
        if self.bg_task:
            self.bg_task.cancel()

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
        view = get_or_create_view(self.bot, None, interaction.user.id, multi=False)
        
        # Handle image attachment if provided
        if image:
            post_data["image_url"] = f"attachment://{image.filename}"
            file = await image.to_file()
            message = await interaction.channel.send(embed=embed, file=file, view=view)
        else:
            message = await interaction.channel.send(embed=embed, view=view)
            
        view.message_id = message.id

        update_post_data(message.id, post_data)
        save_all_posts()  # Force save new posts immediately
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
        view = get_or_create_view(self.bot, None, interaction.user.id, multi=True, squad_names=squad_names)
        
        # Handle image attachment if provided
        if image:
            post_data["image_url"] = f"attachment://{image.filename}"
            file = await image.to_file()
            message = await interaction.channel.send(embed=embed, file=file, view=view)
        else:
            message = await interaction.channel.send(embed=embed, view=view)
            
        view.message_id = message.id

        update_post_data(message.id, post_data)
        save_all_posts()  # Force save new posts immediately
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
        view = get_or_create_view(self.bot, None, interaction.user.id, multi=True, squad_names=squad_names)
        
        # Handle image attachment if provided
        if image:
            post_data["image_url"] = f"attachment://{image.filename}"
            file = await image.to_file()
            message = await interaction.channel.send(embed=embed, file=file, view=view)
        else:
            message = await interaction.channel.send(embed=embed, view=view)
            
        view.message_id = message.id

        update_post_data(message.id, post_data)
        save_all_posts()  # Force save new posts immediately
        await interaction.response.send_message("✅ CrewUp post created.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(SquadUp(bot))
