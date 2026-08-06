"""Dashboard SQLite 安全快照与原子恢复。

主规格第 10 节：自动升级前使用 SQLite backup API 生成一致快照（WAL 模式下
包含所有已提交数据，不能只复制主库或只依赖 checkpoint）；回退时保持
Dashboard 停止，先隔离 -wal/-shm，再原子恢复主库并执行 integrity/schema
校验。

快照路径约定为 ``manager/recovery/<transaction-id>/dashboard.db``；快照文件
与事务目录使用敏感权限，完成后 fsync 文件与父目录。恢复目录不挂载给
Dashboard、不能通过普通 archive API 列出，由调用方与部署契约保证。

所有路径使用 ``_path_security`` 的 no-follow 原语防止 symlink/reparse
重定向；任何校验失败都 fail closed 并保留现场。
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from ._path_security import (
    assert_contained_no_reparse,
    is_reparse_point,
    open_regular_binary_no_follow,
)

if TYPE_CHECKING:
    from dicepp_data import InstanceLayout

SNAPSHOT_FILENAME = "dashboard.db"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class DashboardDbError(ValueError):
    """Dashboard 数据库快照/恢复契约被破坏，必须 fail closed。"""


def _assert_safe_parent(path: Path) -> None:
    """拒绝直接父目录为 symlink/reparse 的路径。"""
    try:
        assert_contained_no_reparse(path, root=path.parent, allow_missing=True)
    except OSError as exc:
        raise DashboardDbError(f"unsafe path: {path}") from exc


def _sha256_no_follow(path: Path) -> str:
    """对普通文件计算 SHA-256；symlink/目录/缺失一律拒绝。"""
    try:
        with open_regular_binary_no_follow(path) as handle:
            digest = hashlib.sha256()
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
            return digest.hexdigest()
    except OSError as exc:
        raise DashboardDbError(
            f"file is missing, not a regular file, or unsafe: {path}"
        ) from exc


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDWR)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_dir(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    try:
        descriptor = os.open(path, os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def snapshot_dashboard_db(source: Path, target: Path) -> str:
    """用 SQLite backup API 生成一致快照，返回目标文件 SHA-256。

    WAL 模式下未 checkpoint 的已提交数据也会进入快照（backup 从 WAL 读取），
    而不是只复制主库。目标文件权限收紧为 0o600；调用方负责创建目标目录
    （``snapshot_for_transaction`` 以 0o700 创建事务目录）。

    源缺失/非 SQLite/不安全路径抛 ``DashboardDbError``（ValueError），
    调用方 fail closed。
    """
    source = Path(source)
    target = Path(target)
    _assert_safe_parent(source)
    _assert_safe_parent(target)
    if is_reparse_point(target):
        raise DashboardDbError(f"refusing to write through a symlink: {target}")
    if not os.path.lexists(source):
        raise DashboardDbError(f"dashboard database does not exist: {source}")
    if is_reparse_point(source):
        raise DashboardDbError(f"refusing to read through a symlink: {source}")
    # 预创建 0o600 空文件并截断，保证最终权限不受 umask 影响；重试幂等。
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.close(descriptor)
    os.chmod(target, 0o600)
    try:
        source_connection = sqlite3.connect(str(source), timeout=10)
        target_connection = sqlite3.connect(str(target), timeout=10)
        try:
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
            source_connection.close()
    except sqlite3.DatabaseError as exc:
        raise DashboardDbError(
            f"source is not a consistent SQLite database: {source}"
        ) from exc
    _fsync_file(target)
    _fsync_dir(target.parent)
    return _sha256_no_follow(target)


def restore_dashboard_db(target: Path, snapshot: Path, expected_sha256: str) -> None:
    """原子恢复 Dashboard 主库；隔离 -wal/-shm；integrity + schema 校验。

    校验顺序（任一失败抛 ``DashboardDbError`` 并保留现场）：

    1. snapshot 的 SHA-256 必须匹配 ``expected_sha256``；
    2. 隔离并删除 target 旁的 ``-wal``/``-shm`` 侧文件；
    3. 用 backup API 生成恢复主库（临时文件 + fsync + 原子 replace）；
    4. ``PRAGMA integrity_check`` 必须为 ``ok``；
    5. 恢复后的 ``sqlite_master`` 表清单与 snapshot 一致。

    target/snapshot 路径含 symlink 或 reparse 时拒绝（no-follow 原语）。
    """
    target = Path(target)
    snapshot = Path(snapshot)
    if not isinstance(expected_sha256, str) or not _HEX64.fullmatch(expected_sha256):
        raise DashboardDbError(
            "expected_sha256 must be a 64-char lowercase hex digest"
        )
    _assert_safe_parent(target)
    _assert_safe_parent(snapshot)
    if is_reparse_point(target):
        raise DashboardDbError(f"refusing to restore through a symlink: {target}")
    if _sha256_no_follow(snapshot) != expected_sha256:
        raise DashboardDbError(
            "snapshot SHA-256 mismatch; refusing to restore"
        )
    _quarantine_sidecars(target)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.restore")
    try:
        source_connection = sqlite3.connect(str(snapshot), timeout=10)
        restored_connection = sqlite3.connect(str(temporary), timeout=10)
        try:
            source_connection.backup(restored_connection)
        finally:
            restored_connection.close()
            source_connection.close()
        os.chmod(temporary, 0o600)
        _fsync_file(temporary)
        os.replace(temporary, target)
        _fsync_dir(target.parent)
    except sqlite3.DatabaseError as exc:
        raise DashboardDbError(
            f"snapshot is not a valid SQLite database: {snapshot}"
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)
    _verify_restored(target, snapshot)


def restore_for_transaction(
    layout: InstanceLayout,
    request: dict[str, Any],
) -> None:
    """从 linux_handoff request 恢复 Dashboard 主库(回退时由 coordinator 调用)。

    ``request`` 必须携带与 :func:`snapshot_for_transaction` 约定一致的
    ``dashboard_db``(``{"path": "manager/recovery/<tx>/dashboard.db",
    "sha256": <hex>}``)与 ``transaction_id``;path 必须精确等于本事务快照
    路径,任何契约破坏都 fail closed 并保留现场。
    """
    dashboard_db = request.get("dashboard_db")
    transaction_id = request.get("transaction_id")
    if (
        not isinstance(dashboard_db, dict)
        or set(dashboard_db) != {"path", "sha256"}
        or not isinstance(dashboard_db.get("path"), str)
        or not isinstance(dashboard_db.get("sha256"), str)
        or not isinstance(transaction_id, str)
        or not transaction_id
        or transaction_id in {".", ".."}
        or "/" in transaction_id
        or "\\" in transaction_id
    ):
        raise DashboardDbError(
            "request dashboard_db contract is invalid; refusing to restore"
        )
    relative = Path(dashboard_db["path"])
    expected = (
        Path("manager") / "recovery" / transaction_id / SNAPSHOT_FILENAME
    )
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or relative != expected
    ):
        raise DashboardDbError(
            "request dashboard_db path must be the transaction snapshot; "
            "refusing to restore"
        )
    snapshot = Path(layout.root) / relative
    restore_dashboard_db(
        layout.dashboard_db, snapshot, dashboard_db["sha256"]
    )


def snapshot_for_transaction(layout: InstanceLayout, transaction_id: str) -> dict[str, str]:
    """创建 ``manager/recovery/<tx>/dashboard.db`` 快照，供 request 引用。

    返回 ``{"path": "manager/recovery/<tx>/dashboard.db", "sha256": <hex>}``；
    ``path`` 是相对于 ``layout.root`` 的相对路径，与 linux_handoff 的
    ``dashboard_db`` 契约一致。事务目录以 0o700 创建，快照文件 0o600。
    """
    if (
        not isinstance(transaction_id, str)
        or not transaction_id
        or transaction_id in {".", ".."}
        or "/" in transaction_id
        or "\\" in transaction_id
    ):
        raise ValueError("transaction_id must be one path segment")
    recovery_root = Path(layout.manager_recovery_dir)
    transaction_dir = recovery_root / transaction_id
    os.makedirs(transaction_dir, mode=0o700, exist_ok=True)
    snapshot_path = transaction_dir / SNAPSHOT_FILENAME
    sha256 = snapshot_dashboard_db(layout.dashboard_db, snapshot_path)
    relative = Path("manager/recovery") / transaction_id / SNAPSHOT_FILENAME
    return {"path": relative.as_posix(), "sha256": sha256}


def _quarantine_sidecars(target: Path) -> None:
    """把 target 旁的 ``-wal``/``-shm`` 侧文件 rename 到隔离名再删除。

    同目录原子 rename 不跟随 symlink；删除失败时保留隔离现场并 fail closed。
    """
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(target) + suffix)
        if not os.path.lexists(sidecar):
            continue
        quarantine = sidecar.with_name(f".{sidecar.name}.{uuid4().hex}.quarantined")
        os.rename(sidecar, quarantine)
        try:
            os.unlink(quarantine)
        except OSError:
            raise DashboardDbError(
                f"failed to remove quarantined {sidecar.name}; site preserved"
            ) from None


def _verify_restored(target: Path, snapshot: Path) -> None:
    """恢复后校验：integrity ok 且 schema 与快照一致（否则保留现场抛错）。"""
    try:
        target_connection = sqlite3.connect(str(target), timeout=10)
        try:
            check = target_connection.execute("PRAGMA integrity_check").fetchone()
            if check is None or check[0] != "ok":
                raise DashboardDbError(
                    f"restored dashboard database failed integrity check: {check}"
                )
            snapshot_connection = sqlite3.connect(str(snapshot), timeout=10)
            try:
                if _schema_signature(snapshot_connection) != _schema_signature(
                    target_connection
                ):
                    raise DashboardDbError(
                        "restored dashboard database schema differs from snapshot"
                    )
            finally:
                snapshot_connection.close()
        finally:
            target_connection.close()
    except sqlite3.DatabaseError as exc:
        raise DashboardDbError(
            f"restored database is not a readable SQLite database: {target}"
        ) from exc


def _schema_signature(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, str, str], ...]:
    rows = connection.execute(
        "SELECT type, name, COALESCE(sql, '') FROM sqlite_master "
        "ORDER BY type, name, sql"
    ).fetchall()
    return tuple((row[0], row[1], row[2]) for row in rows)


__all__ = [
    "DashboardDbError",
    "snapshot_dashboard_db",
    "restore_dashboard_db",
    "snapshot_for_transaction",
    "restore_for_transaction",
]
