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


def test_discord_summary_lists_each_rank_count_without_names() -> None:
    first_role_id = RANK_GROUPS[0][1][0][1]
    role = SimpleNamespace(
        id=first_role_id,
        members=[
            SimpleNamespace(id=2, display_name="Zulu Rat", name="zulu", bot=False),
            SimpleNamespace(id=1, display_name="Alpha", name="alpha", bot=False),
            SimpleNamespace(id=3, display_name="Ignored Bot", name="bot", bot=True),
        ],
    )
    cog = object.__new__(RankDirectory)

    rendered = str(cog._build_summary_embed(FakeGuild([role])).to_dict())

    assert "**O11 Field Marshal (FM) (2)**" in rendered
    assert "**E3 Corporal (Cpl) (0)**" in rendered
    assert "Alpha" not in rendered
    assert "Zulu Rat" not in rendered
    assert "Ignored Bot" not in rendered


def test_html_contains_full_grouped_member_breakdown() -> None:
    first_role_id = RANK_GROUPS[0][1][0][1]
    role = SimpleNamespace(
        id=first_role_id,
        members=[
            SimpleNamespace(id=2, display_name="Zulu <Rat>", name="zulu", bot=False),
            SimpleNamespace(id=1, display_name="Alpha & Co", name="alpha", bot=False),
            SimpleNamespace(id=3, display_name="Ignored Bot", name="bot", bot=True),
        ],
    )
    cog = object.__new__(RankDirectory)

    rendered = cog._render_html(FakeGuild([role]))

    assert "General Staff" in rendered
    assert "O11 Field Marshal (FM) <span>2</span>" in rendered
    assert "Alpha &amp; Co" in rendered
    assert "Zulu &lt;Rat&gt;" in rendered
    assert rendered.index("Alpha &amp; Co") < rendered.index("Zulu &lt;Rat&gt;")
    assert "Ignored Bot" not in rendered
