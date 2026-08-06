from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from dicepp_data import InstanceLayout
from dicepp_manager.archive import apply_archive, create_archive


def _create_instance_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO schema_metadata(key, value) VALUES (?, ?)",
            [
                ("application", "dicepp"),
                ("target_name", "instance"),
                ("current_version", "1"),
                ("created_at", "2026-01-01T00:00:00+00:00"),
                ("updated_at", "2026-01-01T00:00:00+00:00"),
            ],
        )
        connection.execute("CREATE TABLE entries (value TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO entries(value) VALUES ('archived')")
        connection.commit()
    finally:
        connection.close()


def _commit_to_wal_then_terminate(database: Path) -> None:
    script = """
import os
import sqlite3
import sys

database = sys.argv[1]
connection = sqlite3.connect(database)
connection.execute('PRAGMA journal_mode=WAL')
connection.execute("INSERT INTO entries(value) VALUES ('stale-after-archive')")
connection.commit()
os._exit(0)
"""
    subprocess.run(
        [sys.executable, "-c", script, str(database)],
        check=True,
        capture_output=True,
    )


def test_restore_discards_wal_written_by_a_terminated_runtime(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    database = layout.data_root / "dicepp.db"
    _create_instance_database(database)
    summary, _manifest = create_archive(layout=layout)

    _commit_to_wal_then_terminate(database)
    assert Path(f"{database}-wal").exists()

    result = apply_archive(summary["filename"], layout=layout)

    assert result["failed_entries"] == []
    assert not Path(f"{database}-wal").exists()
    assert not Path(f"{database}-shm").exists()
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("SELECT value FROM entries ORDER BY value").fetchall() == [
            ("archived",),
        ]
    finally:
        connection.close()
