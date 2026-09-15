from types import SimpleNamespace

from cogs.rank_directory import OUTPUT_CHANNEL_ID, RANK_GROUPS, RankDirectory


class FakeGuild:
    def __init__(self, roles):
        self._roles = {role.id: role for role in roles}
        self.name = "7DR"

    def get_role(self, role_id):
        return self._roles.get(role_id)


def test_rank_structure_uses_requested_channel_and_unique_role_ids() -> None:
    role_ids = [role_id for _group, ranks in RANK_GROUPS for _label, role_id in ranks]

    assert OUTPUT_CHANNEL_ID == 1098316982459314279
    assert len(role_ids) == 20
    assert len(set(role_ids)) == len(role_ids)
    assert role_ids[0] == 1098651308212359289
    assert role_ids[-1] == 1098647326882541609


def test_rank_directory_lists_plain_escaped_names_without_mentions() -> None:
    first_role_id = RANK_GROUPS[0][1][0][1]
    role = SimpleNamespace(
        id=first_role_id,
        members=[
            SimpleNamespace(id=2, display_name="Zulu *Rat*", name="zulu", bot=False),
            SimpleNamespace(id=1, display_name="Alpha @everyone", name="alpha", bot=False),
            SimpleNamespace(id=3, display_name="Ignored Bot", name="bot", bot=True),
        ],
    )
    cog = object.__new__(RankDirectory)

    rendered = "\n".join(cog._rank_sections(FakeGuild([role])))

    assert "Alpha @\u200beveryone" in rendered
    assert "Zulu \\*Rat\\*" in rendered
    assert rendered.index("Alpha") < rendered.index("Zulu")
    assert "Ignored Bot" not in rendered
    assert "<@" not in rendered


def test_rank_directory_pages_at_section_boundaries() -> None:
    pages = RankDirectory._paginate_sections(["A" * 60, "B" * 60, "C" * 60], limit=125)

    assert pages == [f"{'A' * 60}\n\n{'B' * 60}", "C" * 60]
