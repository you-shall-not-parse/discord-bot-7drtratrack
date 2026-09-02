import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import cogs.HLLInfLeaderboard as infantry


def member(user_id: int, *role_names: str):
    return SimpleNamespace(
        id=user_id,
        mention=f"<@{user_id}>",
        roles=[SimpleNamespace(name=name) for name in role_names],
    )


def test_infantry_leaderboard_excludes_departed_blueberry_and_diplomat_members() -> None:
    assert infantry.HLLInfLeaderboard._is_leaderboard_eligible(member(1, "Infantry"))
    assert infantry.HLLInfLeaderboard._is_leaderboard_eligible(member(2, "Other"))
    assert not infantry.HLLInfLeaderboard._is_leaderboard_eligible(member(3, "Blueberry"))
    assert not infantry.HLLInfLeaderboard._is_leaderboard_eligible(member(4, "DIPLOMAT"))
    assert not infantry.HLLInfLeaderboard._is_leaderboard_eligible(member(5, "Other", "Diplomat"))
    assert not infantry.HLLInfLeaderboard._is_leaderboard_eligible(None)


def test_infantry_embed_filters_ineligible_members_and_backfills_top_five(tmp_path, monkeypatch) -> None:
    database = tmp_path / "leaderboard.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE submissions (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                stat TEXT,
                value INTEGER,
                submitted_at TEXT,
                proof_verified INTEGER
            )
            """
        )
        connection.executemany(
            "INSERT INTO submissions(user_id, stat, value, submitted_at, proof_verified) VALUES (?, ?, ?, ?, 1)",
            [
                (1, "Most Kills", 100, "2026-01-01T00:00:00"),  # Left the server.
                (2, "Most Kills", 90, "2026-01-02T00:00:00"),
                (3, "Most Kills", 80, "2026-01-03T00:00:00"),
                (4, "Most Kills", 70, "2026-01-04T00:00:00"),
                (5, "Most Kills", 60, "2026-01-05T00:00:00"),
                (6, "Most Kills", 50, "2026-01-06T00:00:00"),
                (7, "Most Kills", 40, "2026-01-07T00:00:00"),
                (8, "Most Kills", 30, "2026-01-08T00:00:00"),
            ],
    )

    members = {
        2: member(2, "Infantry"),
        3: member(3, "Blueberry"),       # Community-only role.
        4: member(4, "Diplomat"),
        5: member(5, "Infantry"),
        6: member(6, "Recon"),
        7: member(7, "Armour"),
        8: member(8, "Recruit"),
    }
    guild = SimpleNamespace(get_member=members.get)
    bot = SimpleNamespace(get_guild=lambda _guild_id: guild)
    monkeypatch.setattr(infantry, "DB_FILE", str(database))
    monkeypatch.setattr(infantry, "STATS", ["Most Kills"])

    embed = asyncio.run(infantry.HLLInfLeaderboard(bot).build_leaderboard_embed())
    value = embed.fields[0].value

    assert "<@1>" not in value
    assert "<@2>" in value
    assert "<@3>" not in value
    assert "<@4>" not in value
    assert all(f"<@{user_id}>" in value for user_id in range(5, 9))
    assert value.count("\n") == 4


def test_infantry_leaderboard_refreshes_when_eligibility_changes() -> None:
    guild = SimpleNamespace(id=infantry.GUILD_ID)
    cog = infantry.HLLInfLeaderboard(SimpleNamespace())
    cog.update_leaderboard = AsyncMock()

    asyncio.run(cog.on_member_remove(SimpleNamespace(guild=guild, roles=[SimpleNamespace(name="Infantry")])))
    asyncio.run(
        cog.on_member_update(
            SimpleNamespace(guild=guild, roles=[SimpleNamespace(name="Diplomat")]),
            SimpleNamespace(guild=guild, roles=[SimpleNamespace(name="Infantry")]),
        )
    )

    assert cog.update_leaderboard.await_count == 2
