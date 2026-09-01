from cogs.EmbedManager import EmbedManager


def test_sync_embeds_requires_guild_and_manage_guild_permission() -> None:
    command = EmbedManager.sync_embeds_cmd
    check_names = {check.__qualname__.split(".<locals>", 1)[0] for check in command.checks}

    assert check_names == {"guild_only", "has_permissions"}
