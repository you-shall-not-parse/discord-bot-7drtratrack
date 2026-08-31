from types import SimpleNamespace

from cogs.rollcall import BLUEBERRY_ROLE_ID, RollCallCog


def test_blueberry_role_is_treated_as_former_member() -> None:
    blueberry = SimpleNamespace(roles=[SimpleNamespace(id=BLUEBERRY_ROLE_ID)])
    active = SimpleNamespace(roles=[SimpleNamespace(id=123)])

    assert RollCallCog._is_former_member(None)
    assert RollCallCog._is_former_member(blueberry)
    assert not RollCallCog._is_former_member(active)


def test_blueberry_is_excluded_from_expected_rollcall_members() -> None:
    blueberry = SimpleNamespace(
        id=1,
        display_name="Virid",
        roles=[SimpleNamespace(id=BLUEBERRY_ROLE_ID)],
    )
    active = SimpleNamespace(
        id=2,
        display_name="Active Member",
        roles=[SimpleNamespace(id=999)],
    )
    tracked_role = SimpleNamespace(members=[blueberry, active])
    guild = SimpleNamespace(get_role=lambda role_id: tracked_role if role_id == 999 else None)
    config = SimpleNamespace(tracked_role_ids=(999,), tracked_role_id=None)
    cog = object.__new__(RollCallCog)

    assert cog._expected_members(guild, config) == [active]
