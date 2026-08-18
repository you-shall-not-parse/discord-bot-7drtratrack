import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.reaction_roles import (
    BirthdayActionSelect,
    COMMUNITY_TEAM_ROLES,
    ReactionRoleView,
    ReactionRoles,
    _birthday_display,
    _parse_birthday_input,
)


class ReactionRoleBirthdayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.today = date(2026, 8, 8)

    def test_parses_birthday_with_public_year(self) -> None:
        stored, show_age, error = _parse_birthday_input(
            "15 06 1995",
            today=self.today,
        )

        self.assertIsNone(error)
        self.assertEqual(stored, "15/06/1995")
        self.assertTrue(show_age)

    def test_blank_year_is_private(self) -> None:
        stored, show_age, error = _parse_birthday_input("15 06", today=self.today)

        self.assertIsNone(error)
        self.assertEqual(stored, "15/06")
        self.assertFalse(show_age)
        self.assertEqual(
            _birthday_display(stored, show_age=show_age, today=self.today),
            "15 June",
        )

    def test_blank_year_allows_leap_day(self) -> None:
        stored, show_age, error = _parse_birthday_input("29 02", today=self.today)

        self.assertIsNone(error)
        self.assertEqual(stored, "29/02")
        self.assertFalse(show_age)

    def test_rejects_invalid_date(self) -> None:
        stored, show_age, error = _parse_birthday_input("31 02 2000", today=self.today)

        self.assertIsNone(stored)
        self.assertFalse(show_age)
        self.assertIn("valid", error or "")

    async def test_view_contains_birthday_dropdown(self) -> None:
        view = ReactionRoleView(SimpleNamespace())

        birthday_selects = [
            child for child in view.children if isinstance(child, BirthdayActionSelect)
        ]
        self.assertEqual(len(birthday_selects), 1)
        self.assertEqual(
            birthday_selects[0].custom_id,
            "reaction_roles:birthday_actions",
        )

    async def test_view_contains_building_inspector_dropdown(self) -> None:
        view = ReactionRoleView(SimpleNamespace())

        team_selects = [
            child
            for child in view.children
            if getattr(child, "custom_id", None) == "reaction_roles:community_team"
        ]
        self.assertEqual(len(team_selects), 1)
        self.assertEqual(
            COMMUNITY_TEAM_ROLES["registered_building_inspector"][1],
            1103588562714251264,
        )

    def test_directory_embed_explains_private_year(self) -> None:
        payload = ReactionRoles.build_embed().to_dict()
        fields = {field["name"]: field["value"] for field in payload["fields"]}

        self.assertEqual(payload["title"], "Role & Birthday Directory")
        self.assertIn("`DD MM`", fields["Birthday Manager"])
        self.assertIn("private", fields["Birthday Manager"])
        self.assertIn("<@&1103588562714251264>", fields["Community Teams"])
        self.assertIn("building-code compliance", fields["Community Teams"])

    async def test_legacy_manager_message_delete_uses_supported_arguments(self) -> None:
        bot_user = object()
        message = SimpleNamespace(
            author=bot_user,
            embeds=[SimpleNamespace(title="🎂 Birthday Manager 🎂")],
            delete=AsyncMock(),
        )

        class FakeTextChannel:
            async def history(self, *, limit: int):
                self.requested_limit = limit
                yield message

        channel = FakeTextChannel()
        guild = SimpleNamespace(get_channel=lambda _channel_id: channel)
        cog = ReactionRoles.__new__(ReactionRoles)
        cog.bot = SimpleNamespace(guilds=[guild], user=bot_user)

        with patch("cogs.reaction_roles.discord.TextChannel", FakeTextChannel):
            await cog._remove_legacy_birthday_manager()

        message.delete.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
