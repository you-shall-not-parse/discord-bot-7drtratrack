import asyncio
import unittest
from types import SimpleNamespace

from cogs.hllv_names import HLLVNames, _normalise_name


class FakeGuild:
    def __init__(self, members):
        self.members = members

    def get_member(self, user_id):
        return self.members.get(user_id)


class HLLVNamesTests(unittest.TestCase):
    def test_normalise_name_collapses_whitespace(self):
        self.assertEqual(_normalise_name("  My   HLLV\nName  "), ("My HLLV Name", None))

    def test_normalise_name_rejects_blank_and_long_values(self):
        self.assertIsNone(_normalise_name(" \n ")[0])
        self.assertIsNone(_normalise_name("x" * 65)[0])

    def test_directory_is_sorted_by_discord_display_name(self):
        guild = FakeGuild(
            {
                1: SimpleNamespace(display_name="Zulu"),
                2: SimpleNamespace(display_name="alpha"),
            }
        )
        lines = HLLVNames._directory_lines(guild, [(1, "One"), (2, "Two")])
        self.assertEqual(lines, ["<@2> - alpha - Two", "<@1> - Zulu - One"])

    def test_embed_escapes_member_supplied_markdown_and_mentions(self):
        guild = FakeGuild({1: SimpleNamespace(display_name="Member")})
        embed = HLLVNames.build_embeds(guild, [(1, "**name** @everyone")])[0]
        self.assertIn(r"\*\*name\*\*", embed.description)
        self.assertNotIn("@everyone", embed.description)
        self.assertEqual(embed.footer.text, "1 registered HLLV name(s)")
        self.assertEqual(embed.image.url, "attachment://hllv-name-directory.webp")

    def test_large_directory_is_split_without_losing_names(self):
        members = {
            index: SimpleNamespace(display_name=f"Member {index:03}")
            for index in range(150)
        }
        records = [(index, "N" * 64) for index in range(150)]
        embeds = HLLVNames.build_embeds(FakeGuild(members), records)
        self.assertGreater(len(embeds), 1)
        combined = "\n".join(embed.description or "" for embed in embeds)
        for index in range(150):
            self.assertIn(f"<@{index}>", combined)
        self.assertTrue(all(len(embed.description or "") <= 4096 for embed in embeds))
        self.assertEqual(embeds[0].image.url, "attachment://hllv-name-directory.webp")
        self.assertFalse(embeds[1].image.url)

    def test_search_matches_nickname_username_id_and_hllv_name(self):
        guild = FakeGuild(
            {
                123: SimpleNamespace(
                    display_name="Discord Nick",
                    name="account_name",
                    global_name="Global Name",
                ),
                456: SimpleNamespace(
                    display_name="Someone Else",
                    name="another_account",
                    global_name=None,
                ),
            }
        )
        records = [(123, "HLLV Soldier"), (456, "Tank Driver")]
        for query in ("discord nick", "ACCOUNT_NAME", "123", "soldier", "global name"):
            self.assertEqual(HLLVNames._search_records(guild, records, query), [(123, "HLLV Soldier")])

    def test_view_contains_directory_dropdown_and_search_button(self):
        cog = SimpleNamespace()
        from cogs.hllv_names import HLLVNameView

        async def build_view():
            return HLLVNameView(cog)

        view = asyncio.run(build_view())
        custom_ids = {item.custom_id for item in view.children}
        self.assertEqual(custom_ids, {"hllv_names:actions", "hllv_names:search"})


if __name__ == "__main__":
    unittest.main()
