"""Dashboard SQLite 安全快照/恢复模块的集成测试（真实 sqlite3 文件）。

保护的行为契约（主规格第 10 节）：
- 快照用 SQLite backup API，WAL 模式下未 checkpoint 的已提交数据也完整进入；
- 快照文件 0o600、事务目录 0o700，完成后无 -wal/-shm 残留到目标旁；
- 恢复先隔离 -wal/-shm，再原子恢复主库，integrity/schema 校验通过才算成功；
- sha256 不匹配或路径含 symlink 一律 fail closed，现场保留。
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

import pytest

from dicepp_data import InstanceLayout
from dicepp_manager.dashboard_db import (
    DashboardDbError,
    restore_dashboard_db,
    restore_for_transaction,
    snapshot_dashboard_db,
    snapshot_for_transaction,
)
from tests.support.fs_utils import symlink_or_skip


def _create_wal_db(path: Path) -> sqlite3.Connection:
    """在 path 创建 WAL 模式数据库并提交数据，返回保持打开的连接。

    连接不关闭时自动 checkpoint 不会触发，-wal 中保留未 checkpoint 数据，
    用于验证快照包含 WAL 中全部已提交内容。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    connection.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY, value TEXT)")
    connection.execute("CREATE INDEX facts_value ON facts (value)")
    connection.executemany(
        "INSERT INTO facts (value) VALUES (?)",
        [(f"row-{index}",) for index in range(25)],
    )
    connection.commit()
    return connection


def _snapshot_dir(layout: InstanceLayout, seed: str) -> Path:
    directory = layout.manager_recovery_dir / (seed * 32)
    directory.mkdir(parents=True)
    return directory


def _schema_rows(path: Path) -> list[tuple[str, str, str]]:
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute(
            "SELECT type, name, COALESCE(sql, '') FROM sqlite_master "
            "ORDER BY type, name, sql"
        ).fetchall()


def test_snapshot_contains_uncheckpointed_wal_commits(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    connection = _create_wal_db(layout.dashboard_db)
    try:
        snapshot = _snapshot_dir(layout, "a") / "dashboard.db"
        # 证明提交数据仍在 WAL 中而未 checkpoint 进主库
        wal_path = Path(str(layout.dashboard_db) + "-wal")
        assert wal_path.stat().st_size > 0

        snapshot_dashboard_db(layout.dashboard_db, snapshot)

        with closing(sqlite3.connect(snapshot)) as restored:
            rows = restored.execute(
                "SELECT id, value FROM facts ORDER BY id"
            ).fetchall()
            assert restored.execute(
                "SELECT COUNT(*) FROM sqlite_master"
            ).fetchone()[0] > 0
        assert rows == [(index, f"row-{index - 1}") for index in range(1, 26)]
    finally:
        connection.close()


def test_snapshot_digest_and_posix_permissions_are_consistent(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    connection = _create_wal_db(layout.dashboard_db)
    try:
        result = snapshot_for_transaction(layout, "b" * 32)

        snapshot = layout.manager_recovery_dir / ("b" * 32) / "dashboard.db"
        if os.name != "nt":
            assert stat.S_IMODE(snapshot.stat().st_mode) == 0o600
            assert stat.S_IMODE(snapshot.parent.stat().st_mode) == 0o700
        assert result["sha256"] == hashlib.sha256(snapshot.read_bytes()).hexdigest()
    finally:
        connection.close()


def test_snapshot_leaves_no_wal_sidecars_next_to_target(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    connection = _create_wal_db(layout.dashboard_db)
    try:
        snapshot = _snapshot_dir(layout, "c") / "dashboard.db"
        snapshot_dashboard_db(layout.dashboard_db, snapshot)

        assert not Path(str(snapshot) + "-wal").exists()
        assert not Path(str(snapshot) + "-shm").exists()
    finally:
        connection.close()


def test_restore_recovers_corrupted_target_with_integrity_and_schema(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    connection = _create_wal_db(layout.dashboard_db)
    try:
        snapshot = _snapshot_dir(layout, "d") / "dashboard.db"
        digest = snapshot_dashboard_db(layout.dashboard_db, snapshot)
    finally:
        connection.close()  # 模拟维护窗口内 Dashboard 已停止

    layout.dashboard_db.write_bytes(b"corrupted-not-a-database" * 8)

    restore_dashboard_db(layout.dashboard_db, snapshot, digest)

    with closing(sqlite3.connect(layout.dashboard_db)) as restored:
        assert restored.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        values = restored.execute(
            "SELECT value FROM facts ORDER BY id"
        ).fetchall()
    assert values == [(f"row-{index}",) for index in range(25)]
    assert _schema_rows(snapshot) == _schema_rows(layout.dashboard_db)


def test_restore_quarantines_target_wal_shm_sidecars(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    connection = _create_wal_db(layout.dashboard_db)
    try:
        snapshot = _snapshot_dir(layout, "e") / "dashboard.db"
        digest = snapshot_dashboard_db(layout.dashboard_db, snapshot)
    finally:
        connection.close()

    # 模拟目标主库带有残留 WAL/SHM 侧文件（绕过 SQLite 手工写入）
    layout.dashboard_db.write_bytes(b"target-with-stale-wal")
    sidecar_wal = Path(str(layout.dashboard_db) + "-wal")
    sidecar_shm = Path(str(layout.dashboard_db) + "-shm")
    sidecar_wal.write_bytes(b"stale wal pages")
    sidecar_shm.write_bytes(b"stale shm header")

    restore_dashboard_db(layout.dashboard_db, snapshot, digest)

    assert not sidecar_wal.exists()
    assert not sidecar_shm.exists()
    with closing(sqlite3.connect(layout.dashboard_db)) as restored:
        assert restored.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert restored.execute("SELECT COUNT(*) FROM facts").fetchone() == (25,)


def test_restore_rejects_mismatched_sha_and_preserves_target(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    connection = _create_wal_db(layout.dashboard_db)
    try:
        snapshot = _snapshot_dir(layout, "f") / "dashboard.db"
        snapshot_dashboard_db(layout.dashboard_db, snapshot)
    finally:
        connection.close()

    layout.dashboard_db.write_bytes(b"precious-existing-target")

    with pytest.raises(DashboardDbError, match="SHA-256 mismatch"):
        restore_dashboard_db(layout.dashboard_db, snapshot, "0" * 64)

    assert layout.dashboard_db.read_bytes() == b"precious-existing-target"
    assert not list(layout.dashboard_data_dir.glob("*.quarantined"))


def test_restore_rejects_symlink_snapshot_and_target(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    connection = _create_wal_db(layout.dashboard_db)
    try:
        snapshot = _snapshot_dir(layout, "g") / "dashboard.db"
        digest = snapshot_dashboard_db(layout.dashboard_db, snapshot)

        symlink_snapshot = layout.manager_recovery_dir / ("h" * 32) / "evil.db"
        symlink_snapshot.parent.mkdir(parents=True)
        symlink_or_skip(symlink_snapshot, snapshot)
        with pytest.raises(DashboardDbError, match="unsafe"):
            restore_dashboard_db(layout.dashboard_db, symlink_snapshot, digest)

        symlink_target = layout.dashboard_data_dir / "linked.db"
        symlink_target.parent.mkdir(parents=True, exist_ok=True)
        symlink_or_skip(symlink_target, tmp_path / "outside.db")
        with pytest.raises(DashboardDbError, match="symlink"):
            restore_dashboard_db(symlink_target, snapshot, digest)
    finally:
        connection.close()


def test_snapshot_rejects_symlink_source_and_target(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    connection = _create_wal_db(layout.dashboard_db)
    try:
        snapshot = _snapshot_dir(layout, "i") / "dashboard.db"

        symlink_source = layout.dashboard_data_dir / "linked-source.db"
        symlink_source.parent.mkdir(parents=True, exist_ok=True)
        symlink_or_skip(symlink_source, layout.dashboard_db)
        with pytest.raises(DashboardDbError, match="symlink"):
            snapshot_dashboard_db(symlink_source, snapshot)

        symlink_target = layout.manager_recovery_dir / ("j" * 32) / "linked.db"
        symlink_target.parent.mkdir(parents=True)
        symlink_or_skip(symlink_target, tmp_path / "outside.db")
        with pytest.raises(DashboardDbError, match="symlink"):
            snapshot_dashboard_db(layout.dashboard_db, symlink_target)
    finally:
        connection.close()


def test_snapshot_for_transaction_returns_tx_scoped_contract(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    connection = _create_wal_db(layout.dashboard_db)
    try:
        transaction_id = "k" * 32
        result = snapshot_for_transaction(layout, transaction_id)

        snapshot = layout.manager_recovery_dir / transaction_id / "dashboard.db"
        assert result == {
            "path": f"manager/recovery/{transaction_id}/dashboard.db",
            "sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        }
        # 与协议层相对路径契约兼容：相对路径、无 ..、无绝对路径
        assert ".." not in result["path"].split("/")
        assert not Path(result["path"]).is_absolute()
    finally:
        connection.close()


def test_restore_for_transaction_recovers_target_from_handoff_request(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    connection = _create_wal_db(layout.dashboard_db)
    try:
        transaction_id = "l" * 32
        snapshot = snapshot_for_transaction(layout, transaction_id)
        request = {
            "transaction_id": transaction_id,
            "dashboard_db": snapshot,
        }
    finally:
        connection.close()  # 模拟维护窗口内 Dashboard 已停止

    layout.dashboard_db.write_bytes(b"corrupted-by-upgrade")

    restore_for_transaction(layout, request)

    with closing(sqlite3.connect(layout.dashboard_db)) as restored:
        assert restored.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert restored.execute("SELECT COUNT(*) FROM facts").fetchone() == (25,)


def test_restore_for_transaction_rejects_foreign_snapshot_path(
    tmp_path: Path,
) -> None:
    """request 中的 path 必须精确等于本事务快照路径，否则 fail closed。"""
    layout = InstanceLayout.from_root(tmp_path)
    connection = _create_wal_db(layout.dashboard_db)
    try:
        transaction_id = "m" * 32
        snapshot = snapshot_for_transaction(layout, transaction_id)
        other = snapshot_for_transaction(layout, "n" * 32)
        request = {
            "transaction_id": transaction_id,
            "dashboard_db": {
                "path": other["path"],  # 指向另一个事务的快照
                "sha256": other["sha256"],
            },
        }
    finally:
        connection.close()

    layout.dashboard_db.write_bytes(b"precious-existing-target")

    with pytest.raises(DashboardDbError, match="transaction snapshot"):
        restore_for_transaction(layout, request)

    assert layout.dashboard_db.read_bytes() == b"precious-existing-target"


def test_restore_for_transaction_rejects_invalid_contract(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    connection = _create_wal_db(layout.dashboard_db)
    try:
        request = {
            "transaction_id": "o" * 32,
            "dashboard_db": {"path": "manager/recovery/../other.db", "sha256": "0" * 64},
        }
    finally:
        connection.close()

    with pytest.raises(DashboardDbError, match="contract is invalid|transaction snapshot"):
        restore_for_transaction(layout, request)

    assert layout.dashboard_db.exists()
