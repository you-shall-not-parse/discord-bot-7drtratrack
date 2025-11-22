import discord
from discord.ext import commands
import json
import os

# ==========================================================
# CONFIG
# ==========================================================

GUILD_ID = 123456789012345678  # Your guild ID for future slash commands
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

    # ==========================================================
    # EMBED CHEAT SHEET — QUICK REFERENCE
    # ==========================================================
    """
    ========== EMBED CHEAT SHEET ==========

    COLOURS:
        discord.Color.blue()
        discord.Color.green()
        discord.Color.red()
        discord.Color.orange()
        discord.Color.gold()
        discord.Color.purple()
        discord.Color.teal()

        Custom Hex:
            discord.Color.from_str("#1abc9c")

    LINE BREAKS:
        "\n"         → new line
        "\n\n"       → blank line
        "\u200b"     → zero width space (for spacing)

    SECTIONS:
        embed.add_field(name="TITLE", value="Your text", inline=False)

    ZERO WIDTH SPACER:
        embed.add_field(name="\u200b", value="\u200b", inline=False)

    ======================================
    """

    # ==========================================================
    # WRITE YOUR EMBEDS IN THIS FUNCTION
    # ==========================================================
    def get_embeds(self):
        """
        Define all embeds in code here.
        Key = channel ID
        Value = discord.Embed object
        """

        embeds = {}

        # ------------------------------------------------------
        # Example Embed #1
        # ------------------------------------------------------
        embed1 = discord.Embed(
            title="📘 Welcome to the Server",
            description=(
                "This is the main server information embed.\n"
                "Feel free to edit this text directly in the code.\n\n"
                "**Changes automatically update after bot restart.**"
            ),
            color=discord.Color.blue()
        )
        embed1.add_field(
            name="Rules",
            value="1. Be respectful.\n2. Follow guidelines.\n3. Enjoy your stay!",
            inline=False,
        )
        embed1.add_field(
            name="Helpful Links",
            value="[Website](https://example.com)\n[Support](https://example.com/support)",
            inline=False,
        )

        embeds[123456789012345678] = embed1  # <--- replace with your channel ID



        # ------------------------------------------------------
        # Example Embed #2 (separate embed in a different channel)
        # ------------------------------------------------------
        embed2 = discord.Embed(
            title="🎮 Game Information",
            description="A list of current game servers and schedules.\n\u200b",
            color=discord.Color.green()
        )
        embed2.add_field(
            name="Server Status",
            value="Online\nPlayers: 55/100",
            inline=False
        )
        embed2.add_field(
            name="Next Events",
            value="• Friday Op: 20:00 UTC\n• Training Night: 19:00 UTC",
            inline=False
        )

        embeds[234567890123456789] = embed2  # <--- replace with another channel ID

        return embeds

    # ==========================================================
    # POST/UPDATE LOGIC
    # ==========================================================
    async def post_or_update(self, channel_id: int, embed: discord.Embed):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            print(f"[EmbedManager] Channel {channel_id} not found.")
            return

        stored = self.embed_store.get(str(channel_id))

        # If previous message exists, fetch and compare
        if stored:
            try:
                msg = await channel.fetch_message(stored["message_id"])

                # Compare: if embed changed → update message
                if msg.embeds and msg.embeds[0].to_dict() != embed.to_dict():
                    print(f"[EmbedManager] Updating embed in channel {channel_id}")
                    await msg.edit(embed=embed)
                else:
                    print(f"[EmbedManager] Embed unchanged in channel {channel_id}")

                return

            except discord.NotFound:
                print(f"[EmbedManager] Old embed missing, sending new one.")

        # If no stored message or missing → send new embed
        new_msg = await channel.send(embed=embed)
        self.embed_store[str(channel_id)] = {"message_id": new_msg.id}
        save_embed_store(self.embed_store)
        print(f"[EmbedManager] Posted new embed to channel {channel_id}")

    # ==========================================================
    # COMMAND FOR TESTING (optional)
    # ==========================================================
    @commands.command(name="sync_embeds")
    async def sync_embeds(self, ctx):
        """Manually sync embeds to channels."""
        embeds = self.get_embeds()
        for channel_id, embed in embeds.items():
            await self.post_or_update(channel_id, embed)

        await ctx.send("Embeds synced.")

    # ==========================================================
    # AUTO-SYNC ON BOT READY
    # ==========================================================
    @commands.Cog.listener()
    async def on_ready(self):
        print("[EmbedManager] Bot ready — syncing persistent embeds.")
        embeds = self.get_embeds()

        for channel_id, embed in embeds.items():
            await self.post_or_update(channel_id, embed)


async def setup(bot):
    await bot.add_cog(EmbedManager(bot))