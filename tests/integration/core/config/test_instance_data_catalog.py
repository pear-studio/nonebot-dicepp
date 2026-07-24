from __future__ import annotations

from pathlib import Path

from dicepp_data import (
    ARCHIVE_PROFILE_FULL,
    ARCHIVE_PROFILE_REGULAR,
    CONTENT_ASSET,
    DATA_CATALOG,
    InstanceLayout,
    PERSONA_DB_ASSET,
)


def _write(path: Path, value: str = "data") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


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


def test_dynamic_asset_parameters_collect_only_bound_bot_files(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    _write(layout.data_bots_dir / "123" / "personas_data_hero.db")
    _write(layout.data_bots_dir / "456" / "personas_data_other.db")

    matches = list(PERSONA_DB_ASSET.iter_matches(layout, bot_id="123"))

    assert [
        (match.logical_path, dict(match.parameters)) for match in matches
    ] == [
        (
            "data/bots/123/personas_data_hero.db",
            {"bot_id": "123", "character": "hero"},
        )
    ]
