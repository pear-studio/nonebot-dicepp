from __future__ import annotations

from pathlib import Path

import pytest

from dicepp_data import (
    BOT_CORE_ASSET,
    CONTENT_ASSET,
    InstanceLayout,
    LOCAL_IMAGES_ASSET,
    PERSONA_DB_ASSET,
)


def test_instance_layout_applies_project_and_compatible_data_override() -> None:
    instance = Path("unit-instance")
    external_data = Path("unit-external-data")

    layout = InstanceLayout.from_env(
        Path("unit-unused"),
        environ={
            "DICEPP_PROJECT_ROOT": str(instance),
            "DICEPP_DATA_DIR": str(external_data),
        },
    )

    assert layout.config_user == instance.resolve() / "config" / "user.json"
    assert layout.content_dir == instance.resolve() / "content"
    assert layout.data_root == external_data.resolve()
    assert layout.backups_dir == external_data.resolve() / "backups"
    assert BOT_CORE_ASSET.resolve(layout, bot_id="12345") == (
        external_data.resolve() / "bots" / "12345" / "bot_data.db"
    )


def test_asset_parameters_cannot_escape_the_instance() -> None:
    layout = InstanceLayout.from_root("unit-instance")

    with pytest.raises(ValueError, match="one path segment"):
        BOT_CORE_ASSET.resolve(layout, bot_id="../outside")


def test_directory_assets_match_files_below_the_root_but_not_the_root_itself() -> None:
    assert LOCAL_IMAGES_ASSET.matches_logical_path("data/local_images/avatar.png")
    assert not LOCAL_IMAGES_ASSET.matches_logical_path("data/local_images")
    assert not LOCAL_IMAGES_ASSET.matches_logical_path("data/local_images/.")
    assert CONTENT_ASSET.matches_logical_path("content/queries/rules.db")
    assert not CONTENT_ASSET.matches_logical_path("content")


def test_dynamic_asset_parameters_roundtrip_between_logical_and_physical_paths() -> None:
    layout = InstanceLayout.from_root("unit-instance")
    logical_path = "data/bots/123/personas_data_hero.db"

    parameters = PERSONA_DB_ASSET.parameters_from_logical_path(logical_path)
    assert parameters == {"bot_id": "123", "character": "hero"}
    assert PERSONA_DB_ASSET.resolve(layout, **parameters) == (
        layout.data_bots_dir / "123" / "personas_data_hero.db"
    )


def test_assets_declare_narrow_restore_scope_roots() -> None:
    layout = InstanceLayout.from_root("unit-instance")

    persona = PERSONA_DB_ASSET.restore_target(
        layout,
        "data/bots/123/personas_data_hero.db",
    )
    local_image = LOCAL_IMAGES_ASSET.restore_target(
        layout,
        "data/local_images/avatar.png",
    )

    assert persona is not None
    assert persona.scope_root == layout.data_bots_dir
    assert persona.path == layout.data_bots_dir / "123" / "personas_data_hero.db"
    assert local_image is not None
    assert local_image.scope_root == layout.local_images_dir
    assert local_image.path == layout.local_images_dir / "avatar.png"


def test_dynamic_sibling_resolution_reuses_asset_filename_contract() -> None:
    current = Path("personas_data_old.db")

    assert PERSONA_DB_ASSET.resolve_sibling(current, character="new") == Path(
        "personas_data_new.db"
    )
    with pytest.raises(ValueError, match="one path segment"):
        PERSONA_DB_ASSET.resolve_sibling(current, character="../outside")
