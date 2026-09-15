from cogs.hll_maps import HLLMaps, MAP_CHOICES, MAP_IMAGE_FILES


def test_map_command_exposes_every_bundled_map_as_a_choice() -> None:
    assert len(MAP_CHOICES) == 20
    assert {choice.value for choice in MAP_CHOICES} == set(MAP_IMAGE_FILES)
    assert [choice.name for choice in MAP_CHOICES] == sorted(MAP_IMAGE_FILES, key=str.casefold)


def test_every_map_choice_has_a_local_image() -> None:
    for map_name, filename in MAP_IMAGE_FILES.items():
        image_path = HLLMaps.map_path(map_name)

        assert image_path is not None, map_name
        assert image_path.name == filename
        assert image_path.is_file(), map_name


def test_unknown_map_does_not_resolve_a_file() -> None:
    assert HLLMaps.map_path("Not a real map") is None
