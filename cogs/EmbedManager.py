import discord
from discord.ext import commands
import json
import os

# ---------------- CONFIG ----------------
GUILD_ID = 1097913605082579024  # your guild ID
DATA_FILE = "stored_embeds.json"

# ---------------- HELPER FUNCTIONS ----------------
def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r") as f:
        return json.load(f)


def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)


class EmbedManager(commands.Cog):
    """Cog to manage static embeds that auto-post/update"""

    def __init__(self, bot):
        self.bot = bot
        self.data = load_data()

    # ---------------- CHEAT SHEET ----------------
    """
    ================= EMBED CHEAT SHEET =================
    COLORS:
        discord.Color.blue()
        discord.Color.green()
        discord.Color.red()
        discord.Color.gold()
        discord.Color.purple()
        discord.Color.orange()
        Custom Hex: discord.Color.from_str("#1abc9c")

    LINE BREAKS:
        "\n"       - new line
        "\n\n"     - blank line
        "\u200b"   - zero width space

    FIELDS:
        embed.add_field(name="Title", value="Content", inline=False)

    SPACER FIELD:
        embed.add_field(name="\u200b", value="\u200b", inline=False)

    =====================================================
    """

    # ---------------- EMBED DEFINITIONS ----------------
    def get_embed_blocks(self):
        """
        Returns a list of embed blocks.
        Each block is a dict: {"channel_id": ..., "embed": discord.Embed}
        """
        blocks = []

        # ---------------- EMBED 1 ----------------
        channel_id = 1099806153170489485
        embed1 = discord.Embed(
            title="📘 About Us",
            description="",
            color=discord.Color.red()
        )
        embed1.add_field(
            name="About 7DR",
            value=(
                "We're 7DR, a military simulation (milsim) EU/UK and US Hell Let Loose console clan "
                "that models itself on the 7th Armoured Division, a real British armed forces unit "
                "that fought throughout both World Wars. For more information on that division, "
                "see the **#the-7th-armoured-division** channel!\n\n"
                "We run organised Hell Let Loose casual and competitive clan scrims every week, "
                "and we maintain an active server."
            ),
            inline=False
        )
        embed1.add_field(
            name="Links",
            value="[Website](https://example.com)\n[Support](https://example.com/support)",
            inline=False
        )
        blocks.append({"channel_id": channel_id, "embed": embed1})

        # ---------------- EMBED 2 ----------------
        channel_id = 1099806153170489485
        embed2 = discord.Embed(
            title="Frequently Asked Questions (FAQs)",
            description="",
            color=discord.Color.green()
        )
        embed2.add_field(
            name="How do I join your clan?",
            value=(
                "Please fill in a recruit form in **#recruitform-requests** and we'll get back to you!\n"
                "Make sure to state which training school you'd like to join: **Infantry**, **Armour**, or **Recon**.\n"
                "You must be **18+** and **level 20** in-game.\n\n"
                "If accepted, you will join an infantry school; once you complete your courses, you will be assigned to a unit."
            ),
            inline=False
        )
        embed2.add_field(
            name="How do I find a squad?",
            value=(
                "As a blueberry you'll be able to see the **#looking-for-squad** channel.\n"
                "Drop a message in there to link up with clan members or other blueberries!"
            ),
            inline=False
        )
        blocks.append({"channel_id": channel_id, "embed": embed2})

        return blocks

    # ---------------- SYNC LOGIC ----------------
    async def sync_embed_block(self, channel_id: int, embed: discord.Embed):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            print(f"[EmbedManager] Channel {channel_id} not found.")
            return

        stored_id = self.data.get(str(channel_id))

        # Fetch existing message if exists
        msg = None
        if stored_id:
            try:
                msg = await channel.fetch_message(stored_id)
            except discord.NotFound:
                print(f"[EmbedManager] Previous embed missing in {channel_id}, will post new.")

        # If message exists, compare
        if msg and msg.embeds and msg.embeds[0].to_dict() != embed.to_dict():
            print(f"[EmbedManager] Updating embed in channel {channel_id}")
            await msg.edit(embed=embed)
        elif msg:
            print(f"[EmbedManager] Embed unchanged in channel {channel_id}")
        else:
            new_msg = await channel.send(embed=embed)
            self.data[str(channel_id)] = new_msg.id
            print(f"[EmbedManager] Posted new embed to channel {channel_id}")

        save_data(self.data)

    async def sync_all_embeds(self):
        blocks = self.get_embed_blocks()
        for block in blocks:
            await self.sync_embed_block(block["channel_id"], block["embed"])

    # ---------------- AUTO-SYNC ON READY ----------------
    @commands.Cog.listener()
    async def on_ready(self):
        print("[EmbedManager] Bot ready — syncing embeds...")
        await self.sync_all_embeds()

    # ---------------- OPTIONAL MANUAL COMMAND ----------------
    @commands.command(name="sync_embeds")
    async def sync_embeds_cmd(self, ctx):
        """Manually sync all embeds to their channels"""
        await self.sync_all_embeds()
        await ctx.send("All embeds synced!")


# ---------------- SETUP ----------------
async def setup(bot):
    await bot.add_cog(EmbedManager(bot))