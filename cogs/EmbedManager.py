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
            title="📘 Server Welcome",
            description=(
                "Welcome to the server.\n"
                "This embed is written directly in code.\n\n"
                "**Edit this text → restart bot → updates automatically.**"
            ),
            color=discord.Color.blue()
        )

        embed.add_field(
            name="Rules",
            value="1. Be nice.\n2. No spam.\n3. Follow the guidelines.",
            inline=False
        )

        embed.add_field(
            name="Links",
            value="[Website](https://example.com)\n[Support](https://example.com/support)",
            inline=False
        )

        blocks.append({"channel_id": channel_id, "embed": embed})

        # ------------------------------------------------------------
        # EMBED BLOCK 2
        # ------------------------------------------------------------
        channel_id = 1099806153170489485  # <--- another channel ID

        embed = discord.Embed(
            title="🎮 Game Servers",
            description="Here is the latest server information.\n\u200b",
            color=discord.Color.green()
        )

        embed.add_field(
            name="Current Servers",
            value="• EU1 — Online\n• EU2 — Restarting\n• US — Online",
            inline=False
        )

        embed.add_field(
            name="Upcoming Events",
            value="• Friday Op — 20:00 UTC\n• Training Night — 19:00 UTC",
            inline=False
        )

        blocks.append({"channel_id": channel_id, "embed": embed})

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