from __future__ import annotations

import sqlite3
from pathlib import Path

from dicepp_data import InstanceLayout
from dicepp_data.archive import (
    create_archive,
    delete_archive,
    export_archive_path,
    list_archives,
    verify_archive,
)
from plugins.DicePP.core.data.schema import INSTANCE_TARGET, apply_schema_target


def test_archive_create_inventory_verify_export_delete(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.data_root.mkdir(parents=True, exist_ok=True)
    db_path = layout.data_root / "dicepp.db"
    apply_schema_target(db_path, INSTANCE_TARGET)
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE entries (value TEXT)")
        connection.execute("INSERT INTO entries VALUES ('sample')")
        connection.commit()

    summary, manifest = create_archive(layout=layout)
    assert manifest["format_version"] == 1
    assert set(manifest) == {
        "format_version",
        "created_at",
        "dicepp_version",
        "description",
        "files",
    }
    assert all(set(item) == {"path", "size", "sha256"} for item in manifest["files"])
    assert list_archives(layout=layout)[0]["filename"] == summary["filename"]
    assert verify_archive(summary["filename"], layout=layout)["verified"] is True
    assert export_archive_path(summary["filename"], layout=layout).is_file()
    deleted = delete_archive(summary["filename"], layout=layout)
    assert deleted["filename"] == summary["filename"]


def test_archive_create_does_not_require_sqlite_schema_metadata(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.data_root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(layout.data_root / "dicepp.db") as connection:
        connection.execute("CREATE TABLE entries (value TEXT)")
        connection.commit()

    _summary, manifest = create_archive(layout=layout)

    assert manifest["files"]
    assert set(manifest["files"][0]) == {"path", "size", "sha256"}
