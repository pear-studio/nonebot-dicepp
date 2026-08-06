"""Linux Manager handoff 协议层：request/decision/result 事务文件契约。

该模块只实现跨进程协议的文件契约，不包含 Docker 或 Manager 业务逻辑。
文件所有权（写入角色）由调用方保证：

- ``request.json``：来源 Manager 唯一写入，发布后不可变；
- ``decision.json``：目标 Manager 写入，**first-write-wins**，是事务方向
  唯一权威且不可逆的提交点；
- ``result.json``：来源镜像中的 Updater helper 角色写入，记录容器切换的
  权威结果。

所有路径必须位于受信任事务目录内；拒绝 symlink/reparse 与异常文件类型。
写入采用同目录临时文件 + ``fsync`` + 原子发布；decision 发布使用
hard-link no-replace 原语（``os.link``），禁止普通 replace 覆盖。

仅在 Linux 运行；模块本身保持不依赖平台特性即可正常导入。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4

from ._file_utils import _atomic_json
from ._path_security import (
    assert_contained_no_reparse,
    open_regular_binary_no_follow,
)

LINUX_MANAGER_HANDOFF_FORMAT = 1

BOT_CURRENT_ALIAS = "ghcr.io/pear-studio/nonebot-dicepp:dicepp-current"
DASHBOARD_MANAGER_CURRENT_ALIAS = (
    "ghcr.io/pear-studio/dicepp-dashboard:dicepp-current"
)
CURRENT_ALIAS_NAMES: Mapping[str, str] = MappingProxyType(
    {
        "bot": BOT_CURRENT_ALIAS,
        "dashboard_manager": DASHBOARD_MANAGER_CURRENT_ALIAS,
    }
)

#: decision 枚举：commit 是唯一不可逆提交点；rollback 只出现在目标 Manager
#: 已停止目标 Runtime、要求恢复来源 Manager 时。
DECISION_COMMIT = "commit"
DECISION_ROLLBACK = "rollback"
_KNOWN_DECISIONS = frozenset({DECISION_COMMIT, DECISION_ROLLBACK})

#: result 枚举：记录 Manager 容器切换的权威结果。
RESULT_TARGET_COMMITTED = "target-committed"
RESULT_SOURCE_RESTORED = "source-restored"
RESULT_RESTORE_FAILED = "restore-failed"
_KNOWN_RESULTS = frozenset(
    {RESULT_TARGET_COMMITTED, RESULT_SOURCE_RESTORED, RESULT_RESTORE_FAILED}
)

_REQUEST_FILENAME = "linux-manager-switch.request.json"
_DECISION_FILENAME = "linux-manager-switch.decision.json"
_RESULT_FILENAME = "linux-manager-switch.result.json"

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9._-]+$")


class HandoffProtocolError(ValueError):
    """A Linux Manager handoff contract file is absent, invalid or conflicted."""

    def __init__(self, message: str, *, code: str = "handoff_protocol_error") -> None:
        super().__init__(message)
        self.code = code


def _validate_identity(
    value: object,
    *,
    label: str,
    pattern: re.Pattern[str],
) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise HandoffProtocolError(
            f"{label} has an invalid value",
            code="handoff_identity_invalid",
        )
    return value


def _validate_iso_timestamp(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise HandoffProtocolError(
            f"{label} must be an ISO-8601 timestamp",
            code="handoff_timestamp_invalid",
        )
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise HandoffProtocolError(
            f"{label} must be an ISO-8601 timestamp",
            code="handoff_timestamp_invalid",
        ) from exc
    return value


def _validate_container_identity(value: object, *, label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise HandoffProtocolError(
            f"{label} must be a container identity object",
            code="handoff_request_invalid",
        )
    if set(value) != {"container_id", "image_id"}:
        raise HandoffProtocolError(
            f"{label} must carry container_id and image_id",
            code="handoff_request_invalid",
        )
    return {
        "container_id": _validate_identity(
            value["container_id"], label=f"{label} container_id", pattern=_CONTAINER_ID
        ),
        "image_id": _validate_identity(
            value["image_id"], label=f"{label} image_id", pattern=_IMAGE_ID
        ),
    }


def _validate_snapshot_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise HandoffProtocolError(
            "dashboard_db path must be a non-empty relative path",
            code="handoff_request_invalid",
        )
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise HandoffProtocolError(
            "dashboard_db path must stay inside the transaction directory",
            code="handoff_request_invalid",
        )
    return value


def validate_request(payload: Any) -> dict[str, Any]:
    """Validate a request payload and return the normalized mapping."""
    if not isinstance(payload, dict):
        raise HandoffProtocolError(
            "request must be a JSON object", code="handoff_request_invalid"
        )
    if payload.get("format_version") != LINUX_MANAGER_HANDOFF_FORMAT:
        raise HandoffProtocolError(
            "request format version is unsupported",
            code="handoff_format_unsupported",
        )
    transaction_id = _validate_identity(
        payload.get("transaction_id"),
        label="transaction_id",
        pattern=_HEX32,
    )
    operation_id = _validate_identity(
        payload.get("operation_id"),
        label="operation_id",
        pattern=_HEX32,
    )
    for label in ("source_version", "target_version", "compose_project"):
        if not isinstance(payload.get(label), str) or not payload[label]:
            raise HandoffProtocolError(
                f"{label} must be a non-empty string",
                code="handoff_request_invalid",
            )
    manager = payload.get("manager")
    if not isinstance(manager, dict):
        raise HandoffProtocolError(
            "manager must be a container identity object",
            code="handoff_request_invalid",
        )
    if set(manager) != {"container_id", "name", "backup_name", "image_id"}:
        raise HandoffProtocolError(
            "manager must carry container_id, name, backup_name and image_id",
            code="handoff_request_invalid",
        )
    manager_id = _validate_identity(
        manager["container_id"], label="manager container_id", pattern=_CONTAINER_ID
    )
    manager_image = _validate_identity(
        manager["image_id"], label="manager image_id", pattern=_IMAGE_ID
    )
    manager_name = manager["name"]
    backup_name = manager["backup_name"]
    if not isinstance(manager_name, str) or not manager_name:
        raise HandoffProtocolError(
            "manager name must be non-empty", code="handoff_request_invalid"
        )
    if not isinstance(backup_name, str) or not backup_name:
        raise HandoffProtocolError(
            "manager backup_name must be non-empty", code="handoff_request_invalid"
        )
    target_manager_image_id = _validate_identity(
        payload.get("target_manager_image_id"),
        label="target_manager_image_id",
        pattern=_IMAGE_ID,
    )
    bot = _validate_container_identity(payload.get("bot"), label="bot")
    dashboard = _validate_container_identity(payload.get("dashboard"), label="dashboard")
    target_images = payload.get("target_images")
    if (
        not isinstance(target_images, dict)
        or set(target_images) != {"bot", "dashboard"}
        or any(
            not isinstance(target_images[role], str)
            or not _IMAGE_ID.fullmatch(target_images[role])
            for role in ("bot", "dashboard")
        )
    ):
        raise HandoffProtocolError(
            "target_images must map bot and dashboard to immutable image IDs",
            code="handoff_request_invalid",
        )
    pre_upgrade_archive = payload.get("pre_upgrade_archive")
    if (
        not isinstance(pre_upgrade_archive, str)
        or not _SAFE_FILENAME.fullmatch(pre_upgrade_archive)
    ):
        raise HandoffProtocolError(
            "pre_upgrade_archive must be a safe archive filename",
            code="handoff_request_invalid",
        )
    dashboard_db = payload.get("dashboard_db")
    if (
        not isinstance(dashboard_db, dict)
        or set(dashboard_db) != {"path", "sha256"}
        or not isinstance(dashboard_db["sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", dashboard_db["sha256"])
    ):
        raise HandoffProtocolError(
            "dashboard_db must carry a relative path and sha256 digest",
            code="handoff_request_invalid",
        )
    snapshot_path = _validate_snapshot_path(dashboard_db["path"])
    original_running = payload.get("original_running")
    if (
        not isinstance(original_running, dict)
        or set(original_running) != {"bot", "dashboard"}
        or any(type(original_running[role]) is not bool for role in ("bot", "dashboard"))
    ):
        raise HandoffProtocolError(
            "original_running must map bot and dashboard to booleans",
            code="handoff_request_invalid",
        )
    created_at = _validate_iso_timestamp(
        payload.get("created_at"), label="created_at"
    )
    for label in ("startup_deadline_seconds", "transaction_deadline_seconds"):
        if (
            type(payload.get(label)) is not int
            or payload[label] <= 0
        ):
            raise HandoffProtocolError(
                f"{label} must be a positive integer",
                code="handoff_request_invalid",
            )
    current_aliases = payload.get("current_aliases")
    if (
        not isinstance(current_aliases, dict)
        or set(current_aliases) != {"bot", "dashboard_manager"}
    ):
        raise HandoffProtocolError(
            "current_aliases must map bot and dashboard_manager",
            code="handoff_request_invalid",
        )
    normalized_aliases: dict[str, dict[str, str]] = {}
    for role in ("bot", "dashboard_manager"):
        alias = current_aliases[role]
        if (
            not isinstance(alias, dict)
            or set(alias) != {"name", "image_id"}
            or alias["name"] != CURRENT_ALIAS_NAMES[role]
        ):
            raise HandoffProtocolError(
                f"current_aliases.{role} must use the managed dicepp-current tag",
                code="handoff_request_invalid",
            )
        normalized_aliases[role] = {
            "name": alias["name"],
            "image_id": _validate_identity(
                alias["image_id"],
                label=f"current_aliases.{role} image_id",
                pattern=_IMAGE_ID,
            ),
        }
    restart_policies = payload.get("restart_policies")
    if (
        not isinstance(restart_policies, dict)
        or set(restart_policies) != {"manager", "bot", "dashboard"}
        or any(
            not isinstance(restart_policies[role], str)
            or not restart_policies[role]
            for role in ("manager", "bot", "dashboard")
        )
    ):
        raise HandoffProtocolError(
            "restart_policies must map manager, bot and dashboard",
            code="handoff_request_invalid",
        )
    labels = payload.get("labels")
    if (
        not isinstance(labels, dict)
        or set(labels) != {"transaction", "role"}
        or not isinstance(labels["transaction"], str)
        or not labels["transaction"]
        or not isinstance(labels["role"], str)
        or not labels["role"]
    ):
        raise HandoffProtocolError(
            "labels must carry transaction and role label names",
            code="handoff_request_invalid",
        )
    return {
        "format_version": LINUX_MANAGER_HANDOFF_FORMAT,
        "transaction_id": transaction_id,
        "operation_id": operation_id,
        "source_version": payload["source_version"],
        "target_version": payload["target_version"],
        "compose_project": payload["compose_project"],
        "manager": {
            "container_id": manager_id,
            "name": manager_name,
            "backup_name": backup_name,
            "image_id": manager_image,
        },
        "target_manager_image_id": target_manager_image_id,
        "bot": bot,
        "dashboard": dashboard,
        "target_images": {
            role: target_images[role] for role in ("bot", "dashboard")
        },
        "pre_upgrade_archive": pre_upgrade_archive,
        "dashboard_db": {
            "path": snapshot_path,
            "sha256": dashboard_db["sha256"],
        },
        "original_running": {
            role: original_running[role] for role in ("bot", "dashboard")
        },
        "created_at": created_at,
        "startup_deadline_seconds": payload["startup_deadline_seconds"],
        "transaction_deadline_seconds": payload["transaction_deadline_seconds"],
        "current_aliases": normalized_aliases,
        "restart_policies": {
            role: restart_policies[role]
            for role in ("manager", "bot", "dashboard")
        },
        "labels": {
            "transaction": labels["transaction"],
            "role": labels["role"],
        },
    }


def validate_decision(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HandoffProtocolError(
            "decision must be a JSON object", code="handoff_decision_invalid"
        )
    if payload.get("format_version") != LINUX_MANAGER_HANDOFF_FORMAT:
        raise HandoffProtocolError(
            "decision format version is unsupported",
            code="handoff_format_unsupported",
        )
    transaction_id = _validate_identity(
        payload.get("transaction_id"),
        label="transaction_id",
        pattern=_HEX32,
    )
    operation_id = _validate_identity(
        payload.get("operation_id"),
        label="operation_id",
        pattern=_HEX32,
    )
    value = payload.get("value")
    if value not in _KNOWN_DECISIONS:
        raise HandoffProtocolError(
            "decision value must be commit or rollback",
            code="handoff_decision_invalid",
        )
    created_at = _validate_iso_timestamp(payload.get("created_at"), label="created_at")
    return {
        "format_version": LINUX_MANAGER_HANDOFF_FORMAT,
        "transaction_id": transaction_id,
        "operation_id": operation_id,
        "value": value,
        "created_at": created_at,
    }


def validate_result(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HandoffProtocolError(
            "result must be a JSON object", code="handoff_result_invalid"
        )
    if payload.get("format_version") != LINUX_MANAGER_HANDOFF_FORMAT:
        raise HandoffProtocolError(
            "result format version is unsupported",
            code="handoff_format_unsupported",
        )
    transaction_id = _validate_identity(
        payload.get("transaction_id"),
        label="transaction_id",
        pattern=_HEX32,
    )
    operation_id = _validate_identity(
        payload.get("operation_id"),
        label="operation_id",
        pattern=_HEX32,
    )
    value = payload.get("value")
    if value not in _KNOWN_RESULTS:
        raise HandoffProtocolError(
            "result value is not a known handoff outcome",
            code="handoff_result_invalid",
        )
    created_at = _validate_iso_timestamp(payload.get("created_at"), label="created_at")
    error = payload.get("error")
    if value == RESULT_RESTORE_FAILED:
        if (
            not isinstance(error, dict)
            or set(error) != {"container_id", "image_id", "summary"}
            or not isinstance(error["summary"], str)
            or not error["summary"]
        ):
            raise HandoffProtocolError(
                "restore-failed result must record container identity and a summary",
                code="handoff_result_invalid",
            )
        error = {
            "container_id": _validate_identity(
                error["container_id"],
                label="error container_id",
                pattern=_CONTAINER_ID,
            ),
            "image_id": _validate_identity(
                error["image_id"],
                label="error image_id",
                pattern=_IMAGE_ID,
            ),
            "summary": error["summary"],
        }
        return {
            "format_version": LINUX_MANAGER_HANDOFF_FORMAT,
            "transaction_id": transaction_id,
            "operation_id": operation_id,
            "value": value,
            "created_at": created_at,
            "error": error,
        }
    if error is not None:
        raise HandoffProtocolError(
            "error detail is only allowed for restore-failed",
            code="handoff_result_invalid",
        )
    return {
        "format_version": LINUX_MANAGER_HANDOFF_FORMAT,
        "transaction_id": transaction_id,
        "operation_id": operation_id,
        "value": value,
        "created_at": created_at,
    }


def _trusted_parent(path: Path, *, root: Path | None = None) -> None:
    """Refuse to touch files below a reparse/symlinked directory.

    When ``root`` (the trusted transaction root, e.g. ``manager/recovery``)
    is provided, every existing component between the root and the file is
    checked for reparse points, so an ancestor symlink cannot redirect the
    transaction files elsewhere.
    """
    try:
        if root is not None:
            assert_contained_no_reparse(path, root=root, allow_missing=True)
        else:
            assert_contained_no_reparse(path, root=path.parent, allow_missing=True)
    except OSError as exc:
        raise HandoffProtocolError(
            "handoff transaction directory is unsafe",
            code="handoff_path_unsafe",
        ) from exc


def _read_json_no_follow(path: Path) -> dict[str, Any]:
    try:
        with open_regular_binary_no_follow(path) as handle:
            try:
                value = json.load(handle)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise HandoffProtocolError(
                    "handoff file is not valid JSON",
                    code="handoff_file_corrupt",
                ) from exc
    except OSError as exc:
        raise HandoffProtocolError(
            "handoff file is not a regular file or is unsafe",
            code="handoff_file_corrupt",
        ) from exc
    if not isinstance(value, dict):
        raise HandoffProtocolError(
            "handoff file root must be a JSON object",
            code="handoff_file_corrupt",
        )
    return value


def _atomic_json_no_follow(path: Path, value: Mapping[str, Any]) -> None:
    """Durable atomic JSON write with no-follow reads and parent fsync."""
    _trusted_parent(path)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_parent(path)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_parent(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    try:
        fd = os.open(path.parent, os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish_no_replace(temporary: Path, final: Path) -> bool:
    """Atomically publish *temporary* as *final*; False if *final* exists.

    Once the hard link is created the final file is durably published; a
    failure to remove the temporary link afterwards must not be reported as a
    publish failure (the caller's cleanup retries it best-effort).
    """
    try:
        os.link(temporary, final)
    except FileExistsError:
        return False
    try:
        os.unlink(temporary)
    except OSError:
        pass
    _fsync_parent(final)
    return True


def _entry_state(path: Path) -> str:
    """Classify the final handoff file: absent / valid / corrupt.

    A corrupt or unsafe entry must never be mistaken for absence: an
    unreadable ``decision`` is a fail-closed condition, not "no decision".
    """
    try:
        if not os.path.lexists(path):
            return "absent"
    except OSError as exc:
        raise HandoffProtocolError(
            "handoff path cannot be inspected",
            code="handoff_path_unsafe",
        ) from exc
    try:
        _read_json_no_follow(path)
        return "valid"
    except HandoffProtocolError:
        return "corrupt"


def _write_json_no_replace(path: Path, normalized: Mapping[str, Any]) -> str:
    """Durable no-replace publish; on conflict return the existing payload."""
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(normalized, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        published = _publish_no_replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    if published:
        return ""
    return json.dumps(_read_json_no_follow(path), ensure_ascii=False, sort_keys=True)


def write_request(
    path: Path, payload: Mapping[str, Any], *, root: Path | None = None
) -> None:
    """Write the immutable request; identical rewrite is idempotent.

    Publishing is first-write-wins like the decision, so a concurrent writer
    can never silently replace an already published request.
    """
    normalized = validate_request(payload)
    _trusted_parent(path, root=root)
    state = _entry_state(path)
    if state == "corrupt":
        raise HandoffProtocolError(
            "an existing request file is corrupt; fail closed",
            code="handoff_request_conflict",
        )
    if state == "valid":
        existing = validate_request(_read_json_no_follow(path))
        if existing != normalized:
            raise HandoffProtocolError(
                "request already exists with different content; it is immutable",
                code="handoff_request_conflict",
            )
        return
    published = _write_json_no_replace(path, normalized)
    if published:
        existing = validate_request(json.loads(published))
        if existing != normalized:
            raise HandoffProtocolError(
                "request already exists with different content; it is immutable",
                code="handoff_request_conflict",
            )


def read_request(path: Path, *, root: Path | None = None) -> dict[str, Any]:
    """Read and validate the immutable request; raises if absent or invalid."""
    _trusted_parent(path, root=root)
    return validate_request(_read_json_no_follow(path))


def write_decision(
    path: Path, payload: Mapping[str, Any], *, root: Path | None = None
) -> str:
    """Publish the first-write-wins decision; return the authoritative value.

    The final file can never be replaced.  An existing decision with the same
    semantic binding (format, transaction, operation, value) is accepted
    idempotently; anything else fails closed.
    """
    normalized = validate_decision(payload)
    _trusted_parent(path, root=root)
    state = _entry_state(path)
    if state == "valid":
        existing = validate_decision(_read_json_no_follow(path))
        if _decision_binding(existing) != _decision_binding(normalized):
            raise HandoffProtocolError(
                "decision already exists with a different value; "
                "first-write-wins forbids replacement",
                code="handoff_decision_conflict",
            )
        return existing["value"]
    if state == "corrupt":
        raise HandoffProtocolError(
            "an existing decision file is corrupt; fail closed",
            code="handoff_decision_conflict",
        )
    published = _write_json_no_replace(path, normalized)
    if published:
        # Lost the race: the other writer won the no-replace publish.
        existing = validate_decision(json.loads(published))
        if _decision_binding(existing) != _decision_binding(normalized):
            raise HandoffProtocolError(
                "decision already exists with a different value; "
                "first-write-wins forbids replacement",
                code="handoff_decision_conflict",
            )
        return existing["value"]
    return normalized["value"]


def _decision_binding(payload: Mapping[str, Any]) -> tuple[object, object, object, object]:
    return (
        payload["format_version"],
        payload["transaction_id"],
        payload["operation_id"],
        payload["value"],
    )


def read_decision(
    path: Path,
    *,
    transaction_id: str | None = None,
    operation_id: str | None = None,
    root: Path | None = None,
) -> str | None:
    """Return the authoritative decision value, or None when absent.

    When transaction/operation identity is provided it must match the file.
    """
    _trusted_parent(path, root=root)
    state = _entry_state(path)
    if state == "absent":
        return None
    if state == "corrupt":
        raise HandoffProtocolError(
            "an existing decision file is corrupt; fail closed",
            code="handoff_decision_conflict",
        )
    existing = validate_decision(_read_json_no_follow(path))
    if (
        transaction_id is not None
        and existing["transaction_id"] != transaction_id
    ):
        raise HandoffProtocolError(
            "decision belongs to another transaction",
            code="handoff_decision_foreign_transaction",
        )
    if (
        operation_id is not None
        and existing["operation_id"] != operation_id
    ):
        raise HandoffProtocolError(
            "decision belongs to another operation",
            code="handoff_decision_foreign_operation",
        )
    return existing["value"]


def write_result(
    path: Path, payload: Mapping[str, Any], *, root: Path | None = None
) -> None:
    """Write the Updater result; only the Updater helper role may call this.

    A result may be rewritten by another Updater helper instance of the same
    role, but an existing corrupt/unsafe file must never be silently replaced:
    that would destroy the evidence of the failed recovery site.
    """
    normalized = validate_result(payload)
    _trusted_parent(path, root=root)
    if _entry_state(path) == "corrupt":
        raise HandoffProtocolError(
            "an existing result file is corrupt; fail closed",
            code="handoff_result_conflict",
        )
    _atomic_json_no_follow(path, normalized)


def read_result(
    path: Path, *, root: Path | None = None
) -> dict[str, Any] | None:
    """Read the Updater result, or None when absent."""
    _trusted_parent(path, root=root)
    state = _entry_state(path)
    if state == "absent":
        return None
    return validate_result(_read_json_no_follow(path))


__all__ = [
    "LINUX_MANAGER_HANDOFF_FORMAT",
    "DECISION_COMMIT",
    "DECISION_ROLLBACK",
    "RESULT_TARGET_COMMITTED",
    "RESULT_SOURCE_RESTORED",
    "RESULT_RESTORE_FAILED",
    "_REQUEST_FILENAME",
    "_DECISION_FILENAME",
    "_RESULT_FILENAME",
    "HandoffProtocolError",
    "validate_request",
    "validate_decision",
    "validate_result",
    "write_request",
    "read_request",
    "write_decision",
    "read_decision",
    "write_result",
    "read_result",
]
