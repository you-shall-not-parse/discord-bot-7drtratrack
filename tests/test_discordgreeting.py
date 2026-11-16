import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from cogs.discordgreeting import DiscordGreeting, ROLE_DM_MESSAGES
from cogs.trainee_alert import DIPLOMAT_ALERT


class DiscordGreetingVideoTests(unittest.IsolatedAsyncioTestCase):
	def setUp(self) -> None:
		self.bot = MagicMock()
		self.cog = DiscordGreeting(self.bot)
		self.member = SimpleNamespace(id=123, send=AsyncMock())

	async def test_trainee_receives_video_before_greeting(self) -> None:
		with (
			patch("cogs.discordgreeting.TRAINEE_INTRO_VIDEO_PATH", Path(__file__)),
			patch("cogs.discordgreeting.discord.File", return_value=MagicMock()),
		):
			sent = await self.cog._safe_dm(
				self.member,
				"Infantry greeting",
				matched_role_name="Infantry Trainee",
			)

		self.assertTrue(sent)
		self.assertEqual(self.member.send.await_count, 2)
		self.assertIn("file", self.member.send.await_args_list[0].kwargs)
		self.assertEqual(self.member.send.await_args_list[1].args, ("Infantry greeting",))

	async def test_non_trainee_receives_no_video(self) -> None:
		sent = await self.cog._safe_dm(
			self.member,
			"Blueberry greeting",
			matched_role_name="Blueberry",
		)

		self.assertTrue(sent)
		self.member.send.assert_awaited_once_with("Blueberry greeting")

	async def test_video_upload_failure_does_not_block_greeting(self) -> None:
		response = SimpleNamespace(status=413, reason="Payload Too Large")
		self.member.send.side_effect = [
			discord.HTTPException(response, "attachment too large"),
			None,
		]
		with (
			patch("cogs.discordgreeting.TRAINEE_INTRO_VIDEO_PATH", Path(__file__)),
			patch("cogs.discordgreeting.discord.File", return_value=MagicMock()),
		):
			sent = await self.cog._safe_dm(
				self.member,
				"Recon greeting",
				matched_role_name="Recon Trainee",
			)

		self.assertTrue(sent)
		self.assertEqual(self.member.send.await_count, 2)


class DiplomatGreetingTests(unittest.IsolatedAsyncioTestCase):
	def setUp(self) -> None:
		self.bot = MagicMock()
		self.cog = DiscordGreeting(self.bot)
		self.member = SimpleNamespace(
			id=123, send=AsyncMock(), edit=AsyncMock(), guild=SimpleNamespace(id=456),
			roles=[SimpleNamespace(id=DIPLOMAT_ALERT.trainee_role_id, name="Renamed diplomat")],
		)
		self.dm = MagicMock(spec=discord.DMChannel)
		self.dm.send = AsyncMock()
		self.nco = MagicMock(spec=discord.TextChannel)
		self.nco.guild = self.member.guild
		self.nco.send = AsyncMock()
		self.bot.get_channel.return_value = self.nco

	def reply(self, content: str, *, user_id: int = 123):
		return SimpleNamespace(
			author=SimpleNamespace(id=user_id, bot=False), channel=self.dm, content=content,
		)

	async def test_delayed_role_collects_answers_and_sets_nickname_once(self) -> None:
		before = SimpleNamespace(id=123, roles=[])
		await self.cog.on_member_update(before, self.member)
		await self.cog.on_member_update(before, self.member)
		self.member.send.assert_awaited_once_with(ROLE_DM_MESSAGES["Diplomat"])
		self.assertIn("What clan are you in?", self.member.send.await_args.args[0])
		await self.cog.on_message(self.reply("Other user's clan", user_id=999))
		await self.cog.on_message(self.reply(""))
		self.nco.send.assert_not_awaited()
		await self.cog.on_message(self.reply("Example Clan"))
		self.assertIn("acronym", self.dm.send.await_args.args[0])
		await self.cog.on_message(self.reply("EC"))
		self.assertIn("What name do you go by?", self.dm.send.await_args.args[0])
		self.nco.send.assert_not_awaited()
		await self.cog.on_message(self.reply("Gerry"))
		await self.cog.on_message(self.reply("extra reply"))
		self.member.edit.assert_awaited_once_with(nick="[EC]Gerry", reason="Diplomat onboarding: clan acronym and preferred name")
		self.bot.get_channel.assert_called_once_with(1097920441022152774)
		self.nco.send.assert_awaited_once()
		content = self.nco.send.await_args.args[0]
		self.assertEqual(content.splitlines()[0], "<@123> has joined with the **Diplmat** role.")
		self.assertIn("<@123>", content)
		self.assertIn("Example Clan", content)
		self.assertIn("EC", content)
		self.assertIn("**Name:** Gerry", content)
		self.assertEqual(self.nco.send.await_args.kwargs["allowed_mentions"].to_dict(), discord.AllowedMentions.none().to_dict())
		self.assertNotIn(123, self.cog._diplomat_sessions)

	async def test_already_present_role_starts_flow(self) -> None:
		await self.cog._welcome_after_onboarding(self.member)
		self.assertIn(123, self.cog._diplomat_sessions)
		self.bot.get_cog.assert_not_called()

	async def test_closed_dms_do_not_create_session(self) -> None:
		self.member.send.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "DMs closed")
		await self.cog._welcome_after_onboarding(self.member)
		self.assertNotIn(123, self.cog._diplomat_sessions)

	async def test_failed_nco_post_keeps_answers_for_retry(self) -> None:
		await self.cog._welcome_after_onboarding(self.member)
		await self.cog.on_message(self.reply("Example Clan"))
		await self.cog.on_message(self.reply("EC"))
		self.nco.send.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "No access")
		await self.cog.on_message(self.reply("Gerry"))
		self.assertEqual(self.cog._diplomat_sessions[123].clan_name, "Example Clan")
		self.assertEqual(self.cog._diplomat_sessions[123].acronym, "EC")
		self.assertIn("try again", self.dm.send.await_args.args[0])
		self.nco.send.side_effect = None
		await self.cog.on_message(self.reply("Gerry"))
		self.assertNotIn(123, self.cog._diplomat_sessions)

	async def test_nickname_failure_still_reports_answers_to_nco(self) -> None:
		await self.cog._welcome_after_onboarding(self.member)
		await self.cog.on_message(self.reply("Example Clan"))
		await self.cog.on_message(self.reply("[OFIN]"))
		self.member.edit.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Hierarchy")
		await self.cog.on_message(self.reply("Gerry"))
		self.nco.send.assert_awaited_once()
		self.assertIn("couldn't update your nickname", self.nco.send.await_args.args[0])
		self.assertIn("couldn't update your nickname", self.dm.send.await_args.args[0])
		self.assertNotIn(123, self.cog._diplomat_sessions)

	async def test_long_nickname_is_rejected_without_truncating(self) -> None:
		await self.cog._welcome_after_onboarding(self.member)
		await self.cog.on_message(self.reply("Example Clan"))
		await self.cog.on_message(self.reply("OFIN"))
		await self.cog.on_message(self.reply("G" * 27))
		self.member.edit.assert_not_awaited()
		self.assertIn("1–26", self.dm.send.await_args.args[0])
		await self.cog.on_message(self.reply("G" * 26))
		self.assertEqual(len(self.member.edit.await_args.kwargs["nick"]), 32)

	async def test_leaving_clears_pending_answers(self) -> None:
		await self.cog._welcome_after_onboarding(self.member)
		await self.cog.on_member_remove(self.member)
		await self.cog.on_message(self.reply("Example Clan"))
		self.nco.send.assert_not_awaited()
		self.assertNotIn(123, self.cog._diplomat_sessions)


if __name__ == "__main__":
	unittest.main()
