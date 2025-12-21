import discord
from discord.ext import commands
from discord import app_commands

import asyncio
import aiofiles
import subprocess
import os
import uuid

# ================== CONFIG ==================
GUILD_ID = 1097913605082579024

OUTPUT_EXT = "mp4"              # mp4 | mov | webm
FADE_DURATION = 3.0             # seconds
OUTRO_VIDEO_PATH = os.path.join(os.path.dirname(__file__), "gohammfiles", "hammvideo - Trim.mp4")
TEMP_DIR = os.path.join(os.path.dirname(__file__), "gohammfiles", "temp_videos")

MAX_CONCURRENT_JOBS = 1         # DO NOT raise unless you know your CPU
# ============================================


# ---------- Utility ----------
def get_duration(path: str) -> float:
    """Return video duration in seconds using ffprobe"""
    result = subprocess.run(
        [
            "/usr/bin/ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path
        ],
        capture_output=True,
        text=True
    )
    return float(result.stdout.strip())


def get_dimensions(path: str) -> tuple[int, int]:
    """Return video dimensions (width, height) using ffprobe"""
    result = subprocess.run(
        [
            "/usr/bin/ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            path
        ],
        capture_output=True,
        text=True
    )
    width, height = result.stdout.strip().split(',')
    return int(width), int(height)


# ---------- Job container ----------
class GoHammJob:
    def __init__(self, interaction: discord.Interaction, attachment: discord.Attachment):
        self.interaction = interaction
        self.attachment = attachment


# ================== COG ==================
class GoHammThis(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.queue: asyncio.Queue[GoHammJob] = asyncio.Queue()
        self.worker_tasks: list[asyncio.Task] = []

        os.makedirs(TEMP_DIR, exist_ok=True)

    # -------- Start workers when cog loads --------
    async def cog_load(self):
        for _ in range(MAX_CONCURRENT_JOBS):
            self.worker_tasks.append(asyncio.create_task(self.worker()))

    # -------- Cleanup on unload --------
    async def cog_unload(self):
        for task in self.worker_tasks:
            task.cancel()

    # -------- Background worker --------
    async def worker(self):
        while True:
            job = await self.queue.get()
            try:
                await self.process_job(job)
            except Exception as e:
                try:
                    await job.interaction.followup.send(
                        f"❌ Processing failed:\n```{e}```",
                        ephemeral=True
                    )
                except:
                    pass
            finally:
                self.queue.task_done()

    # -------- Core processing --------
    async def process_job(self, job: GoHammJob):
        interaction = job.interaction
        video = job.attachment

        uid = uuid.uuid4().hex
        input_path = f"{TEMP_DIR}/{uid}_input"
        output_path = f"{TEMP_DIR}/{uid}_output.{OUTPUT_EXT}"

        # Save uploaded video
        async with aiofiles.open(input_path, "wb") as f:
            await f.write(await video.read())

        # Calculate fade timing
        duration = get_duration(input_path)
        fade_start = max(duration - FADE_DURATION, 0)

        # Get dimensions
        input_width, input_height = get_dimensions(input_path)
        outro_width, outro_height = get_dimensions(OUTRO_VIDEO_PATH)

        # Build filter_complex with scaling if dimensions differ
        if (input_width, input_height) == (outro_width, outro_height):
            # Same dimensions, no scaling needed
            filter_complex = (
                f"[0:v]fps=30[v0];[1:v]fps=30[v1];"
                f"[v0][v1]"
                f"xfade=transition=fade:"
                f"duration={FADE_DURATION}:offset={fade_start}[v];"
                f"[0:a][1:a]"
                f"acrossfade=d={FADE_DURATION}[a]"
            )
        else:
            # Different dimensions, scale outro to match input
            filter_complex = (
                f"[0:v]fps=30[v0];[1:v]scale={input_width}:{input_height},fps=30[v1];"
                f"[v0][v1]"
                f"xfade=transition=fade:"
                f"duration={FADE_DURATION}:offset={fade_start}[v];"
                f"[0:a][1:a]"
                f"acrossfade=d={FADE_DURATION}[a]"
            )

        # FFmpeg crossfade command
        cmd = [
            "/usr/bin/ffmpeg",
            "-i", input_path,
            "-i", OUTRO_VIDEO_PATH,
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-map", "[a]",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-profile:v", "main",
            "-level:v", "4.0",
            "-movflags", "+faststart",
            "-c:a", "aac",
            "-b:a", "128k",
            "-y",
            output_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"FFmpeg error:\n{result.stderr}")

        # Send result
        await interaction.followup.send(
            content="🔥 **Hamm’d.**",
            file=discord.File(output_path)
        )

        # Cleanup
        try:
            os.remove(input_path)
            os.remove(output_path)
        except:
            pass

    # -------- Slash command (enqueue only) --------
    @app_commands.command(
        name="gohammthis",
        description="Append the Hamm outro with a smooth fade transition"
    )
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def gohammthis(
        self,
        interaction: discord.Interaction,
        video: discord.Attachment
    ):
        if not video.content_type or not video.content_type.startswith("video"):
            return await interaction.response.send_message(
                "❌ Please upload a valid video file.",
                ephemeral=True
            )

        await interaction.response.defer(thinking=True, ephemeral=True)

        job = GoHammJob(interaction, video)
        await self.queue.put(job)

        position = self.queue.qsize()

        await interaction.followup.send(
            f"📥 Added to queue.\n"
            f"⏳ Position: **{position}**",
        )


# -------- Cog setup --------
async def setup(bot: commands.Bot):
    await bot.add_cog(GoHammThis(bot))
