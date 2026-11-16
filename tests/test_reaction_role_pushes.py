import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from cogs.reaction_roles import (
    MAIN_GUILD_ID, PLAY_STYLE_ROLES, PUSH_CATEGORIES,
    ReactionRoles, ReactionRolePushView,
)


class ReactionRolePushTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch("cogs.reaction_roles.BIRTHDAY_DB_PATH", str(Path(self.temp.name) / "state.db"))
        self.state_patch = patch("cogs.reaction_roles._load_state", return_value={})
        self.db_patch.start()
        self.state_patch.start()
        self.bot = MagicMock()
        self.cog = ReactionRoles(self.bot)
        self.member = MagicMock(spec=discord.Member)
        self.member.id = 123
        self.member.roles = [SimpleNamespace(name="Administration")]
        self.guild = SimpleNamespace(id=MAIN_GUILD_ID)
        self.interaction = SimpleNamespace(
            guild=self.guild, user=self.member,
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        self.message = SimpleNamespace(id=456, jump_url="https://discord.com/example", edit=AsyncMock())
        self.channel = MagicMock(spec=discord.TextChannel)
        self.channel.id = 789
        self.channel.guild = self.guild
        self.channel.permissions_for.return_value = SimpleNamespace(view_channel=True, send_messages=True)
        self.channel.send = AsyncMock(return_value=self.message)
        self.channel.fetch_message = AsyncMock(return_value=self.message)
        self.bot.get_channel.return_value = self.channel

    async def asyncTearDown(self) -> None:
        self.cog.cog_unload()
        self.db_patch.stop()
        self.state_patch.stop()
        self.temp.cleanup()

    async def push(self, category="play_style") -> None:
        await ReactionRoles.push_reaction_roles.callback(self.cog, self.interaction, category, self.channel)

    async def test_administration_can_post_one_section_for_exactly_one_week(self) -> None:
        now = discord.utils.utcnow()
        with patch("cogs.reaction_roles.discord.utils.utcnow", return_value=now):
            await self.push()
        self.channel.send.assert_awaited_once()
        args = self.channel.send.await_args.kwargs
        self.assertEqual(args["embed"].title, "Play Style")
        self.assertIn("Flanking Attack", args["embed"].description)
        self.assertIn("Frontdoor", args["embed"].description)
        self.assertEqual(len(args["view"].children), 1)
        self.assertIs(args["view"].children[0].role_choices, PLAY_STYLE_ROLES)
        row = self.cog._birthday_db.execute("SELECT * FROM reaction_role_pushes").fetchone()
        self.assertEqual(row, (456, 789, MAIN_GUILD_ID, "play_style", int((now + timedelta(weeks=1)).timestamp())))
        self.assertEqual(self.cog.state, {})

    async def test_without_administration_role_post_is_denied(self) -> None:
        self.member.roles = [SimpleNamespace(name="Officer")]
        await self.push()
        self.channel.send.assert_not_awaited()
        self.assertIn("Administration", self.interaction.response.send_message.await_args.args[0])

    async def test_other_guild_and_inaccessible_channel_are_denied(self) -> None:
        self.channel.guild = SimpleNamespace(id=MAIN_GUILD_ID + 1)
        await self.push()
        self.channel.send.assert_not_awaited()
        self.channel.guild = self.guild
        self.channel.permissions_for.return_value = SimpleNamespace(view_channel=False, send_messages=False)
        await self.push()
        self.channel.send.assert_not_awaited()

    async def test_post_failure_does_not_create_saved_reminder(self) -> None:
        self.channel.send.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "No access")
        await self.push()
        self.assertEqual(self.cog._birthday_db.execute("SELECT * FROM reaction_role_pushes").fetchall(), [])
        self.assertIn("couldn't post", self.interaction.followup.send.await_args.args[0])

    async def test_each_child_panel_is_available(self) -> None:
        for category in PUSH_CATEGORIES:
            view = ReactionRolePushView(self.cog, category, 9999999999)
            self.assertEqual(len(view.children), 1)
            self.assertEqual(view.children[0].custom_id, f"reaction_roles:push:{category}")
            view.stop()

    async def test_expiry_survives_restart_and_blocks_clicks_before_cleanup(self) -> None:
        await self.push()
        expiry = self.cog._push_views[456].expires_at
        self.cog.cog_unload()
        self.cog = ReactionRoles(self.bot)
        view = self.cog._push_views[456]
        self.assertEqual(view.expires_at, expiry)
        now = discord.utils.utcnow()
        with patch("cogs.reaction_roles.discord.utils.utcnow", return_value=now):
            self.assertTrue(await view.interaction_check(self.interaction))
        after_expiry = now + timedelta(weeks=1, seconds=1)
        with patch("cogs.reaction_roles.discord.utils.utcnow", return_value=after_expiry):
            self.assertFalse(await view.interaction_check(self.interaction))
            await self.cog.expire_role_pushes()
        self.message.edit.assert_awaited_once()
        self.assertIsNone(self.message.edit.await_args.kwargs["view"])
        self.assertEqual(self.cog._birthday_db.execute("SELECT * FROM reaction_role_pushes").fetchall(), [])
        self.assertNotIn(456, self.cog._push_views)

    async def test_failed_expiry_edit_keeps_expired_guard_for_retry(self) -> None:
        await self.push()
        self.message.edit.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "No access")
        with patch("cogs.reaction_roles.discord.utils.utcnow", return_value=discord.utils.utcnow() + timedelta(weeks=2)):
            await self.cog.expire_role_pushes()
            self.assertIn(456, self.cog._push_views)
            self.assertFalse(await self.cog._push_views[456].interaction_check(self.interaction))
        self.assertIsNotNone(self.cog._birthday_db.execute("SELECT * FROM reaction_role_pushes").fetchone())

    async def test_master_and_push_replace_the_same_category_roles(self) -> None:
        class Role:
            managed = False

            def __init__(self, role_id, position=1):
                self.id, self.position = role_id, position

            def __ge__(self, other):
                return self.position >= other.position

        roles = {role_id: Role(role_id) for _, role_id, _ in PLAY_STYLE_ROLES.values()}
        self.guild.me = SimpleNamespace(guild_permissions=SimpleNamespace(manage_roles=True), top_role=Role(99, 10))
        self.guild.get_role = roles.get
        self.member.roles = [roles[PLAY_STYLE_ROLES["attacker"][1]]]
        self.member.add_roles = AsyncMock()
        self.member.remove_roles = AsyncMock()
        push = ReactionRolePushView(self.cog, "play_style", 9999999999)
        control = push.children[0]
        control._values = ["flexi"]
        await control.callback(self.interaction)
        self.assertIs(self.member.add_roles.await_args.args[0], roles[PLAY_STYLE_ROLES["flexi"][1]])
        self.assertIs(self.member.remove_roles.await_args.args[0], roles[PLAY_STYLE_ROLES["attacker"][1]])
        self.member.roles = [roles[PLAY_STYLE_ROLES["flexi"][1]]]
        master_control = self.cog._view.children[1]
        master_control._values = ["attacker"]
        await master_control.callback(self.interaction)
        self.assertIs(self.member.add_roles.await_args.args[0], roles[PLAY_STYLE_ROLES["attacker"][1]])
        self.assertIs(self.member.remove_roles.await_args.args[0], roles[PLAY_STYLE_ROLES["flexi"][1]])
        push.stop()


if __name__ == "__main__":
    unittest.main()
