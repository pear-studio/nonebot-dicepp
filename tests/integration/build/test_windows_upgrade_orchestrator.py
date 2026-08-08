from __future__ import annotations

import json
import sqlite3

from dicepp_data import InstanceLayout
from dicepp_manager.store import JOURNAL_SQL, OPERATIONS_SQL

from scripts.build.windows_upgrade_orchestrator import _copy_runtime_diagnostics


def test_runtime_diagnostics_include_latest_manager_state(tmp_path) -> None:
    root = tmp_path / "instance"
    diagnostics = tmp_path / "diagnostics"
    layout = InstanceLayout.from_root(root)
    layout.manager_db.parent.mkdir(parents=True)
    with sqlite3.connect(layout.manager_db) as connection:
        connection.execute(OPERATIONS_SQL)
        connection.execute(JOURNAL_SQL)
        connection.execute(
            """INSERT INTO manager_operations (
                   operation_id, runtime_unit_id, action, status, created_at,
                   updated_at, message, detail
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "operation-1",
                "dicepp-runtime",
                "upgrade.install",
                "interrupted",
                "2026-08-08T01:00:00Z",
                "2026-08-08T01:01:00Z",
                "Manager restarted",
                '{"reason":"manager_restart"}',
            ),
        )
        connection.execute(
            """INSERT INTO manager_journal (
                   transaction_id, operation_id, kind, phase, status,
                   updated_at, detail
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                "transaction-1",
                "operation-1",
                "upgrade",
                "awaiting_windows_restart",
                "interrupted",
                "2026-08-08T01:02:00Z",
                '{"target_version":"3.0.0rc21"}',
            ),
        )

    _copy_runtime_diagnostics(root, diagnostics)

    snapshot = json.loads(
        (diagnostics / "manager-state.json").read_text(encoding="utf-8")
    )
    assert snapshot["journal"] == {
        "transaction_id": "transaction-1",
        "operation_id": "operation-1",
        "kind": "upgrade",
        "phase": "awaiting_windows_restart",
        "status": "interrupted",
        "updated_at": "2026-08-08T01:02:00Z",
        "detail": {"target_version": "3.0.0rc21"},
    }
    assert snapshot["operation"]["operation_id"] == "operation-1"
    assert snapshot["operation"]["detail"] == {"reason": "manager_restart"}
