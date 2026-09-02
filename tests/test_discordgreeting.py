import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from cogs.discordgreeting import DiscordGreeting


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


if __name__ == "__main__":
	unittest.main()
