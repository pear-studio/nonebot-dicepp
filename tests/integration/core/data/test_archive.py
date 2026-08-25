from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

from dicepp_data import InstanceLayout
from dicepp_data.archive import (
    create_archive,
    delete_archive,
    export_archive_path,
    list_archives,
    verify_archive,
)
from dicepp_data.instance_data import import_instance_data
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
    assert manifest["format_version"] == 3
    assert {"checksum", "assets", "catalog", "scope"}.isdisjoint(manifest)
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


def test_rich_v3_manifest_is_imported_and_migrated(tmp_path: Path) -> None:
    source_layout = InstanceLayout.from_root(tmp_path / "source")
    source_layout.data_root.mkdir(parents=True, exist_ok=True)
    apply_schema_target(source_layout.data_root / "dicepp.db", INSTANCE_TARGET)
    with sqlite3.connect(source_layout.data_root / "dicepp.db") as connection:
        connection.execute("CREATE TABLE entries (value TEXT)")
        connection.execute("INSERT INTO entries VALUES ('legacy')")
        connection.commit()

    summary, manifest = create_archive(layout=source_layout)
    for record in manifest["files"]:
        record["asset_id"] = "legacy.asset"
    manifest.update(
        {
            "catalog": {"digest": "old-catalog"},
            "assets": [{"id": "legacy.asset", "schema": {"latest_version": 999}}],
            "checksum": {"algorithm": "sha256", "files": {"old": "declaration"}},
            "schema": {"name": "old", "latest_version": 999},
            "dicepp_version": "0.1.0",
        }
    )
    archive_path = source_layout.backups_dir / summary["filename"]
    rewritten = archive_path.with_name("rich-v3.zip")
    with zipfile.ZipFile(archive_path) as original, zipfile.ZipFile(rewritten, "w") as updated:
        for item in original.infolist():
            payload = (
                json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
                if item.filename == "manifest.json"
                else original.read(item.filename)
            )
            updated.writestr(item, payload)
    archive_path.unlink()
    rewritten.rename(archive_path)

    target_layout = InstanceLayout.from_root(tmp_path / "target")
    target_archive = target_layout.backups_dir / archive_path.name
    target_archive.parent.mkdir(parents=True, exist_ok=True)
    target_archive.write_bytes(archive_path.read_bytes())
    result = import_instance_data(target_layout, archive=archive_path.name)

    assert result["imported"] == ["data/dicepp.db"]
    assert result["migrations"]
