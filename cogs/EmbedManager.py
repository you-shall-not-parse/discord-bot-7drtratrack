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
        Each block is a dict: {"key": ..., "channel_id": ..., "embed": discord.Embed}
        """

        blocks = []

        # ---------------- EMBED 1 ----------------
        embed1 = discord.Embed(
            title=":rat: About Us :rat:",
            description="",
            color=discord.Color.red()
        )
        embed1.set_image(url="https://cdn.discordapp.com/attachments/1098976074852999261/1448099075143503922/file_0000000040dc7208b0cf42742a355373.png?ex=693a06db&is=6938b55b&hm=556b01728256b65e7acbf84c74160f663f968ca560667f16cb1aa88630b8b165")
        
        embed1.add_field(
            name="Who are We?",
            value=(
                "We're 7DR, a military simulation (milsim) EU/UK and US Hell Let Loose console clan "
                "that models itself on the 7th Armoured Division, a real British armed forces unit "
                "that fought throughout both World Wars. For more information on that division, "
                "see <#1098337552194351114>!\n"
                "We run organised Hell Let Loose casual and competitive clan scrims every week, and we maintain an active server."
            ),
            inline=False
        )
        embed1.add_field(
            name="Links",
            value="[Linktr.ee](https://linktr.ee/7drc)",
            inline=False
        )
        blocks.append({
            "key": "about_us",
            "channel_id": 1441744889145720942,
            "embed": embed1
        })

        # ---------------- EMBED 2 ----------------
        embed2 = discord.Embed(
            title="Frequently Asked Questions (FAQs)",
            description="",
            color=discord.Color.red()
        )
        embed2.add_field(
            name=":question: How do I join 7DR?",
            value=(
                "You must first complete a recruit form to be considered.\n"
                "Direct-entry trainees will have recieved a message from RatBot. If you missed this or you're a blueberry, please press the Start Application button in <#1401634001248190515> and we'll get back to you!\n"
                "If accepted, you will join a school of your choosing and once you complete your basic training (aim-shoot-communicate) you will be assigned to a unit."
            ),
            inline=False
        )
        embed2.add_field(
            name=":question: How do I find a squad?",
            value=(
                "Drop a message in <#1099090838203666474> to link up with clan members or non-clan members (blueberries)\n"
                "Keep an eye on server seeding events in <#1441511200474271875> channel too!"
            ),
            inline=False
        )
        embed2.add_field(
            name=":question: How do I see my in-game stats?",
            value=(
                "[Here are our HLL server stats](https://7dr-stats.hlladmin.com/games), you can also delve into a stats tool which goes across multiple servers here https://hllrecords.com/"
            ),
            inline=False
        )
        embed2.add_field(
            name=":question: How do I vote for a map?",
            value=(
                "You can vote for maps in <#1441751747935735878>!"
            ),
            inline=False
        )
        embed2.add_field(
            name=":question: How do I report a player?",
            value=(
                "Please contact a member of the clan in the <#1441511200474271875> channel and they will contact a server admin to investigate."
            ),
            inline=False
        )
        blocks.append({
            "key": "faq",
            "channel_id": 1441744889145720942,
            "embed": embed2
        })

        # ---------------- EMBED 3 ----------------
        embed3 = discord.Embed(
            title="Community Server Directory",
            description="",
            color=discord.Color.red()
        )
        embed3.description = (
            "This directory shows the channels you can see as a non‑clan member: **Blueberry (B)** or **Diplomat (D)**.\n"
            "Diplomat access is mostly informational for organising events with us, Blueberry is more fun-related... You can select both also!\n"
            "Direct‑entry trainees and clan members will gain more access after onboarding and role assignment."
        )

        embed3.add_field(
            name=":pencil:｜Information",
            value=(
            "- <#1098337552194351114> [B][D]\n"
            "- <#1296885095138852967> [B][D]\n"
            "- <#1098525492631572564> [B][D]\n"
            "- <#1098316982459314279> [B]\n"
            "- <#1099248200776421406> [B]\n"
            "- <#1441744889145720942> [B][D]\n"
            "- <#1441751747935735878> [B][D]\n"
            "- <#1332736267485708419> [D]"
            ),
            inline=False
        )

        embed3.add_field(
            name=":military_helmet:｜Recruits",
            value=(
            "- <#1098330967166419055> [B][D]\n"
            "- <#1401634001248190515> [B]\n"
            "- <#1098665953706909848> [B]"
            ),
            inline=False
        )

        embed3.add_field(
            name=":speech_balloon:｜General",
            value=(
            "- <#1441511200474271875> [B][D]\n"
            "- <#1099090838203666474> [B]\n"
            "- <#1097913605539774485> [B][D]\n"
            "- <#1106900027659522108> [B][D]\n"
            "- <#1114905902651285565> [B][D]\n"
            "- <#1398672228803018763> [B][D]\n"
            "- <#1399082728313458778> [B][D]"
            ),
            inline=False
        )

        embed3.add_field(
            name=":speaker:｜Voice Chat",
            value="- <#1409966793321091255> [B]",
            inline=False
        )

        embed3.add_field(
            name=":shirt:｜Merchandise",
            value="- <#1212477923542704188> [B][D]",
            inline=False
        )
        blocks.append({
            "key": "ServDir",
            "channel_id": 1441744889145720942,
            "embed": embed3
        })

        return blocks

    # ---------------- SYNC LOGIC ----------------
    async def sync_embed_block(self, block):
        channel = self.bot.get_channel(block["channel_id"])
        if channel is None:
            print(f"[EmbedManager] Channel {block['channel_id']} not found.")
            return

        key = block["key"]
        embed_to_post = block["embed"]
        stored_id = self.data.get(key)

        msg = None
        if stored_id:
            try:
                msg = await channel.fetch_message(stored_id)
            except discord.NotFound:
                print(f"[EmbedManager] Previous embed '{key}' missing, will post new.")

        if msg and msg.embeds and msg.embeds[0].to_dict() != embed_to_post.to_dict():
            print(f"[EmbedManager] Updating embed '{key}' in channel {channel.id}")
            await msg.edit(embed=embed_to_post)
        elif msg:
            print(f"[EmbedManager] Embed '{key}' unchanged in channel {channel.id}")
        else:
            new_msg = await channel.send(embed=embed_to_post)
            self.data[key] = new_msg.id
            print(f"[EmbedManager] Posted new embed '{key}' to channel {channel.id}")

        save_data(self.data)

    async def sync_all_embeds(self):
        blocks = self.get_embed_blocks()
        for block in blocks:
            await self.sync_embed_block(block)

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
