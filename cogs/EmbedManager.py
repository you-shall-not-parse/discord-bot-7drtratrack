import discord
from discord.ext import commands
import json
import os

GUILD_ID = 1097913605082579024  # future slash command scope
EMBED_STORE_FILE = "stored_embeds.json"


def load_embed_store():
    if not os.path.exists(EMBED_STORE_FILE):
        return {}
    with open(EMBED_STORE_FILE, "r") as f:
        return json.load(f)


def save_embed_store(data):
    with open(EMBED_STORE_FILE, "w") as f:
        json.dump(data, f, indent=4)


class EmbedManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.embed_store = load_embed_store()

    # ================================================================
    # CHEAT SHEET
    # ================================================================
    """
    ================= EMBED CHEAT SHEET =================

    COLORS:
        discord.Color.blue()
        discord.Color.green()
        discord.Color.red()
        discord.Color.gold()
        discord.Color.purple()
        discord.Color.teal()
        discord.Color.orange()

        Hex:
            discord.Color.from_str("#1abc9c")

    NEW LINES:
        "\n"       - new line
        "\n\n"     - blank line
        "\u200b"   - zero width spacer

    FIELDS:
        embed.add_field(name="Title", value="Content", inline=False)

    SECTIONS:
        embed.add_field(name="Section Title", value="Your text", inline=False)

    SPACER FIELD:
        embed.add_field(name="\u200b", value="\u200b", inline=False)

    =====================================================
    """

    # ================================================================
    # YOU WRITE ALL YOUR EMBEDS HERE
    # Each block includes its own channel ID
    # ================================================================
    def get_embed_blocks(self):
        """
        RETURNS A LIST OF BLOCKS:
        [
            {
                "channel_id": ...,
                "embed": <discord.Embed>
            },
            ...
        ]
        """

        blocks = []

        # ------------------------------------------------------------
        # EMBED BLOCK 1
        # ------------------------------------------------------------
        channel_id = 1099806153170489485  # <--- put the channel ID here

        embed = discord.Embed(
            title="📘 About Us",
            description="",
            color=discord.Color.red()
        )

        embed.add_field(
            name="",
            value="We're 7DR a miltary simulation (milsim) EU/UK and US Hell Let Loose console clan that models itself on the 7th Armoured Division, a real life British armed forces division that fought throughout both World Wars, for more information on that unit see #the-7th-armoured-division channel!" \n
"We have organised Hell Let Loose casual and competitive clan scrims on a weekly basis and an active server",
            inline=False
        )

        embed.add_field(
            name="Links",
            value="[Website](https://example.com)\n[Support](https://example.com/support)",
            inline=False
        )

        blocks.append({"channel_id": channel_id, "embed": embed1})

        # ------------------------------------------------------------
        # EMBED BLOCK 2
        # ------------------------------------------------------------
        channel_id = 1099806153170489485  # <--- another channel ID

        embed = discord.Embed(
            title="Frequently Asked Questions (FAQs)",
            description="",
            color=discord.Color.green()
        )

        embed.add_field(
            name="How do I join your clan?",
            value="Please fill in a recruit form in #recruitform-requests and we'll get back to you! Ensure you specify the training school you'd like to join - Infantry, Armour or Recon. You must be over 18 years old and level 20 in-game. If your application is accepted you will join an infantry school and on completion join a unit!",
            inline=False
        )

        embed.add_field(
            name="How do I find a squad?",
            value="As a blueberry you'll be able to see #looking-for-squad channel. drop a message in there to link up with clan members or fellow blueberries!",
            inline=False
        )

        blocks.append({"channel_id": channel_id, "embed": embed2})

        # ------------------------------------------------------------
        # Add more embed blocks here as needed
        # ------------------------------------------------------------

        return blocks

    # ================================================================
    # POST OR UPDATE LOGIC
    # ================================================================
    async def sync_embed_block(self, channel_id: int, embed: discord.Embed):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            print(f"[EmbedManager] Channel {channel_id} not found.")
            return

        stored = self.embed_store.get(str(channel_id))

        # If embed stored previously → fetch message
        if stored:
            try:
                msg = await channel.fetch_message(stored["message_id"])

                # Compare: update only if changed
                if msg.embeds and msg.embeds[0].to_dict() != embed.to_dict():
                    print(f"[EmbedManager] Updating embed in channel {channel_id}")
                    await msg.edit(embed=embed)
                else:
                    print(f"[EmbedManager] Embed unchanged in channel {channel_id}")
                return

            except discord.NotFound:
                print(f"[EmbedManager] Stored message missing. Reposting.")

        # Message missing → send new
        new_msg = await channel.send(embed=embed)
        self.embed_store[str(channel_id)] = {"message_id": new_msg.id}
        save_embed_store(self.embed_store)

        print(f"[EmbedManager] Posted new embed to channel {channel_id}")

    # ================================================================
    # OPTIONAL MANUAL COMMAND
    # ================================================================
    @commands.command(name="sync_embeds")
    async def sync_embeds_cmd(self, ctx):
        blocks = self.get_embed_blocks()
        for block in blocks:
            await self.sync_embed_block(block["channel_id"], block["embed"])
        await ctx.send("Embeds synced.")

    # ================================================================
    # AUTO SYNC ON READY
    # ================================================================
    @commands.Cog.listener()
    async def on_ready(self):
        print("[EmbedManager] Bot ready — syncing embeds...")
        blocks = self.get_embed_blocks()

        for block in blocks:
            await self.sync_embed_block(block["channel_id"], block["embed"])


async def setup(bot):
    await bot.add_cog(EmbedManager(bot))