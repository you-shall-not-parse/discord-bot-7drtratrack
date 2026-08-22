import unittest
from types import SimpleNamespace

from cogs.infantry_trainee_alert import (
    TRAINER_ROLE_NAME,
    InfantryTraineeAlert,
    _alert_content,
    _can_test_alert,
    _has_role,
)


class InfantryTraineeAlertTests(unittest.TestCase):
    def test_role_check_requires_exact_role_name(self) -> None:
        trainer = SimpleNamespace(roles=[SimpleNamespace(name=TRAINER_ROLE_NAME)])
        wrong_case = SimpleNamespace(roles=[SimpleNamespace(name=TRAINER_ROLE_NAME.lower())])

        self.assertTrue(_has_role(trainer, TRAINER_ROLE_NAME))
        self.assertFalse(_has_role(wrong_case, TRAINER_ROLE_NAME))

    def test_alert_mentions_trainee_and_trainer_role_without_button_text(self) -> None:
        content = _alert_content(123, 456)

        self.assertIn("<@123>", content)
        self.assertIn("<@&456>", content)
        self.assertNotIn("Click to DM", content)

    def test_test_alert_is_clearly_labelled(self) -> None:
        content = _alert_content(123, 456, is_test=True)

        self.assertIn("TEST ALERT", content)
        self.assertIn("<@123>", content)
        self.assertIn("<@&456>", content)

    def test_trainer_can_run_test(self) -> None:
        member = SimpleNamespace(
            roles=[SimpleNamespace(name=TRAINER_ROLE_NAME)],
            guild_permissions=SimpleNamespace(manage_guild=False, administrator=False),
        )
        self.assertTrue(_can_test_alert(member))

    def test_manage_guild_can_run_test(self) -> None:
        member = SimpleNamespace(
            roles=[],
            guild_permissions=SimpleNamespace(manage_guild=True, administrator=False),
        )
        self.assertTrue(_can_test_alert(member))

    def test_regular_member_cannot_run_test(self) -> None:
        member = SimpleNamespace(
            roles=[],
            guild_permissions=SimpleNamespace(manage_guild=False, administrator=False),
        )
        self.assertFalse(_can_test_alert(member))

    def test_test_slash_command_name(self) -> None:
        command = InfantryTraineeAlert.test_infantry_trainee_alert
        self.assertEqual(command.name, "test_infantry_trainee_alert")


if __name__ == "__main__":
    unittest.main()
