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
        # discord.py 2.x uses TextInput
        super().__init__(title=title)
        self.cog = cog
        self.index = index

        self.add_item(
            discord.ui.TextInput(
                label="Event Title",
                style=discord.TextStyle.short,
                placeholder="E.g. Sunday Ops",
                required=True,
                max_length=200,
            )
        )
        self.add_item(
            discord.ui.TextInput(
                label="Event Date (YYYY-MM-DD HH:MM or TBD)",
                style=discord.TextStyle.short,
                placeholder="2025-09-10 19:00 or TBD",
                required=False,
            )
        )
        self.add_item(
            discord.ui.TextInput(
                label="Optional Squad Maker (mention ID)",
                style=discord.TextStyle.short,
                required=False,
                placeholder="123456789012345678",
            )
        )
        self.add_item(
            discord.ui.TextInput(
                label="Reminder Hours (optional)",
                style=discord.TextStyle.short,
                required=False,
                placeholder="e.g. 2",
            )
        )

    async def callback(self, interaction: discord.Interaction):
        import logging, traceback
        try:
            title = self.children[0].value.strip()
            date_value = self.children[1].value.strip()
            squad_maker = self.children[2].value.strip() or None
            reminder_raw = self.children[3].value.strip()

            # Parse reminder safely
            reminder = None
            if reminder_raw:
                try:
                    reminder = int(reminder_raw)
                except ValueError:
                    await interaction.response.send_message("❌ Reminder must be an integer number of hours.", ephemeral=True)
                    return

            # Parse date safely
            if not date_value or date_value.lower() == "tbd":
                date_str = "TBD"
            else:
                try:
                    dt_naive = datetime.strptime(date_value, "%Y-%m-%d %H:%M")
                    dt = TIMEZONE.localize(dt_naive)
                    date_str = dt.isoformat()
                except Exception:
                    await interaction.response.send_message("❌ Date must be in format YYYY-MM-DD HH:MM, or 'TBD'.", ephemeral=True)
                    return

            event = {
                "title": title,
                "date": date_str,
                "organiser": interaction.user.id,
                "squad_maker": int(squad_maker) if squad_maker else None,
                "reminder_hours": reminder,
                "reminded": False
            }

            if self.index is not None:
                # validate index
                if 0 <= self.index < len(self.cog.data["events"]):
                    self.cog.data["events"][self.index] = event
                    action = "Edited"
                else:
                    await interaction.response.send_message("❌ Invalid event index.", ephemeral=True)
                    return
            else:
                self.cog.data["events"].append(event)
                action = "Added"

            save_data()
            await self.cog.update_calendar_message()
            await interaction.response.send_message(f"✅ {action} event **{title}**", ephemeral=True)

        except Exception as e:
            logging.exception("Exception in EventModal.callback")
            try:
                await interaction.response.send_message(f"❌ Something went wrong: {e}", ephemeral=True)
            except Exception:
                traceback.print_exc()


# ===== MAIN COG =====
class CalendarCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data = data
        self.reminder_task.start()

    def has_permission(self, interaction: discord.Interaction):
        user = interaction.user
        roles = getattr(user, "roles", None)
        if roles is None:
            return False
        return any(r.name in ALLOWED_ROLES for r in roles)

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

        events = self.data.get("events", [])
        if not events:
            await interaction.response.send_message("No events to edit.", ephemeral=True)
            return

        recent = events[-25:]
        reversed_recent = list(recent)[::-1]  # most recent first
        options = []
        for j, e in enumerate(reversed_recent):
            real_idx = len(events) - 1 - j
            options.append(discord.SelectOption(label=e.get("title", "Untitled"), value=str(real_idx)))

        select = discord.ui.Select(placeholder="Choose event to edit", options=options, max_values=1)

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

        events = self.data.get("events", [])
        if not events:
            await interaction.response.send_message("No events to delete.", ephemeral=True)
            return

        recent = events[-25:]
        reversed_recent = list(recent)[::-1]  # most recent first
        options = []
        for j, e in enumerate(reversed_recent):
            real_idx = len(events) - 1 - j
            options.append(discord.SelectOption(label=e.get("title", "Untitled"), value=str(real_idx)))

        select = discord.ui.Select(placeholder="Choose event to delete", options=options, max_values=1)

        async def select_callback(inter: discord.Interaction):
            idx = int(select.values[0])
            if 0 <= idx < len(self.data["events"]):
                removed = self.data["events"].pop(idx)
                save_data()
                await self.update_calendar_message()
                await inter.response.send_message(f"🗑️ Deleted **{removed['title']}**", ephemeral=True)
            else:
                await inter.response.send_message("❌ Invalid event index.", ephemeral=True)

        select.callback = select_callback
        view = discord.ui.View()
        view.add_item(select)
        await interaction.response.send_message("Select event to delete:", view=view, ephemeral=True)

    # ===== UPDATE CALENDAR =====
    async def update_calendar_message(self):
        channel_id = self.data.get("calendar_channel_id") or CALENDAR_CHANNEL_ID
        channel = self.bot.get_channel(channel_id)
        if not channel:
            return
        try:
            message = await channel.fetch_message(self.data["calendar_message_id"])
            await message.edit(embed=build_calendar_embed(), view=CalendarButtons(self))
        except Exception:
            embed = build_calendar_embed()
            view = CalendarButtons(self)
            new_message = await channel.send(embed=embed, view=view)
            self.data["calendar_message_id"] = new_message.id
            self.data["calendar_channel_id"] = channel.id
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
            try:
                event_dt = datetime.fromisoformat(e["date"])
            except Exception:
                continue
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
