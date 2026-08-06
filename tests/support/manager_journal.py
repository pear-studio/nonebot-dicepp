"""Support helpers for Manager journal databases.

These helpers build real-schema manager journal databases so unit tests do
not open SQLite connections or write files directly (see the unit-test
layout policy and ``docs/dev/testing.md``).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from dicepp_manager.store import JOURNAL_SQL


def write_manager_journal(
    db_path: Path,
    rows: list[dict[str, str]],
) -> None:
    """Create ``db_path`` with the real Manager journal schema and insert rows.

    Each row carries ``transaction_id``, ``kind``, ``phase``, ``status`` and
    ``updated_at``; ``updated_at`` defaults to a fixed timestamp when omitted.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.execute(JOURNAL_SQL)
    connection.executemany(
        "INSERT INTO manager_journal "
        "(transaction_id, kind, phase, status, updated_at) "
        "VALUES (:transaction_id, :kind, :phase, :status, :updated_at)",
        rows,
    )
    connection.commit()
    connection.close()
