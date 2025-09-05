import discord
from discord.ext import commands, tasks
import json
import os
from datetime import datetime, timedelta
import pytz

# ===== CONFIG =====
CALENDAR_CHANNEL_ID = 1332736267485708419  # <-- replace with your calendar channel ID
ALLOWED_ROLES = ["Administration", "Fight Arrangeer", "7DR-SNCO"]  # roles that can add/edit/remove events
DATA_FILE = "events.json"
TIMEZONE = pytz.timezone("Europe/London")  # UK time

# ===== STORAGE =====
if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w") as f:
        json.dump({"events": [], "calendar_channel_id": None, "calendar_message_id": None}, f)

with open(DATA_FILE, "r") as f:
    data = json.load(f)


def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)


# ===== EMBED BUILDER =====
def build_calendar_embed():
    now = datetime.now(TIMEZONE)
    current_month = now.month
    next_month = (now.month % 12) + 1

    events = data["events"]
    dated_events = [e for e in events if e["date"] != "TBD"]
    tbd_events = [e for e in events if e["date"] == "TBD"]

    dated_events = sorted(dated_events, key=lambda e: datetime.fromisoformat(e["date"]))

    grouped = {"this_month": [], "next_month": [], "other": {}, "tbd": []}

    for e in dated_events:
        dt = datetime.fromisoformat(e["date"])
        month = dt.month
        entry = f"**{e['title']}** — <t:{int(dt.timestamp())}:F>\nOrganiser: <@{e['organiser']}>"

        if e.get("squad_maker"):
            entry += f"\nSquad Maker: <@{e['squad_maker']}>"
        if e.get("reminder_hours"):
            entry += f"\nReminder: {e['reminder_hours']}h before"

        if month == current_month:
            grouped["this_month"].append(entry)
        elif month == next_month:
            grouped["next_month"].append(entry)
        else:
            grouped["other"].setdefault(month, []).append(entry)

    for e in tbd_events:
        entry = f"**{e['title']}** — 📌 TBD\nOrganiser: <@{e['organiser']}>"
        if e.get("squad_maker"):
            entry += f"\nSquad Maker: <@{e['squad_maker']}>"
        grouped["tbd"].append(entry)

    embed = discord.Embed(title="📅 Server Events", color=discord.Color.blue())
    embed.timestamp = datetime.now(TIMEZONE)

    if grouped["this_month"]:
        embed.add_field(
            name=now.strftime("%B"),
            value="\n\n".join(grouped["this_month"]),
            inline=False,
        )
    if grouped["next_month"]:
        next_dt = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
        embed.add_field(
            name=next_dt.strftime("%B"),
            value="\n\n".join(grouped["next_month"]),
            inline=False,
        )
    for m, items in grouped["other"].items():
        embed.add_field(
            name=datetime(2000, m, 1).strftime("%B"),
            value="\n\n".join(items),
            inline=False,
        )
    if grouped["tbd"]:
        embed.add_field(
            name="📌 TBD",
            value="\n\n".join(grouped["tbd"]),
            inline=False,
        )

    if not events:
        embed.description = "No events scheduled."

    return embed


# ===== BUTTON VIEW =====
class CalendarButtons(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="➕ Add Event", style=discord.ButtonStyle.green)
    async def add_event(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.open_event_modal(interaction, "add")

    @discord.ui.button(label="✏️ Edit Event", style=discord.ButtonStyle.blurple)
    async def edit_event(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.edit_event_selection(interaction)

    @discord.ui.button(label="🗑️ Remove Event", style=discord.ButtonStyle.red)
    async def remove_event(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.remove_event_selection(interaction)


# ===== MODAL FOR ADD/EDIT =====
class EventModal(discord.ui.Modal):
    def __init__(self, cog, title, index=None):
        super().__init__(title=title)
        self.cog = cog
        self.index = index

        self.add_item(discord.ui.InputText(label="Event Title"))
        self.add_item(discord.ui.InputText(label="Event Date (YYYY-MM-DD HH:MM or TBD)", required=False))
        self.add_item(discord.ui.InputText(label="Optional Squad Maker (mention ID)", required=False))
        self.add_item(discord.ui.InputText(label="Reminder Hours (optional)", required=False))

    async def callback(self, interaction: discord.Interaction):
        title = self.children[0].value.strip()
        date_value = self.children[1].value.strip()
        squad_maker = self.children[2].value.strip() or None
        reminder = int(self.children[3].value) if self.children[3].value else None

        if not date_value or date_value.lower() == "tbd":
            date_str = "TBD"
        else:
            dt = TIMEZONE.localize(datetime.strptime(date_value, "%Y-%m-%d %H:%M"))
            date_str = dt.isoformat()

        event = {
            "title": title,
            "date": date_str,
            "organiser": interaction.user.id,
            "squad_maker": int(squad_maker) if squad_maker else None,
            "reminder_hours": reminder,
            "reminded": False
        }

        if self.index is not None:
            self.cog.data["events"][self.index] = event
            action = "Edited"
        else:
            self.cog.data["events"].append(event)
            action = "Added"

        save_data()
        await self.cog.update_calendar_message()
        await interaction.response.send_message(f"✅ {action} event **{title}**", ephemeral=True)


# ===== MAIN COG =====
class CalendarCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data = data
        self.reminder_task.start()

    def has_permission(self, interaction: discord.Interaction):
        return any(r.name in ALLOWED_ROLES for r in interaction.user.roles)

    @commands.Cog.listener()
    async def on_ready(self):
        channel = self.bot.get_channel(CALENDAR_CHANNEL_ID)
        if not channel:
            print("Calendar channel not found")
            return

        message_id = self.data.get("calendar_message_id")
        message = None

        if message_id:
            try:
                message = await channel.fetch_message(message_id)
            except discord.NotFound:
                message = None

        if not message:
            embed = build_calendar_embed()
            view = CalendarButtons(self)
            new_message = await channel.send(embed=embed, view=view)
            self.data["calendar_channel_id"] = channel.id
            self.data["calendar_message_id"] = new_message.id
            save_data()

        print("✅ Calendar ready")

    # ===== BUTTON HELPERS =====
    async def open_event_modal(self, interaction: discord.Interaction, mode="add", index=None):
        if not self.has_permission(interaction):
            await interaction.response.send_message("❌ You don’t have permission.", ephemeral=True)
            return
        modal = EventModal(self, f"{mode.title()} Event", index)
        await interaction.response.send_modal(modal)

    async def edit_event_selection(self, interaction: discord.Interaction):
        if not self.has_permission(interaction):
            await interaction.response.send_message("❌ You don’t have permission.", ephemeral=True)
            return
        options = [
            discord.SelectOption(label=e["title"], value=str(i))
            for i, e in enumerate(self.data["events"][-25:][::-1])
        ]
        if not options:
            await interaction.response.send_message("No events to edit.", ephemeral=True)
            return

        select = discord.ui.Select(placeholder="Choose event to edit", options=options)

        async def select_callback(inter: discord.Interaction):
            idx = int(select.values[0])
            await self.open_event_modal(inter, "edit", idx)

        select.callback = select_callback
        view = discord.ui.View()
        view.add_item(select)
        await interaction.response.send_message("Select event to edit:", view=view, ephemeral=True)

    async def remove_event_selection(self, interaction: discord.Interaction):
        if not self.has_permission(interaction):
            await interaction.response.send_message("❌ You don’t have permission.", ephemeral=True)
            return
        options = [
            discord.SelectOption(label=e["title"], value=str(i))
            for i, e in enumerate(self.data["events"][-25:][::-1])
        ]
        if not options:
            await interaction.response.send_message("No events to delete.", ephemeral=True)
            return

        select = discord.ui.Select(placeholder="Choose event to delete", options=options)

        async def select_callback(inter: discord.Interaction):
            idx = int(select.values[0])
            removed = self.data["events"].pop(idx)
            save_data()
            await self.update_calendar_message()
            await inter.response.send_message(f"🗑️ Deleted **{removed['title']}**", ephemeral=True)

        select.callback = select_callback
        view = discord.ui.View()
        view.add_item(select)
        await interaction.response.send_message("Select event to delete:", view=view, ephemeral=True)

    # ===== UPDATE CALENDAR =====
    async def update_calendar_message(self):
        channel = self.bot.get_channel(self.data["calendar_channel_id"])
        if not channel:
            return
        try:
            message = await channel.fetch_message(self.data["calendar_message_id"])
            await message.edit(embed=build_calendar_embed(), view=CalendarButtons(self))
        except discord.NotFound:
            embed = build_calendar_embed()
            view = CalendarButtons(self)
            new_message = await channel.send(embed=embed, view=view)
            self.data["calendar_message_id"] = new_message.id
            save_data()

    # ===== REMINDERS TASK =====
    @tasks.loop(minutes=1)
    async def reminder_task(self):
        now = datetime.now(TIMEZONE)
        updated = False
        for e in self.data["events"]:
            if e.get("date") == "TBD" or not e.get("reminder_hours"):
                continue
            if e.get("reminded"):
                continue
            event_dt = datetime.fromisoformat(e["date"])
            reminder_time = event_dt - timedelta(hours=e["reminder_hours"])
            if reminder_time <= now < reminder_time + timedelta(minutes=1):
                users_to_notify = [e['organiser']]
                if e.get("squad_maker"):
                    users_to_notify.append(e['squad_maker'])
                
                for user_id in users_to_notify:
                    user = self.bot.get_user(user_id)
                    if user:
                        try:
                            await user.send(f"⏰ Reminder: Event **{e['title']}** starts at <t:{int(event_dt.timestamp())}:F>!")
                        except discord.Forbidden:
                            print(f"Cannot DM user {user_id}")

                e["reminded"] = True
                updated = True
        if updated:
            save_data()


# ===== ENTRYPOINT =====
async def setup(bot: commands.Bot):
    await bot.add_cog(CalendarCog(bot))