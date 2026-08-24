from __future__ import annotations

import io
import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

import dicepp_manager.archive as archive_module
from dicepp_data import InstanceLayout
from dicepp_manager.archive import (
    ArchiveError,
    ArchiveInvalidError,
    MAX_MANIFEST_BYTES,
    apply_archive,
    create_archive,
    enforce_system_retention,
    export_archive_path,
    import_archive,
    plan_archive_restore,
    verify_archive,
    list_archives,
)


def _write(path: Path, value: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(value, encoding="utf-8")


def _schema_metadata_rows(
    *,
    target_name: str = "instance",
    current_version: str = "1",
) -> list[tuple[str, str]]:
    timestamp = "2026-01-01T00:00:00+00:00"
    return [
        ("application", "dicepp"),
        ("target_name", target_name),
        ("current_version", current_version),
        ("created_at", timestamp),
        ("updated_at", timestamp),
    ]


def _rewrite_manifest(path: Path, update) -> None:
    with zipfile.ZipFile(path, "r") as source:
        members = {
            info.filename: source.read(info.filename)
            for info in source.infolist()
            if info.filename != "manifest.json"
        }
        manifest = json.loads(source.read("manifest.json"))
    update(manifest)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name, payload in members.items():
            target.writestr(name, payload)
        target.writestr("manifest.json", json.dumps(manifest))


def _rewrite_payload(path: Path, arcname: str, payload: bytes) -> None:
    with zipfile.ZipFile(path, "r") as source:
        members = {
            info.filename: source.read(info.filename)
            for info in source.infolist()
            if info.filename != "manifest.json"
        }
        manifest = json.loads(source.read("manifest.json"))
    members[arcname] = payload
    digest = hashlib.sha256(payload).hexdigest()
    manifest["checksum"]["files"][arcname] = digest
    top = next(item for item in manifest["files"] if item["path"] == arcname)
    top["size"] = len(payload)
    top["sha256"] = digest
    asset = next(item for item in manifest["assets"] if item["id"] == top["asset_id"])
    asset_file = next(item for item in asset["files"] if item["path"] == arcname)
    asset_file["size"] = len(payload)
    asset_file["sha256"] = digest
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name, value in members.items():
            target.writestr(name, value)
        target.writestr("manifest.json", json.dumps(manifest))


def _create_instance_database(path: Path, *, values: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO schema_metadata(key, value) VALUES (?, ?)",
            _schema_metadata_rows(),
        )
        connection.execute("CREATE TABLE entries (value TEXT PRIMARY KEY)")
        connection.executemany(
            "INSERT INTO entries(value) VALUES (?)",
            [(value,) for value in values or []],
        )
        connection.commit()
    finally:
        connection.close()


def _create_opaque_database(path: Path, *, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE legacy_entries (value TEXT PRIMARY KEY)")
        connection.execute(
            "INSERT INTO legacy_entries(value) VALUES (?)",
            (value,),
        )
        connection.commit()
    finally:
        connection.close()


def _read_archive_sqlite_values(path: Path, arcname: str) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        payload = archive.read(arcname)
    extracted = path.with_suffix(".sqlite-check.db")
    extracted.write_bytes(payload)
    connection = sqlite3.connect(extracted)
    try:
        values = [row[0] for row in connection.execute("SELECT value FROM entries ORDER BY value")]
    finally:
        connection.close()
    try:
        return values
    finally:
        # A closed SQLite connection is required on Windows before unlinking.
        extracted.with_name(f"{extracted.name}-wal").unlink(missing_ok=True)
        extracted.with_name(f"{extracted.name}-shm").unlink(missing_ok=True)
        extracted.unlink(missing_ok=True)


def test_create_archive_checkpoints_managed_sqlite_wal_before_snapshot(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    database = layout.data_root / "dicepp.db"
    _create_instance_database(database, values=["base"])

    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        connection.execute("INSERT INTO entries(value) VALUES ('wal-committed')")
        connection.commit()
        assert Path(f"{database}-wal").exists()

        summary, _manifest = create_archive(layout=layout)
    finally:
        connection.close()

    archive_path = export_archive_path(summary["filename"], layout=layout)
    assert _read_archive_sqlite_values(archive_path, "data/dicepp.db") == [
        "base",
        "wal-committed",
    ]


def test_opaque_sqlite_is_reported_and_restored_byte_for_byte(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    database = layout.data_root / "dicepp.db"
    _create_opaque_database(database, value="legacy")
    original = database.read_bytes()

    summary, manifest = create_archive(layout=layout)
    verification = verify_archive(summary["filename"], layout=layout)

    expected = {
        "count": 1,
        "files": ["data/dicepp.db"],
    }
    assert summary["opaque_sqlite_count"] == 1
    assert manifest["compatibility"]["opaque_sqlite"] == expected
    assert verification["verified"] is True
    assert verification["opaque_sqlite"] == expected
    assert verification["restorable_files"] == ["data/dicepp.db"]
    assert any("preserved as opaque files" in item for item in verification["warnings"])

    database.write_bytes(b"changed after archive")
    apply_archive(summary["filename"], layout=layout)

    assert database.read_bytes() == original


def test_corrupt_sqlite_cannot_be_downgraded_to_opaque(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    database = layout.data_root / "dicepp.db"
    _create_opaque_database(database, value="legacy")
    summary, _manifest = create_archive(layout=layout)
    path = export_archive_path(summary["filename"], layout=layout)
    _rewrite_payload(path, "data/dicepp.db", b"not a sqlite database")

    verification = verify_archive(summary["filename"], layout=layout)

    assert verification["verified"] is False
    assert verification["opaque_sqlite"] == {"count": 0, "files": []}
    assert any(
        "SQLite schema metadata cannot be read" in item
        for item in verification["problems"]
    )


def test_opaque_sqlite_must_pass_quick_check_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    database = layout.data_root / "dicepp.db"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA page_size=1024")
        connection.execute("VACUUM")
        connection.execute(
            "CREATE TABLE legacy_entries (id INTEGER PRIMARY KEY, payload BLOB)"
        )
        connection.executemany(
            "INSERT INTO legacy_entries(payload) VALUES (?)",
            [(bytes([index % 251]) * 400,) for index in range(200)],
        )

    # Page 1 contains sqlite_schema, while page 2 is this table's B-tree root.
    # Damaging page 2 leaves the metadata lookup able to report a missing table,
    # but makes SQLite's integrity check fail.
    with database.open("r+b") as handle:
        handle.seek(1024)
        handle.write(b"\x00")
    monkeypatch.setattr(
        archive_module,
        "_checkpoint_managed_sqlite_assets",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(
        ArchiveInvalidError,
        match="New archive verification failed",
    ):
        create_archive(layout=layout)

    assert not list(layout.manager_backups_dir.glob("*.zip"))
    assert not list(layout.manager_backups_dir.glob("*.inprogress"))


@pytest.mark.parametrize(
    ("removed_key", "updates", "expected_error"),
    [
        ("application", {}, "metadata is incomplete"),
        (None, {"application": "other"}, "application mismatch"),
        ("created_at", {}, "metadata is incomplete"),
        ("updated_at", {}, "metadata is incomplete"),
        (None, {"current_version": "invalid"}, "version is invalid"),
        (None, {"current_version": "-1"}, "version is invalid"),
        (None, {"current_version": "0"}, "version is invalid"),
    ],
)
def test_managed_sqlite_requires_complete_valid_lifecycle_metadata(
    tmp_path: Path,
    removed_key: str | None,
    updates: dict[str, str],
    expected_error: str,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    database = layout.data_root / "dicepp.db"
    metadata = dict(_schema_metadata_rows())
    if removed_key is not None:
        metadata.pop(removed_key)
    metadata.update(updates)
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO schema_metadata(key, value) VALUES (?, ?)",
            metadata.items(),
        )

    with pytest.raises(ArchiveError, match=expected_error):
        create_archive(layout=layout)

    assert not list(layout.manager_backups_dir.glob("*.zip"))


def test_sqlite_checkpoint_failure_prevents_archive_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    _create_instance_database(layout.data_root / "dicepp.db", values=["kept"])

    class BrokenConnection:
        def execute(self, _statement: str):
            raise sqlite3.OperationalError("checkpoint is locked")

        def close(self) -> None:
            pass

    monkeypatch.setattr(archive_module.sqlite3, "connect", lambda *_args, **_kwargs: BrokenConnection())

    with pytest.raises(ArchiveError, match="SQLite checkpoint failed"):
        create_archive(layout=layout)

    assert not list(layout.manager_backups_dir.glob("*.zip"))
    assert not list(layout.manager_backups_dir.glob("*.inprogress"))


def test_new_archive_fsyncs_file_before_publishing_target(tmp_path: Path, monkeypatch) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    _write(layout.config_user, '{"value": "saved"}')
    events: list[str] = []
    real_replace = archive_module.os.replace
    real_verify = archive_module.verify_archive_path

    def record_file_fsync(path: Path) -> None:
        events.append(f"file:{path.suffix}")

    def record_verify(*args, **kwargs):
        events.append("verify")
        return real_verify(*args, **kwargs)

    def record_replace(source, target) -> None:
        events.append("replace")
        real_replace(source, target)

    monkeypatch.setattr(archive_module, "_fsync_file", record_file_fsync, raising=False)
    monkeypatch.setattr(archive_module, "_fsync_directory", lambda _path: events.append("directory"))
    monkeypatch.setattr(archive_module, "verify_archive_path", record_verify)
    monkeypatch.setattr(archive_module.os, "replace", record_replace)

    summary, _manifest = create_archive(layout=layout)

    assert summary["filename"]
    assert events.index("file:.inprogress") < events.index("verify") < events.index("replace")
    assert events.index("replace") < events.index("directory")


def test_archive_fails_when_an_enumerated_payload_cannot_be_opened_safely(
    tmp_path: Path,
    monkeypatch,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    _write(layout.config_user, '{"value": "must-not-disappear"}')
    original_open = archive_module._open_regular_payload

    def missing_user_config(path: Path):
        if path == layout.config_user:
            return None
        return original_open(path)

    monkeypatch.setattr(archive_module, "_open_regular_payload", missing_user_config)

    with pytest.raises(ArchiveError, match="cannot be read safely"):
        create_archive(layout=layout)

    assert not list(layout.manager_backups_dir.glob("*.zip"))
    assert not list(layout.manager_backups_dir.glob("*.inprogress"))


def test_regular_and_full_plans_have_distinct_exact_delete_scope(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    _write(layout.config_user, "{}")
    _write(layout.content_dir / "decks" / "kept.txt", "saved")
    regular, _ = create_archive(layout=layout, profile="regular")
    full, _ = create_archive(layout=layout, profile="full")
    _write(layout.content_dir / "decks" / "extra.txt", "extra")

    regular_plan = plan_archive_restore(regular["filename"], layout=layout)
    full_plan = plan_archive_restore(full["filename"], layout=layout)

    assert not any(item["target_path"].startswith("content/") for item in regular_plan["remove"])
    assert [item["target_path"] for item in full_plan["remove"]] == [
        "content/decks/extra.txt"
    ]


def test_v1_is_read_as_regular_and_cross_platform_source_is_informational(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    archive_dir = layout.manager_backups_dir
    archive_dir.mkdir(parents=True)
    payload = b'{"from": "legacy"}'
    import hashlib

    manifest = {
        "format_version": 1,
        "created_at": "2026-01-01T00:00:00Z",
        "dicepp_version": "3.0.0",
        "description": "windows legacy",
        "source_platform": "win32",
        "checksum": {
            "algorithm": "sha256",
            "files": {"config/user.json": hashlib.sha256(payload).hexdigest()},
        },
    }
    with zipfile.ZipFile(archive_dir / "legacy.zip", "w") as archive:
        archive.writestr("config/user.json", payload)
        archive.writestr("manifest.json", json.dumps(manifest))

    verification = verify_archive("legacy.zip", layout=layout)
    plan = plan_archive_restore("legacy.zip", layout=layout)

    assert verification["verified"] is True
    assert verification["profile"] == "regular"
    assert plan["profile"] == "regular"


@pytest.mark.parametrize("format_version", [1, 2])
def test_legacy_opaque_sqlite_without_declaration_is_inferred_from_payload(
    tmp_path: Path,
    format_version: int,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    _create_opaque_database(layout.data_root / "dicepp.db", value="legacy-v2")
    summary, _manifest = create_archive(layout=layout)
    path = export_archive_path(summary["filename"], layout=layout)

    def downgrade_to_legacy(manifest: dict) -> None:
        manifest["format_version"] = format_version
        manifest.pop("compatibility")

    _rewrite_manifest(path, downgrade_to_legacy)

    listed = next(
        item
        for item in list_archives(layout=layout)
        if item["filename"] == summary["filename"]
    )
    verification = verify_archive(summary["filename"], layout=layout)
    plan = plan_archive_restore(summary["filename"], layout=layout)

    assert listed["opaque_sqlite_count"] is None
    assert verification["verified"] is True
    assert verification["opaque_sqlite"] == {
        "count": 1,
        "files": ["data/dicepp.db"],
    }
    assert verification["archive"]["opaque_sqlite_count"] == 1
    assert plan["opaque_sqlite"] == verification["opaque_sqlite"]
    assert plan["archive"]["opaque_sqlite_count"] == 1


def test_v3_requires_a_valid_opaque_sqlite_declaration(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    _write(layout.config_user, "{}")
    summary, manifest = create_archive(layout=layout)
    assert manifest["format_version"] == 3
    path = export_archive_path(summary["filename"], layout=layout)
    _rewrite_manifest(path, lambda value: value.pop("compatibility"))

    listed = next(
        item
        for item in list_archives(layout=layout)
        if item["filename"] == summary["filename"]
    )
    assert listed["valid"] is False
    with pytest.raises(ArchiveInvalidError, match="declaration is missing"):
        verify_archive(summary["filename"], layout=layout)


def test_v3_opaque_sqlite_declaration_must_match_payload(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    _create_opaque_database(layout.data_root / "dicepp.db", value="legacy-v3")
    summary, _manifest = create_archive(layout=layout)
    path = export_archive_path(summary["filename"], layout=layout)

    def remove_opaque_declaration(manifest: dict) -> None:
        manifest["compatibility"]["opaque_sqlite"] = {
            "count": 0,
            "files": [],
        }

    _rewrite_manifest(path, remove_opaque_declaration)
    verification = verify_archive(summary["filename"], layout=layout)

    assert verification["verified"] is False
    assert any(
        "opaque SQLite declaration does not match" in problem
        for problem in verification["problems"]
    )


def test_newer_schema_manifest_is_blocked_before_restore(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    _write(layout.config_user, "{}")
    summary, _ = create_archive(layout=layout)
    path = export_archive_path(summary["filename"], layout=layout)

    def update(manifest):
        persona = next(item for item in manifest["assets"] if item["id"] == "data.persona")
        persona["schema"]["latest_version"] = 999

    _rewrite_manifest(path, update)
    verification = verify_archive(summary["filename"], layout=layout)

    assert verification["verified"] is False
    assert any("newer" in problem for problem in verification["problems"])


def test_newer_dicepp_release_is_blocked_with_pep440_ordering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    _write(layout.config_user, "{}")
    summary, _ = create_archive(layout=layout)
    path = export_archive_path(summary["filename"], layout=layout)
    _rewrite_manifest(path, lambda manifest: manifest.update(dicepp_version="3.0.0"))
    monkeypatch.setattr(archive_module, "get_dicepp_version", lambda: "3.0.0rc9")

    verification = verify_archive(summary["filename"], layout=layout)

    assert verification["verified"] is False
    assert any("newer DicePP version" in item for item in verification["problems"])


def test_v2_sqlite_metadata_is_cross_checked_against_catalog(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.data_root.mkdir(parents=True)
    instance_db = layout.data_root / "dicepp.db"
    with sqlite3.connect(instance_db) as connection:
        connection.execute(
            "CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO schema_metadata(key, value) VALUES (?, ?)",
            _schema_metadata_rows(),
        )
    summary, _ = create_archive(layout=layout)
    path = export_archive_path(summary["filename"], layout=layout)

    future = tmp_path / "future-v2.db"
    with sqlite3.connect(future) as connection:
        connection.execute(
            "CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO schema_metadata(key, value) VALUES (?, ?)",
            _schema_metadata_rows(current_version="2"),
        )
    _rewrite_payload(path, "data/dicepp.db", future.read_bytes())

    verification = verify_archive(summary["filename"], layout=layout)

    assert verification["verified"] is False
    assert any("newer than this DicePP" in item for item in verification["problems"])


def test_v2_sqlite_metadata_cannot_exceed_manifest_declaration(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.data_root.mkdir(parents=True)
    instance_db = layout.data_root / "dicepp.db"
    with sqlite3.connect(instance_db) as connection:
        connection.execute(
            "CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO schema_metadata(key, value) VALUES (?, ?)",
            _schema_metadata_rows(),
        )
    summary, _ = create_archive(layout=layout)
    path = export_archive_path(summary["filename"], layout=layout)

    def lower_declaration(manifest):
        asset = next(item for item in manifest["assets"] if item["id"] == "data.instance")
        asset["schema"]["latest_version"] = 0

    _rewrite_manifest(path, lower_declaration)
    verification = verify_archive(summary["filename"], layout=layout)

    assert verification["verified"] is False
    assert any("manifest declaration" in item for item in verification["problems"])


def test_restore_fsyncs_parent_directory_after_atomic_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    _write(layout.config_user, '{"archived": true}')
    summary, _ = create_archive(layout=layout)
    _write(layout.config_user, '{"current": true}')
    events: list[tuple[str, Path]] = []
    real_replace = archive_module.os.replace

    def replace(source, target):
        real_replace(source, target)
        events.append(("replace", Path(target)))

    monkeypatch.setattr(archive_module.os, "replace", replace)
    monkeypatch.setattr(
        archive_module,
        "_fsync_directory",
        lambda directory: events.append(("fsync", Path(directory))),
    )

    apply_archive(summary["filename"], layout=layout)

    replace_index = events.index(("replace", layout.config_user))
    assert events[replace_index + 1] == ("fsync", layout.config_user.parent)


def test_archive_summary_invalid_profile_is_reported_as_invalid(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.manager_backups_dir.mkdir(parents=True)
    path = layout.manager_backups_dir / "bad-profile.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps({"format_version": 2, "profile": "future"}),
        )

    summaries = list_archives(layout=layout)

    assert summaries[0]["filename"] == path.name
    assert summaries[0]["valid"] is False


def test_import_export_retention_and_sensitive_metadata(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    _write(layout.config_user, '{"api_key": "secret"}')
    manual, manifest = create_archive(layout=layout, archive_kind="manual")
    exported = export_archive_path(manual["filename"], layout=layout).read_bytes()
    imported = import_archive(
        "from-linux.zip",
        io.BytesIO(exported),
        layout=layout,
    )
    for index in range(7):
        create_archive(
            f"safety-{index}",
            layout=layout,
            archive_kind="system",
        )

    deleted = enforce_system_retention(layout=layout, keep=5)

    assert manifest["sensitive"] is True
    assert imported["restored"] is False
    assert imported["verification"]["verified"] is True
    assert len(deleted) == 2
    assert export_archive_path(manual["filename"], layout=layout).exists()
    assert export_archive_path(imported["archive"]["filename"], layout=layout).exists()


def test_duplicate_zip_members_are_rejected(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.manager_backups_dir.mkdir(parents=True)
    path = layout.manager_backups_dir / "duplicate.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("config/user.json", b"{}")
        archive.writestr("config/user.json", b"{}")
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format_version": 1,
                    "checksum": {
                        "algorithm": "sha256",
                        "files": {"config/user.json": "bad"},
                    },
                }
            ),
        )

    verification = verify_archive("duplicate.zip", layout=layout)

    assert verification["verified"] is False
    assert any("Duplicate zip member" in item for item in verification["problems"])


def test_older_v2_catalog_preserves_assets_added_by_current_program(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    _write(layout.config_user, '{"saved": true}')
    summary, _ = create_archive(layout=layout)
    path = export_archive_path(summary["filename"], layout=layout)

    def make_older(manifest):
        manifest["catalog"]["digest"] = "0" * 64
        manifest["assets"] = [
            item for item in manifest["assets"] if item["id"] != "config.bots"
        ]
        persona = next(item for item in manifest["assets"] if item["id"] == "data.persona")
        persona["schema"]["latest_version"] = 2

    _rewrite_manifest(path, make_older)
    _write(layout.config_bots_dir / "new-current-asset.json", '{"keep": true}')

    verification = verify_archive(summary["filename"], layout=layout)
    plan = plan_archive_restore(summary["filename"], layout=layout)

    assert verification["verified"] is True
    assert any("additional assets" in item for item in verification["warnings"])
    assert not any(
        item["target_path"] == "config/bots/new-current-asset.json"
        for item in plan["remove"]
    )


def test_v2_asset_unknown_to_current_program_is_blocked(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    _write(layout.config_user, "{}")
    summary, _ = create_archive(layout=layout)
    path = export_archive_path(summary["filename"], layout=layout)

    def add_future(manifest):
        manifest["catalog"]["digest"] = "f" * 64
        manifest["assets"].append(
            {
                "id": "data.future",
                "kind": "file",
                "schema": None,
                "sensitive": False,
                "files": [],
            }
        )

    _rewrite_manifest(path, add_future)

    verification = verify_archive(summary["filename"], layout=layout)

    assert verification["verified"] is False
    assert any("unsupported asset" in item for item in verification["problems"])


def test_v1_scope_drives_exact_regular_removal(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.manager_backups_dir.mkdir(parents=True)
    payload = b'{"saved": true}'
    manifest = {
        "format_version": 1,
        "created_at": "2026-01-01T00:00:00Z",
        "checksum": {
            "algorithm": "sha256",
            "files": {"config/user.json": hashlib.sha256(payload).hexdigest()},
        },
        "scope": {
            "included": ["config/user.json", "config/bots/*.json"],
            "excluded": ["content"],
        },
    }
    with zipfile.ZipFile(layout.manager_backups_dir / "legacy-exact.zip", "w") as archive:
        archive.writestr("config/user.json", payload)
        archive.writestr("manifest.json", json.dumps(manifest))
    _write(layout.config_bots_dir / "extra.json", "{}")

    plan = plan_archive_restore("legacy-exact.zip", layout=layout)

    assert [item["target_path"] for item in plan["remove"]] == [
        "config/bots/extra.json"
    ]


def test_v1_sqlite_schema_newer_than_current_is_blocked(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.manager_backups_dir.mkdir(parents=True)
    database = tmp_path / "future.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO schema_metadata(key, value) VALUES (?, ?)",
            _schema_metadata_rows(current_version="999"),
        )
    payload = database.read_bytes()
    manifest = {
        "format_version": 1,
        "created_at": "2026-01-01T00:00:00Z",
        "checksum": {
            "algorithm": "sha256",
            "files": {"data/dicepp.db": hashlib.sha256(payload).hexdigest()},
        },
        "scope": {"included": ["data/dicepp.db"], "excluded": ["content"]},
    }
    with zipfile.ZipFile(layout.manager_backups_dir / "future-v1.zip", "w") as archive:
        archive.writestr("data/dicepp.db", payload)
        archive.writestr("manifest.json", json.dumps(manifest))

    verification = verify_archive("future-v1.zip", layout=layout)

    assert verification["verified"] is False
    assert any("newer" in item for item in verification["problems"])


def test_list_treats_oversized_manifest_as_invalid_without_parsing_it(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.manager_backups_dir.mkdir(parents=True)
    path = layout.manager_backups_dir / "huge-manifest.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", b"{" + b" " * MAX_MANIFEST_BYTES + b"}")

    listed = list_archives(layout=layout)

    assert listed == [
        {
            "filename": "huge-manifest.zip",
            "size": path.stat().st_size,
            "created_at": listed[0]["created_at"],
            "valid": False,
        }
    ]


def test_unknown_compression_is_blocked_before_manifest_parsing(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.manager_backups_dir.mkdir(parents=True)
    path = layout.manager_backups_dir / "unsupported-compression.zip"
    payload = b"{}"
    manifest = {
        "format_version": 1,
        "checksum": {
            "algorithm": "sha256",
            "files": {"config/user.json": hashlib.sha256(payload).hexdigest()},
        },
        "scope": {"included": ["config/user.json"], "excluded": ["content"]},
    }
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo("config/user.json")
        info.compress_type = zipfile.ZIP_BZIP2
        archive.writestr(info, payload)
        archive.writestr("undeclared.txt", b"hidden")
        archive.writestr("manifest.json", json.dumps(manifest))

    verification = verify_archive(path.name, layout=layout)

    assert verification["verified"] is False
    assert any("Unsupported zip compression" in item for item in verification["problems"])


def test_undeclared_payload_is_a_verification_problem_not_a_warning(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.manager_backups_dir.mkdir(parents=True)
    path = layout.manager_backups_dir / "undeclared.zip"
    payload = b"{}"
    manifest = {
        "format_version": 1,
        "checksum": {
            "algorithm": "sha256",
            "files": {"config/user.json": hashlib.sha256(payload).hexdigest()},
        },
        "scope": {"included": ["config/user.json"], "excluded": ["content"]},
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("config/user.json", payload)
        archive.writestr("undeclared.txt", b"hidden")
        archive.writestr("manifest.json", json.dumps(manifest))

    verification = verify_archive(path.name, layout=layout)

    assert verification["verified"] is False
    assert any("undeclared payload" in item for item in verification["problems"])
