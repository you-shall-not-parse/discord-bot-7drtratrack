import io
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from PIL import Image

from cogs.officer_info import CHANNEL_ID, MAIN_GUILD_ID, GuideReader, OfficerInfo, OfficerPanel, render_page
from cogs.nameshame import NameShame, NAMESHAME_REPORTER_ROLE_IDS, ReportFlowView


class GuideTests(unittest.IsolatedAsyncioTestCase):
    def test_actual_pdf_renders_first_and_last_pages(self):
        first, count = render_page(0)
        self.assertGreater(count, 0)
        last, _ = render_page(count - 1)
        for data in (first, last):
            with Image.open(io.BytesIO(data)) as image:
                self.assertGreater(image.width, 500)
                self.assertGreater(image.height, 500)
        with self.assertRaises(ValueError):
            render_page(count)

    async def test_panel_is_persistent(self):
        self.assertTrue(OfficerPanel().is_persistent())

    async def test_report_button_uses_loaded_nameshame(self):
        cog = SimpleNamespace(open_report=AsyncMock())
        interaction = SimpleNamespace(client=SimpleNamespace(get_cog=lambda name: cog))
        await OfficerPanel().report.callback(interaction)
        cog.open_report.assert_awaited_once_with(interaction)

    async def test_report_button_handles_unavailable_cog(self):
        interaction = SimpleNamespace(
            client=SimpleNamespace(get_cog=lambda name: None),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        await OfficerPanel().report.callback(interaction)
        self.assertIn("unavailable", interaction.response.send_message.call_args.args[0])

    async def test_report_flow_preserves_role_restrictions(self):
        member = MagicMock(spec=discord.Member)
        member.roles = []
        interaction = SimpleNamespace(
            guild_id=MAIN_GUILD_ID,
            user=member,
            response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()),
        )
        cog = SimpleNamespace()
        await NameShame.open_report(cog, interaction)
        self.assertIn("not allowed", interaction.response.send_message.call_args.args[0])
        member.roles = [SimpleNamespace(id=next(iter(NAMESHAME_REPORTER_ROLE_IDS)))]
        await NameShame.open_report(cog, interaction)
        kwargs = interaction.response.send_message.call_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        self.assertIsInstance(kwargs["view"], ReportFlowView)
        self.assertIs(kwargs["view"].cog, cog)

    async def test_publish_reuses_existing_message_after_restart(self):
        channel = MagicMock(spec=discord.TextChannel)
        channel.guild = SimpleNamespace(id=MAIN_GUILD_ID)
        message = SimpleNamespace(id=123, edit=AsyncMock())
        channel.send = AsyncMock(return_value=message)
        channel.fetch_message = AsyncMock(return_value=message)
        bot = SimpleNamespace(get_channel=lambda _: channel)
        with tempfile.TemporaryDirectory() as directory, patch("cogs.officer_info.STATE_PATH", str(Path(directory) / "state.json")), patch.dict("os.environ", {"OFFICER_WEBSITE_PIN": "test-pin"}):
            await OfficerInfo(bot).publish()
            await OfficerInfo(bot).publish()
        channel.send.assert_awaited_once()
        channel.fetch_message.assert_awaited_once_with(123)
        message.edit.assert_awaited_once()

    async def test_navigation_and_independent_readers(self):
        interaction = SimpleNamespace(
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )
        reader = GuideReader()
        other = GuideReader()
        with patch("cogs.officer_info.render_page", return_value=(b"png", 2)):
            await reader.display(interaction, first=True)
            self.assertTrue(reader.previous.disabled)
            self.assertFalse(reader.next.disabled)
            await reader.display(interaction, 1)
            self.assertEqual(reader.page, 1)
            self.assertTrue(reader.next.disabled)
            self.assertFalse(reader.previous.disabled)
            self.assertEqual(other.page, 0)
            await reader.display(interaction, -1)
            self.assertEqual(reader.page, 0)

    async def test_missing_pdf_reports_error(self):
        interaction = SimpleNamespace(
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        with patch("cogs.officer_info.render_page", side_effect=FileNotFoundError), self.assertLogs("cogs.officer_info", level="ERROR"):
            await GuideReader().display(interaction, first=True)
        self.assertIn("could not be opened", interaction.followup.send.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
