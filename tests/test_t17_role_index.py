from cogs.t17_role_index import INDEX_CHANNEL_ID, T17RoleIndex


def test_t17_index_targets_replacement_channel() -> None:
    assert INDEX_CHANNEL_ID == 1549529105874165911


def test_t17_index_html_contains_same_fields_and_escapes_member_data() -> None:
    document = T17RoleIndex._render_index_html(
        {
            "Basic <Trained>": [
                {
                    "username": "user<script>",
                    "nickname": "Nick & Name",
                    "t17_id": "player/id?x=1",
                },
                {
                    "username": "unknown-user",
                    "nickname": "Unknown Player",
                    "t17_id": "",
                },
            ]
        }
    )

    assert "Discord name" in document
    assert "Nickname" in document
    assert "T17 ID" in document
    assert "Basic &lt;Trained&gt;" in document
    assert "user&lt;script&gt;" in document
    assert "Nick &amp; Name" in document
    assert "https://www.hllrecords.com/profiles/player%2Fid%3Fx%3D1" in document
    assert "player/id?x=1" in document
    assert "Unknown" in document
    assert "<script>" not in document
