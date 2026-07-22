from __future__ import annotations

import json
from pathlib import Path

import pytest

from dicepp_data import (
    ARCHIVE_PROFILE_FULL,
    ARCHIVE_PROFILE_REGULAR,
    BOT_CORE_ASSET,
    CONTENT_ASSET,
    DATA_CATALOG,
    InstanceLayout,
    LOCAL_IMAGES_ASSET,
    PERSONA_DB_ASSET,
)


def _write(path: Path, value: str = "data") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def test_instance_layout_applies_project_and_compatible_data_override(tmp_path: Path) -> None:
    instance = tmp_path / "instance"
    external_data = tmp_path / "external-data"

    layout = InstanceLayout.from_env(
        tmp_path / "unused",
        environ={
            "DICEPP_PROJECT_ROOT": str(instance),
            "DICEPP_DATA_DIR": str(external_data),
        },
    )

    assert layout.config_user == instance.resolve() / "config" / "user.json"
    assert layout.content_dir == instance.resolve() / "content"
    assert layout.data_root == external_data.resolve()
    assert BOT_CORE_ASSET.resolve(layout, bot_id="12345") == (
        external_data.resolve() / "bots" / "12345" / "bot_data.db"
    )


def test_catalog_profiles_enumerate_managed_files_without_crossing_scope(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    _write(layout.config_user, "secret")
    _write(layout.config_bots_dir / "123.json", "{}")
    _write(layout.config_bots_dir / "_template.json", "template")
    _write(layout.data_root / "dicepp.db")
    _write(layout.data_bots_dir / "123" / "bot_data.db")
    _write(layout.data_bots_dir / "123" / "log.db")
    _write(layout.data_bots_dir / "123" / "personas_data_hero.db")
    _write(layout.data_bots_dir / "123" / "personas_data_..db")
    _write(layout.data_bots_dir / "123" / "unmanaged.db")
    _write(layout.local_images_dir / "avatar.png")
    _write(layout.content_queries_dir / "rules.db")

    regular = {
        match.logical_path
        for match in DATA_CATALOG.collect(layout, ARCHIVE_PROFILE_REGULAR)
    }
    full = {
        match.logical_path
        for match in DATA_CATALOG.collect(layout, ARCHIVE_PROFILE_FULL)
    }

    assert regular == {
        "config/user.json",
        "config/bots/123.json",
        "data/dicepp.db",
        "data/bots/123/bot_data.db",
        "data/bots/123/log.db",
        "data/bots/123/personas_data_hero.db",
        "data/local_images/avatar.png",
    }
    assert full == regular | {"content/queries/rules.db"}
    assert DATA_CATALOG.find_for_logical_path(
        "content/queries/rules.db",
        profile=ARCHIVE_PROFILE_REGULAR,
    ) is None
    assert DATA_CATALOG.find_for_logical_path(
        "content/queries/rules.db",
        profile=ARCHIVE_PROFILE_FULL,
    ) is CONTENT_ASSET


def test_catalog_description_and_digest_are_stable_and_machine_readable() -> None:
    description = DATA_CATALOG.to_dict()
    encoded = json.dumps(description, sort_keys=True, separators=(",", ":"))

    assert description["format_version"] == 1
    assert [asset["id"] for asset in description["assets"]] == sorted(
        asset.id for asset in DATA_CATALOG.assets
    )
    assert len(DATA_CATALOG.digest) == 64
    assert DATA_CATALOG.digest == DATA_CATALOG.digest
    assert "personas_data_{character}.db" in encoded


def test_asset_parameters_cannot_escape_the_instance(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)

    with pytest.raises(ValueError, match="one path segment"):
        BOT_CORE_ASSET.resolve(layout, bot_id="../outside")


def test_directory_assets_match_files_below_the_root_but_not_the_root_itself() -> None:
    assert LOCAL_IMAGES_ASSET.matches_logical_path("data/local_images/avatar.png")
    assert not LOCAL_IMAGES_ASSET.matches_logical_path("data/local_images")
    assert not LOCAL_IMAGES_ASSET.matches_logical_path("data/local_images/.")
    assert CONTENT_ASSET.matches_logical_path("content/queries/rules.db")
    assert not CONTENT_ASSET.matches_logical_path("content")


def test_dynamic_asset_parameters_roundtrip_between_logical_and_physical_paths(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    logical_path = "data/bots/123/personas_data_hero.db"

    parameters = PERSONA_DB_ASSET.parameters_from_logical_path(logical_path)
    assert parameters == {"bot_id": "123", "character": "hero"}
    assert PERSONA_DB_ASSET.resolve(layout, **parameters) == (
        layout.data_bots_dir / "123" / "personas_data_hero.db"
    )

    _write(layout.data_bots_dir / "123" / "personas_data_hero.db")
    _write(layout.data_bots_dir / "456" / "personas_data_other.db")
    matches = list(PERSONA_DB_ASSET.iter_matches(layout, bot_id="123"))
    assert [(match.logical_path, dict(match.parameters)) for match in matches] == [
        (logical_path, parameters)
    ]


def test_assets_declare_narrow_restore_scope_roots(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)

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


def test_dynamic_sibling_resolution_reuses_asset_filename_contract(tmp_path: Path) -> None:
    current = tmp_path / "personas_data_old.db"

    assert PERSONA_DB_ASSET.resolve_sibling(current, character="new") == (
        tmp_path / "personas_data_new.db"
    )
    with pytest.raises(ValueError, match="one path segment"):
        PERSONA_DB_ASSET.resolve_sibling(current, character="../outside")
