import asyncio
import logging
from typing import Optional

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

# =============================
# DM TEXT CONFIG (EDIT THIS)
# =============================
# Put your onboarding role NAMES here (must match exactly).
# The first matching role found on the member will be used.
ROLE_DM_MESSAGES: dict[str, str] = {
	# Example:
	"Infantry Trainee": " Welcome! Since you chose Infantry trainee...",
	"Tank Crew Trainee": "Welcome! Since you chose Armour trainee...",
	"Recon Trainee": "Welcome! Since you chose Recon trainee...",
	"Blueberry": "**Welcome! Since you chose Blueberry...** \n\n" 
	"Check out [#❓｜community-info-and-faq](https://discord.com/channels/1097913605082579024/1441744889145720942) and join us in [#🫐｜community-chat](https://discord.com/channels/1097913605082579024/1441511200474271875) for HLL chatter, in [#🕹️｜the-arcade](https://discord.com/channels/1097913605082579024/1398672228803018763)  for other games or in [#🧙🏻‍♂️｜side-quests](https://discordapp.com/channels/1097913605082579024/1399082728313458778) for hobbies.\n\n"
    "👋 We recommend you add your current T17 HLL in-game name with the # numbers after it in [#team-17-names](https://discord.com/channels/1097913605082579024/1098665953706909848) so we can identify each other!\n\n"
    "😲 We also have [#reaction-roles](https://discord.com/channels/1097913605082579024/1099248200776421406) channel for you to add your own server roles for in game rank.\n\n"
	"Interested in joining us? Click the button in [#recruitform-requests](https://discord.com/channels/1097913605082579024/1401634001248190515)\n\n",
	"You can also map vote here [#🗺️｜map-voting](https://discord.com/channels/1097913605082579024/1441751747935735878)\n\n"
	"Diplomat": "**Welcome! Since you chose Diplomat...** \n\n" 
	"👋 Check out [#❓｜community-info-and-faq](https://discord.com/channels/1097913605082579024/1441744889145720942) and join us in [#🫐｜community-chat](https://discord.com/channels/1097913605082579024/1441511200474271875) for HLL chatter, in [#🕹️｜the-arcade](https://discord.com/channels/1097913605082579024/1398672228803018763) for other games or in [#🧙🏻‍♂️｜side-quests](https://discordapp.com/channels/1097913605082579024/1399082728313458778) for hobbies.\n\n"
	"You can see all of our upcoming events in [#upcoming-events-calendar](https://discord.com/channels/1097913605082579024/1332736267485708419) to plan and organise events with us! \n\n"
    "You can also map vote here [#🗺️｜map-voting](https://discord.com/channels/1097913605082579024/1441751747935735878)\n\n",
}

# If the member's matched onboarding role is in this set, the bot will also
# automatically start the Recruit Form DM flow (from cogs/recruitform.py).
RECRUIT_FORM_TRIGGER_ROLES: set[str] = {
	# Put 3 of your 5 onboarding role names here.
	# Example: "Infantry",
	"Infantry Trainee",
    "Recon Trainee",
	"Tank Crew Trainee",
}

# If none of the roles above are found after waiting, this message is used.
DEFAULT_DM_MESSAGE = (
	"Welcome! Please complete onboarding and pick a role. "
	"If you don’t receive the right DM, ping an admin in #entree-chat and they'll assist you."
)

# How long to wait for Discord Onboarding to apply roles (seconds)
MAX_WAIT_FOR_ONBOARDING_ROLE_SECONDS = 10 * 60

# Poll interval while waiting (seconds)
ROLE_POLL_INTERVAL_SECONDS = 5


class DiscordGreeting(commands.Cog):
	def __init__(self, bot: commands.Bot):
		self.bot = bot
		self._already_dmed: set[int] = set()
		self._welcome_tasks: dict[int, asyncio.Task] = {}
		self._dm_locks: dict[int, asyncio.Lock] = {}

	def _get_dm_lock(self, user_id: int) -> asyncio.Lock:
		lock = self._dm_locks.get(user_id)
		if lock is None:
			lock = asyncio.Lock()
			self._dm_locks[user_id] = lock
		return lock

	def _cancel_welcome_task(self, user_id: int) -> None:
		task = self._welcome_tasks.pop(user_id, None)
		if task and not task.done():
			task.cancel()

	def _pick_role_and_message(self, member: discord.Member) -> Optional[tuple[str, str]]:
		if not ROLE_DM_MESSAGES:
			return None

		member_role_names = {r.name for r in member.roles}
		for role_name, message in ROLE_DM_MESSAGES.items():
			if role_name in member_role_names:
				return role_name, message
		return None

	def _maybe_start_recruit_form(self, member: discord.Member, matched_role_name: str) -> None:
		if not RECRUIT_FORM_TRIGGER_ROLES:
			return
		if matched_role_name not in RECRUIT_FORM_TRIGGER_ROLES:
			return

		recruit_cog = self.bot.get_cog("RecruitFormCog")
		if recruit_cog is None:
			logger.warning(
				"RecruitFormCog not loaded; cannot auto-start recruit form for %s (%s).",
				member,
				member.id,
			)
			return

		starter = getattr(recruit_cog, "start_form_session", None)
		if starter is None:
			logger.warning(
				"RecruitFormCog has no start_form_session(); cannot auto-start recruit form for %s (%s).",
				member,
				member.id,
			)
			return

		try:
			started = starter(member)
			if started:
				logger.info("Auto-started recruit form for %s (%s)", member, member.id)
		except Exception as e:
			logger.warning(
				"Failed to auto-start recruit form for %s (%s): %s",
				member,
				member.id,
				e,
			)

	async def _safe_dm(self, member: discord.Member, message: str) -> bool:
		lock = self._get_dm_lock(member.id)
		async with lock:
			# Re-check inside the lock so join-task + member_update cannot double-send.
			if member.id in self._already_dmed:
				return False
			try:
				await member.send(message)
				self._already_dmed.add(member.id)
				return True
			except discord.Forbidden:
				logger.info("Cannot DM member %s (%s): DMs closed.", member, member.id)
				return False
			except discord.HTTPException as e:
				logger.warning("Failed to DM member %s (%s): %s", member, member.id, e)
				return False

	async def _welcome_after_onboarding(self, member: discord.Member) -> None:
		# Avoid double-sends from join + role update events.
		if member.id in self._already_dmed:
			return
		try:
			waited = 0
			picked = self._pick_role_and_message(member)
			while picked is None and waited < MAX_WAIT_FOR_ONBOARDING_ROLE_SECONDS:
				await asyncio.sleep(ROLE_POLL_INTERVAL_SECONDS)
				waited += ROLE_POLL_INTERVAL_SECONDS

				# Member object may be stale; refetch from guild to see latest roles.
				try:
					fresh = await member.guild.fetch_member(member.id)
				except discord.HTTPException:
					fresh = member

				picked = self._pick_role_and_message(fresh)

			matched_role_name: Optional[str] = None
			matched_message: Optional[str] = None
			if picked is not None:
				matched_role_name, matched_message = picked

			final_message = matched_message or DEFAULT_DM_MESSAGE
			sent = await self._safe_dm(member, final_message)
			if sent and matched_role_name:
				self._maybe_start_recruit_form(member, matched_role_name)
		except asyncio.CancelledError:
			# Cancelled because another path (e.g. member_update) already handled it.
			return
		finally:
			self._welcome_tasks.pop(member.id, None)

	@commands.Cog.listener()
	async def on_member_join(self, member: discord.Member):
		# Allow leave/rejoin testing (or genuine rejoins) to receive DMs again.
		self._already_dmed.discard(member.id)
		self._cancel_welcome_task(member.id)
		# Note: we intentionally don't try to cancel any prior tasks here; those
		# would be from a previous join and will naturally no-op or time out.
		# Discord Onboarding roles are often applied *after* join, so we wait/poll.
		task = asyncio.create_task(self._welcome_after_onboarding(member))
		self._welcome_tasks[member.id] = task

	@commands.Cog.listener()
	async def on_member_remove(self, member: discord.Member):
		# If they leave and rejoin later, they should be eligible to receive the DM again.
		self._already_dmed.discard(member.id)
		self._cancel_welcome_task(member.id)

	@commands.Cog.listener()
	async def on_member_update(self, before: discord.Member, after: discord.Member):
		# If onboarding role gets applied after join and the poll hasn't sent yet,
		# this gives us a second chance to send immediately.
		if after.id in self._already_dmed:
			return

		if ROLE_DM_MESSAGES:
			before_names = {r.name for r in before.roles}
			after_names = {r.name for r in after.roles}
			newly_added = after_names - before_names
			if any(role_name in newly_added for role_name in ROLE_DM_MESSAGES.keys()):
				picked = self._pick_role_and_message(after)
				if picked:
					matched_role_name, matched_message = picked
					sent = await self._safe_dm(after, matched_message)
					if sent:
						self._cancel_welcome_task(after.id)
						self._maybe_start_recruit_form(after, matched_role_name)


async def setup(bot: commands.Bot):
	await bot.add_cog(DiscordGreeting(bot))
