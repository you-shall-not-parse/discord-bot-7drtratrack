import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from cogs.trainee_alert import (
    DIPLOMAT_ALERT,
    INFANTRY_ALERT,
    SQUAD_LEADER_ALERT,
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

    def test_squad_leader_role_check_uses_configured_id(self) -> None:
        trainee = SimpleNamespace(roles=[SimpleNamespace(id=1107595211862966283, name="Renamed role")])

        self.assertTrue(_has_trainee_role(trainee, SQUAD_LEADER_ALERT))
        self.assertEqual(SQUAD_LEADER_ALERT.alert_channel_id, INFANTRY_ALERT.alert_channel_id)
        self.assertEqual(SQUAD_LEADER_ALERT.trainer_role_name, INFANTRY_ALERT.trainer_role_name)
        self.assertFalse(SQUAD_LEADER_ALERT.mention_trainer_role)

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

    def test_squad_leader_alert_uses_sl_wording(self) -> None:
        content = _alert_content(123, None, SQUAD_LEADER_ALERT)

        self.assertIn("<@123> has joined with the **Squad Leader Trainee** role", content)
        self.assertIn("Please contact them to arrange their SL training", content)
        self.assertNotIn("<@&", content)

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

    def test_diplomat_alert_uses_role_id_and_clan_wording(self) -> None:
        member = SimpleNamespace(roles=[SimpleNamespace(id=1098206797900284035, name="Renamed")])
        self.assertTrue(_has_trainee_role(member, DIPLOMAT_ALERT))
        self.assertEqual(DIPLOMAT_ALERT.alert_channel_id, 1097920441022152774)
        content = _alert_content(123, None, DIPLOMAT_ALERT)
        self.assertIn("<@123> has joined with the **Diplomat** role", content)
        self.assertIn("clan name and acronym", content)
        self.assertNotIn("training", content)


class DiplomatAlertLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_role_added_after_join_alerts_only_once(self) -> None:
        cog = TraineeAlert(MagicMock())
        cog._send_alert = AsyncMock(return_value=object())
        before = SimpleNamespace(id=123, roles=[])
        after = SimpleNamespace(id=123, roles=[SimpleNamespace(id=1098206797900284035)])
        try:
            await cog.on_member_join(before)
            await cog.on_member_update(before, after)
            await cog.on_member_update(before, after)
            cog._send_alert.assert_awaited_once_with(after, DIPLOMAT_ALERT)
        finally:
            cog.cog_unload()

    async def test_member_joining_with_role_alerts(self) -> None:
        cog = TraineeAlert(MagicMock())
        cog._send_alert = AsyncMock(return_value=object())
        member = SimpleNamespace(id=123, roles=[SimpleNamespace(id=1098206797900284035)])
        try:
            await cog.on_member_join(member)
            cog._send_alert.assert_awaited_once_with(member, DIPLOMAT_ALERT)
        finally:
            cog.cog_unload()


if __name__ == "__main__":
    unittest.main()
