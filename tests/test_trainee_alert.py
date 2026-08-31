import unittest
from types import SimpleNamespace

from cogs.trainee_alert import (
    INFANTRY_ALERT,
    TANK_CREW_ALERT,
    TraineeAlert,
    _alert_content,
    _can_test_alert,
    _has_role,
    _has_trainee_role,
)


class TraineeAlertTests(unittest.TestCase):
    def test_infantry_role_check_requires_exact_role_name(self) -> None:
        trainer = SimpleNamespace(roles=[SimpleNamespace(name=INFANTRY_ALERT.trainer_role_name)])
        wrong_case = SimpleNamespace(roles=[SimpleNamespace(name=INFANTRY_ALERT.trainer_role_name.lower())])

        self.assertTrue(_has_role(trainer, role_name=INFANTRY_ALERT.trainer_role_name))
        self.assertFalse(_has_role(wrong_case, role_name=INFANTRY_ALERT.trainer_role_name))

    def test_tank_crew_role_check_uses_configured_id(self) -> None:
        trainee = SimpleNamespace(roles=[SimpleNamespace(id=TANK_CREW_ALERT.trainee_role_id, name="Renamed role")])

        self.assertTrue(_has_trainee_role(trainee, TANK_CREW_ALERT))
        self.assertEqual(TANK_CREW_ALERT.alert_channel_id, 1334213005055102977)
        self.assertEqual(TANK_CREW_ALERT.trainer_role_id, 1337743860532645930)

    def test_alert_mentions_trainee_and_trainer_role_without_button_text(self) -> None:
        content = _alert_content(123, 456, INFANTRY_ALERT)

        self.assertIn("<@123>", content)
        self.assertIn("<@&456>", content)
        self.assertNotIn("Click to DM", content)

    def test_tank_crew_alert_uses_tank_wording(self) -> None:
        content = _alert_content(123, 456, TANK_CREW_ALERT, is_test=True)

        self.assertIn("TEST ALERT", content)
        self.assertIn("Tank Crew Trainee", content)
        self.assertIn("tank crew training", content)

    def test_matching_trainer_can_run_test(self) -> None:
        member = SimpleNamespace(
            roles=[SimpleNamespace(id=TANK_CREW_ALERT.trainer_role_id)],
            guild_permissions=SimpleNamespace(manage_guild=False, administrator=False),
        )
        self.assertTrue(_can_test_alert(member, TANK_CREW_ALERT))

    def test_manage_guild_can_run_test(self) -> None:
        member = SimpleNamespace(
            roles=[],
            guild_permissions=SimpleNamespace(manage_guild=True, administrator=False),
        )
        self.assertTrue(_can_test_alert(member, INFANTRY_ALERT))

    def test_regular_member_cannot_run_test(self) -> None:
        member = SimpleNamespace(
            roles=[],
            guild_permissions=SimpleNamespace(manage_guild=False, administrator=False),
        )
        self.assertFalse(_can_test_alert(member, INFANTRY_ALERT))

    def test_test_slash_command_name(self) -> None:
        command = TraineeAlert.test_trainee_alert
        self.assertEqual(command.name, "test_trainee_alert")


if __name__ == "__main__":
    unittest.main()
