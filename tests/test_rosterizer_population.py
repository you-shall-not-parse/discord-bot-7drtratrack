from types import SimpleNamespace

from cogs.rosterizer import _members_needing_role


def _member(member_id: int, role_ids: list[int], *, bot: bool = False):
    return SimpleNamespace(
        id=member_id,
        bot=bot,
        roles=[SimpleNamespace(id=role_id) for role_id in role_ids],
    )


def test_members_needing_role_only_returns_human_source_members_without_target() -> None:
    target_role = SimpleNamespace(id=20)
    needs_target = _member(1, [10])
    already_has_target = _member(2, [10, 20])
    bot_member = _member(3, [10], bot=True)
    source_role = SimpleNamespace(members=[needs_target, already_has_target, bot_member])

    assert _members_needing_role(source_role, target_role) == [needs_target]
