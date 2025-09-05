import discord
from discord.ext import commands, tasks
import json
import os
from datetime import datetime, timedelta
import pytz
import logging

# ===== CONFIG =====
CALENDAR_CHANNEL_ID = 1332736267485708419  # <-- replace with your calendar channel ID
ALLOWED_ROLES = ["Administration", "Fight Arrangeer", "7DR-SNCO"]  # roles that can add/edit/remove events
DATA_FILE = "events.json"
TIMEZONE = pytz.timezone("Europe/London")  # UK time

# basic logging to file and console
logging.basicConfig(level=logging.INFO, filename="calendar_debug.log", filemode="a",
                    format="%(asctime)s %(levelname)s %(message)s")
console = logging.StreamHandler()
console.setLevel(logging.INFO)
logging.getLogger().addHandler(console)

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

    events = data.get("events", [])
    dated_events = [e for e in events if e.get("date") and e["date"] != "TBD"]
    tbd_events = [e for e in events if e.get("date") == "TBD"]

    # sort by ISO date strings
    try:
        dated_events = sorted(dated_events, key=lambda e: datetime.fromisoformat(e["date"]))
    except Exception:
        # fallback: don't crash the embed builder
        logging.exception("Failed to sort dated events in build_calendar_embed")

    grouped = {"this_month": [], "next_month": [], "other": {}, "tbd": []}

    for e in dated_events:
        try:
            dt = datetime.fromisoformat(e["date"])
        except Exception:
            continue
        month = dt.month
        entry = f"**{e.get('title','Untitled')}** — <t:{int(dt.timestamp())}:F>\nOrganiser: <@{e.get('organiser')}>"

        if e.get("squad_maker"):
            entry += f"\nSquad Maker: <@{e.get('squad_maker')}>"
        if e.get("reminder_hours"):
            entry += f"\nReminder: {e.get('reminder_hours')}h before"

        if month == current_month:
            grouped["this_month"].append(entry)
        elif month == next_month:
            grouped["next_month"].append(entry)
        else:
            grouped["other"].setdefault(month, []).append(entry)

    for e in tbd_events:
        entry = f"**{e.get('title','Untitled')}** — 📌 TBD\nOrganiser: <@{e.get('organiser')}>"
        if e.get("squad_maker"):
            entry += f"\nSquad Maker: <@{e.get('squad_maker')}>"
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

        # Keep references to inputs so they're easier to read in callback
        self.title_input = discord.ui.TextInput(
            label="Event Title",
            style=discord.TextStyle.short,
            placeholder="E.g. Sunday Ops",
            required=True,
            max_length=200,
        )
        self.date_input = discord.ui.TextInput(
            label="Event Date (YYYY-MM-DD HH:MM or TBD)",
            style=discord.TextStyle.short,
            placeholder="2025-09-10 19:00 or TBD",
            required=False,
        )
        self.squad_input = discord.ui.TextInput(
            label="Optional Squad Maker (mention ID)",
            style=discord.TextStyle.short,
            required=False,
            placeholder="123456789012345678",
        )
        self.reminder_input = discord.ui.TextInput(
            label="Reminder Hours (optional)",
            style=discord.TextStyle.short,
            required=False,
            placeholder="e.g. 2",
        )

        # add items in the order you want them displayed
        self.add_item(self.title_input)
        self.add_item(self.date_input)
        self.add_item(self.squad_input)
        self.add_item(self.reminder_input)

    async def callback(self, interaction: discord.Interaction):
        try:
            logging.info("EventModal submitted by user id=%s", getattr(interaction.user, "id", "unknown"))

            title = (self.title_input.value or "").strip()
            date_value = (self.date_input.value or "").strip()
            squad_maker_raw = (self.squad_input.value or "").strip()
            reminder_raw = (self.reminder_input.value or "").strip()

            logging.info("Received values - title=%s, date=%s, squad=%s, reminder=%s",
                         title, date_value, squad_maker_raw, reminder_raw)

            # Parse reminder safely
            reminder = None
            if reminder_raw:
                try:
                    reminder = int(reminder_raw)
                except ValueError:
                    msg = "❌ Reminder must be an integer number of hours."
                    logging.warning(msg + " Got: %s", reminder_raw)
                    if not interaction.response.is_done():
                        await interaction.response.send_message(msg, ephemeral=True)
                    else:
                        await interaction.followup.send(msg, ephemeral=True)
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
                    msg = "❌ Date must be in format YYYY-MM-DD HH:MM, or 'TBD'."
                    logging.warning(msg + " Got: %s", date_value)
                    if not interaction.response.is_done():
                        await interaction.response.send_message(msg, ephemeral=True)
                    else:
                        await interaction.followup.send(msg, ephemeral=True)
                    return

            event = {
                "title": title or "Untitled",
                "date": date_str,
                "organiser": interaction.user.id,
                "squad_maker": int(squad_maker_raw) if squad_maker_raw else None,
                "reminder_hours": reminder,
                "reminded": False
            }

            # Add or edit
            if self.index is not None:
                if 0 <= self.index < len(self.cog.data.get("events", [])):
                    self.cog.data["events"][self.index] = event
                    action = "Edited"
                else:
                    msg = "❌ Invalid event index."
                    logging.warning(msg + " index=%s events_len=%s", self.index, len(self.cog.data.get("events", [])))
                    if not interaction.response.is_done():
                        await interaction.response.send_message(msg, ephemeral=True)
                    else:
                        await interaction.followup.send(msg, ephemeral=True)
                    return
            else:
                self.cog.data.setdefault("events", []).append(event)
                action = "Added"

            save_data()
            await self.cog.update_calendar_message()

            success_msg = f"✅ {action} event **{event['title']}**"
            logging.info(success_msg)
            if not interaction.response.is_done():
                await interaction.response.send_message(success_msg, ephemeral=True)
            else:
                await interaction.followup.send(success_msg, ephemeral=True)

        except Exception as e:
            # write a full traceback to the debug file and attempt to notify user
            logging.exception("Unhandled exception in EventModal.callback")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(f"❌ Something went wrong: {e}", ephemeral=True)
                else:
                    await interaction.followup.send(f"❌ Something went wrong: {e}", ephemeral=True)
            except Exception:
                # final fallback: append to debug log file (logging already writes to file)
                logging.exception("Also failed to notify the user about the exception")


# ===== MAIN COG =====
class CalendarCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data = data
        # start reminders after initialization
        try:
            self.reminder_task.start()
        except RuntimeError:
            # already started
            pass

    def has_permission(self, interaction: discord.Interaction):
        # Ensure we have a Member object (roles only exist on Member)
        user = interaction.user
        roles = getattr(user, "roles", None)
        if roles is None:
            return False
        return any(r.name in ALLOWED_ROLES for r in roles)

    @commands.Cog.listener()
    async def on_ready(self):
        # Ensure the channel exists
        channel = self.bot.get_channel(CALENDAR_CHANNEL_ID)
        if not channel:
            logging.warning("Calendar channel not found (id=%s)", CALENDAR_CHANNEL_ID)
            return

        message_id = self.data.get("calendar_message_id")
        message = None

        if message_id:
            try:
                message = await channel.fetch_message(message_id)
            except Exception:
                message = None

        # If there's an old bot message, delete it so we recreate fresh one and register view
        if message:
            try:
                await message.delete()
            except Exception:
                # ignore if deletion fails for any reason
                logging.exception("Failed to delete old calendar message")

        # Send a fresh calendar message and register the view so buttons work after restart
        embed = build_calendar_embed()
        view = CalendarButtons(self)
        try:
            new_message = await channel.send(embed=embed, view=view)
            self.data["calendar_channel_id"] = channel.id
            self.data["calendar_message_id"] = new_message.id
            save_data()
        except Exception:
            logging.exception("Failed to send calendar message in on_ready")
            return

        # Register the view handlers in memory (so interactions work)
        try:
            self.bot.add_view(CalendarButtons(self))
        except Exception:
            logging.exception("bot.add_view failed in on_ready (ignored)")

        logging.info("✅ Calendar ready (message id=%s)", self.data.get("calendar_message_id"))

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
            try:
                idx = int(select.values[0])
            except Exception:
                await inter.response.send_message("❌ Invalid selection.", ephemeral=True)
                return
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
            try:
                idx = int(select.values[0])
            except Exception:
                await inter.response.send_message("❌ Invalid selection.", ephemeral=True)
                return

            if 0 <= idx < len(self.data.get("events", [])):
                removed = self.data["events"].pop(idx)
                save_data()
                await self.update_calendar_message()
                await inter.response.send_message(f"🗑️ Deleted **{removed.get('title','Untitled')}**", ephemeral=True)
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
            logging.warning("update_calendar_message: channel not found (id=%s)", channel_id)
            return
        try:
            message = await channel.fetch_message(self.data.get("calendar_message_id"))
            # edit in-place if possible
            await message.edit(embed=build_calendar_embed(), view=CalendarButtons(self))
        except Exception:
            # If message not found or other issue, send a fresh one and store id and register view
            logging.exception("Failed to fetch/edit calendar message; sending a fresh one")
            try:
                embed = build_calendar_embed()
                view = CalendarButtons(self)
                new_message = await channel.send(embed=embed, view=view)
                self.data["calendar_message_id"] = new_message.id
                self.data["calendar_channel_id"] = channel.id
                save_data()
                try:
                    self.bot.add_view(CalendarButtons(self))
                except Exception:
                    logging.exception("bot.add_view failed in update_calendar_message (ignored)")
            except Exception:
                logging.exception("Failed to send fresh calendar message in update_calendar_message")

    # ===== REMINDERS TASK =====
    @tasks.loop(minutes=1)
    async def reminder_task(self):
        now = datetime.now(TIMEZONE)
        updated = False
        for e in list(self.data.get("events", [])):
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
                users_to_notify = [e.get('organiser')]
                if e.get("squad_maker"):
                    users_to_notify.append(e.get('squad_maker'))

                for user_id in users_to_notify:
                    try:
                        user = self.bot.get_user(user_id)
                        if user:
                            try:
                                await user.send(f"⏰ Reminder: Event **{e.get('title','Untitled')}** starts at <t:{int(event_dt.timestamp())}:F>!")
                            except discord.Forbidden:
                                logging.warning("Cannot DM user %s", user_id)
                    except Exception:
                        logging.exception("Failed to notify user %s for event %s", user_id, e.get('title'))

                e["reminded"] = True
                updated = True
        if updated:
            save_data()


# ===== ENTRYPOINT =====
async def setup(bot: commands.Bot):
    await bot.add_cog(CalendarCog(bot))
