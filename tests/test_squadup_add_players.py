import unittest

from cogs.SquadUp import SquadSignupView, add_users_to_squads


class AddUsersToSquadsTests(unittest.TestCase):
    def test_adds_players_to_squads_in_order(self):
        post = {
            "squads": {"Alpha": [], "Bravo": []},
            "max_per_squad": 2,
        }

        result = add_users_to_squads(post, [10, 20, 30])

        self.assertEqual(result, (3, 0))
        self.assertEqual(post["squads"], {"Alpha": [10, 20], "Bravo": [30]})

    def test_skips_players_already_in_a_squad_and_duplicate_selections(self):
        post = {
            "squads": {"Alpha": [10], "Bravo": []},
            "max_per_squad": 2,
        }

        result = add_users_to_squads(post, [10, 20, 20])

        self.assertEqual(result, (1, 0))
        self.assertEqual(post["squads"], {"Alpha": [10, 20], "Bravo": []})

    def test_does_not_partially_add_selection_when_there_is_not_enough_room(self):
        post = {
            "squads": {"Alpha": [10]},
            "max_per_squad": 2,
        }

        result = add_users_to_squads(post, [20, 30])

        self.assertEqual(result, (0, 1))
        self.assertEqual(post["squads"], {"Alpha": [10]})


class SquadSignupViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_multi_squad_view_includes_add_players_button(self):
        view = SquadSignupView(None, 123, 1, True, ["Alpha"], True)

        self.assertIn("squadup_add_players", [item.custom_id for item in view.children])

    async def test_crew_view_does_not_include_add_players_button(self):
        view = SquadSignupView(None, 456, 1, True, ["Tank 1"], False)

        self.assertNotIn("squadup_add_players", [item.custom_id for item in view.children])


if __name__ == "__main__":
    unittest.main()
