import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import Modal, TextInput, View, Select
from datetime import datetime, timedelta
import pytz
import json
import os

EVENTS_FILE = "events.json"
TIMEZONE = pytz.timezone("Europe/London")
CALENDAR_MANAGER_ROLES = ["Administration", "7DR-SNCO", "Fight Arrangeer"]
CALENDAR_CHANNEL_ID = 1332736267485708419  # Your calendar channel ID
GUILD_ID = 1097913605082579024  # Your guild/server ID

# ---------------- Utility ----------------
def has_calendar_permission(member: discord.Member) -> bool:
    return any(role.name in CALENDAR_MANAGER_ROLES for role in member.roles)

def load_events():
    if not os.path.exists(EVENTS_FILE):
        return {"events": [], "calendar_message_id": None}
    with open(EVENTS_FILE, "r") as f:
        return json.load(f)

def save_events(data):
    with open(EVENTS_FILE, "w") as f:
        json.dump(data, f, indent=4)

def event_to_str(event):
    if event["date"] == "TBD":
        dt_str = "📌 TBD"
    else:
        dt = datetime.fromisoformat(event["date"]).astimezone(TIMEZONE)
        dt_str = f"🗓️ {dt.strftime('%d-%m-%Y, %H:%M %Z')}"
    organiser = f"<@{event['organiser']}>" if event.get("organiser") else "Unknown"
    squad_maker = f"<@{event['squad_maker']}>" if event.get("squad_maker") else "None"
    reminder = f"{event['reminder_hours']}h before" if event.get("reminder_hours") else "None"
    thread = f"[Link](https://discord.com/channels/{event.get('guild_id')}/{event.get('thread_id')})" if event.get("thread_id") else "None"
    return (
        f"**{event['title']}**\n"
        f"{dt_str}\n"
        f"👤 Organiser: {organiser}\n"
        f"⚔️ Squad Maker: {squad_maker}\n"
        f"⏰ Reminder: {reminder}\n"
        f"🧵 Thread: {thread}"
    )

def group_events(events):
    now = datetime.now(TIMEZONE)
    this_month, next_month, future, tbd = [], [], [], []
    for event in events:
        if event["date"] == "TBD":
            tbd.append(event)
            continue
        dt = datetime.fromisoformat(event["date"]).astimezone(TIMEZONE)
        if event.get("recurring") and dt < now:
            continue
        if dt.month == now.month and dt.year == now.year:
            this_month.append(event)
        elif dt.month == (now.month % 12) + 1 and dt.year == (now.year if now.month < 12 else now.year+1):
            next_month.append(event)
        else:
            future.append(event)
    return this_month, next_month, future, tbd

def build_calendar_embed(events):
    this_month, next_month, future, tbd = group_events(events)
    embed = discord.Embed(
        title="📅 Unit Calendar",
        description="Upcoming scheduled events",
        colour=discord.Colour.blue(),
        timestamp=datetime.now(TIMEZONE)
    )
    if this_month:
        embed.add_field(name="This Month", value="\n\n".join(event_to_str(e) for e in this_month), inline=False)
    if next_month:
        embed.add_field(name="Next Month", value="\n\n".join(event_to_str(e) for e in next_month), inline=False)
    if future:
        embed.add_field(name="Future Months", value="\n\n".join(event_to_str(e) for e in future), inline=False)
    if tbd:
        embed.add_field(name="📌 TBD", value="\n\n".join(event_to_str(e) for e in tbd), inline=False)
    if not (this_month or next_month or future or tbd):
        embed.description = "No events scheduled."
    return embed

# ---------------- Basic Modal ----------------
class EventBasicModal(Modal):
    def __init__(self, interaction, event=None):
        super().__init__(title="Add/Edit Event - Basic Info")
        self.interaction = interaction
        self.event = event

        self.title_input = TextInput(
            label="Title", 
            default=event["title"] if event else "", 
            required=True
        )
        self.add_item(self.title_input)
        
        self.date_input = TextInput(
            label="Date & Time (DD-MM-YYYY HH:MM) or TBD",
            default=datetime.fromisoformat(event["date"]).strftime("%d-%m-%Y %H:%M") if event and event["date"] != "TBD" else "",
            required=True
        )
        self.add_item(self.date_input)
        
        self.recurring_input = TextInput(
            label="Recurring? (yes/no)",
            default="yes" if event and event.get("recurring") else "no",
            required=False
        )
        self.add_item(self.recurring_input)
        
        self.organiser_input = TextInput(
            label="Organiser (mention @user, optional)",
            default=f"<@{event['organiser']}>" if event and event.get("organiser_override") else "",
            required=False
        )
        self.add_item(self.organiser_input)
        
        self.squad_maker_input = TextInput(
            label="Squad Maker (mention @user, optional)",
            default=f"<@{event['squad_maker']}>" if event and event.get("squad_maker") else "",
            required=False
        )
        self.add_item(self.squad_maker_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not has_calendar_permission(interaction.user):
            await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
            return

        # Show advanced modal
        advanced_modal = EventAdvancedModal(
            interaction, 
            self.event,
            self.title_input.value,
            self.date_input.value,
            self.recurring_input.value,
            self.organiser_input.value,
            self.squad_maker_input.value
        )
        await interaction.response.send_modal(advanced_modal)

# ---------------- Advanced Modal ----------------
class EventAdvancedModal(Modal):
    def __init__(self, interaction, event=None, title="", date_value="", recurring="", organiser="", squad_maker=""):
        super().__init__(title="Add/Edit Event - Advanced Settings")
        self.interaction = interaction
        self.event = event
        
        # Store basic info
        self.title = title
        self.date_value = date_value
        self.recurring = recurring
        self.organiser = organiser
        self.squad_maker = squad_maker
        
        # Advanced settings
        self.reminder_input = TextInput(
            label="Reminder (hours before, optional)",
            default=str(event["reminder_hours"]) if event and event.get("reminder_hours") else "",
            required=False
        )
        self.add_item(self.reminder_input)
        
        self.thread_input = TextInput(
            label="Thread Channel ID (optional)",
            default=str(event["thread_channel"]) if event and event.get("thread_channel") else "",
            required=False
        )
        self.add_item(self.thread_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not has_calendar_permission(interaction.user):
            await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
            return

        data = load_events()
        events = data["events"]

        # Process date
        try:
            if self.date_value.strip().upper() == "TBD":
                dt_iso = "TBD"
            else:
                dt = datetime.strptime(self.date_value.strip(), "%d-%m-%Y %H:%M")
                dt_iso = TIMEZONE.localize(dt).isoformat()
        except ValueError:
            await interaction.response.send_message("❌ Invalid date format. Use DD-MM-YYYY HH:MM or 'TBD'.", ephemeral=True)
            return

        # Process other fields
        recurring = self.recurring.lower() == "yes"

        organiser_override = None
        squad_maker = None
        if self.organiser.strip().startswith("<@") and self.organiser.strip().endswith(">"):
            try:
                organiser_override = int(self.organiser.strip()[2:-1].replace("!", ""))
            except ValueError:
                pass
        if self.squad_maker.strip().startswith("<@") and self.squad_maker.strip().endswith(">"):
            try:
                squad_maker = int(self.squad_maker.strip()[2:-1].replace("!", ""))
            except ValueError:
                pass

        try:
            reminder_hours = int(self.reminder_input.value) if self.reminder_input.value.strip().isdigit() else None
        except ValueError:
            reminder_hours = None
            
        try:
            thread_channel = int(self.thread_input.value) if self.thread_input.value.strip().isdigit() else None
        except ValueError:
            thread_channel = None

        new_event = {
            "title": self.title,
            "date": dt_iso,
            "recurring": recurring,
            "organiser": organiser_override or interaction.user.id,
            "squad_maker": squad_maker,
            "reminder_hours": reminder_hours,
            "thread_channel": thread_channel,
            "thread_id": None,
            "guild_id": interaction.guild_id
        }

        # Remove old if editing
        if self.event:
            events = [e for e in events if e["title"] != self.event["title"]]
        events.append(new_event)
        data["events"] = events
        save_events(data)

        # Thread creation
        if thread_channel:
            try:
                channel = interaction.guild.get_channel(thread_channel)
                if channel and isinstance(channel, discord.TextChannel):
                    thread = await channel.create_thread(name=self.title, type=discord.ChannelType.public_thread)
                    msg = await thread.send(event_to_str(new_event))
                    new_event["thread_id"] = thread.id
                    # Update event with thread info
                    events = [e for e in events if e["title"] != new_event["title"]]
                    events.append(new_event)
                    data["events"] = events
                    save_events(data)
            except Exception as e:
                await interaction.response.send_message(f"⚠️ Error creating thread: {str(e)}", ephemeral=True)
                return

        # Post updated calendar
        try:
            calendar_channel = interaction.guild.get_channel(CALENDAR_CHANNEL_ID)
            if calendar_channel:
                if data.get("calendar_message_id"):
                    try:
                        old_msg = await calendar_channel.fetch_message(data["calendar_message_id"])
                        await old_msg.delete()
                    except:
                        pass
                embed = build_calendar_embed(events)
                msg = await calendar_channel.send(embed=embed)
                data["calendar_message_id"] = msg.id
                save_events(data)
        except Exception as e:
            await interaction.response.send_message(f"⚠️ Error updating calendar: {str(e)}", ephemeral=True)
            return

        await interaction.response.send_message("✅ Event added/updated and calendar refreshed.", ephemeral=True)

# ---------------- Cog ----------------
class CalendarCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        print("Calendar Cog initialized")

    @commands.Cog.listener()
    async def on_ready(self):
        print("Calendar Cog is ready")

    # ---------------- Slash Commands ----------------
    @app_commands.command(name="addevent", description="Add a new event")
    async def addevent(self, interaction: discord.Interaction):
        if not has_calendar_permission(interaction.user):
            await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
            return
        await interaction.response.send_modal(EventBasicModal(interaction))

    @app_commands.command(name="editevent", description="Edit an existing event")
    async def editevent(self, interaction: discord.Interaction):
        if not has_calendar_permission(interaction.user):
            await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
            return
        data = load_events()
        events = data["events"]
        if not events:
            await interaction.response.send_message("No events to edit.", ephemeral=True)
            return
        options = [discord.SelectOption(label=e["title"][:100], value=e["title"][:100]) for e in events[-25:][::-1]]
        select = Select(placeholder="Choose an event to edit", options=options)

        async def select_callback(inter):
            chosen_event = next(e for e in events if e["title"].startswith(select.values[0]))
            await inter.response.send_modal(EventBasicModal(inter, chosen_event))
        select.callback = select_callback
        view = View()
        view.add_item(select)
        await interaction.response.send_message("Select an event to edit:", view=view, ephemeral=True)

    @app_commands.command(name="deleteevent", description="Delete an existing event")
    async def deleteevent(self, interaction: discord.Interaction):
        if not has_calendar_permission(interaction.user):
            await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
            return
        data = load_events()
        events = data["events"]
        if not events:
            await interaction.response.send_message("No events to delete.", ephemeral=True)
            return
        options = [discord.SelectOption(label=e["title"][:100], value=e["title"][:100]) for e in events[-25:][::-1]]
        select = Select(placeholder="Choose an event to delete", options=options)

        async def select_callback(inter):
            selected_value = select.values[0]
            event = next(e for e in events if e["title"].startswith(selected_value))
            title = event["title"]
            
            # Confirm deletion
            confirm_view = View()
            confirm_button = discord.ui.Button(label="Confirm Deletion", style=discord.ButtonStyle.danger)
            cancel_button = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
            
            async def confirm_callback(confirm_inter):
                if event.get("thread_id"):
                    try:
                        thread = interaction.guild.get_thread(event["thread_id"])
                        if thread:
                            await thread.edit(archived=True)
                    except:
                        pass
                        
                events.remove(event)
                data["events"] = events
                save_events(data)
                
                # Refresh calendar
                calendar_channel = interaction.guild.get_channel(CALENDAR_CHANNEL_ID)
                if calendar_channel:
                    if data.get("calendar_message_id"):
                        try:
                            old_msg = await calendar_channel.fetch_message(data["calendar_message_id"])
                            await old_msg.delete()
                        except:
                            pass
                    embed = build_calendar_embed(events)
                    msg = await calendar_channel.send(embed=embed)
                    data["calendar_message_id"] = msg.id
                    save_events(data)
                await confirm_inter.response.send_message(f"✅ Event `{title}` deleted and calendar refreshed.", ephemeral=True)
                
            async def cancel_callback(cancel_inter):
                await cancel_inter.response.send_message("Operation canceled.", ephemeral=True)
                
            confirm_button.callback = confirm_callback
            cancel_button.callback = cancel_callback
            confirm_view.add_item(confirm_button)
            confirm_view.add_item(cancel_button)
            
            await inter.response.send_message(f"Are you sure you want to delete event `{title}`?", view=confirm_view, ephemeral=True)

        select.callback = select_callback
        view = View()
        view.add_item(select)
        await interaction.response.send_message("Select an event to delete:", view=view, ephemeral=True)

    @app_commands.command(name="calendar", description="Show the unit calendar")
    async def calendar(self, interaction: discord.Interaction):
        data = load_events()
        embed = build_calendar_embed(data["events"])
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(CalendarCog(bot))
