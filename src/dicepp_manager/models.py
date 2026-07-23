"""Platform-neutral Manager domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Any, Literal
from uuid import uuid4

ManagerAction = Literal["start", "stop", "restart"]
OperationStatus = Literal["queued", "running", "succeeded", "failed", "rejected", "interrupted"]
RuntimeState = Literal["unknown", "stopped", "running"]
VALID_ACTIONS: set[str] = {"start", "stop", "restart"}
_SAFE_RUNTIME_UNIT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def validate_runtime_unit_id(value: str) -> str:
    if not isinstance(value, str) or not _SAFE_RUNTIME_UNIT_ID.fullmatch(value):
        raise ValueError(
            "runtime_unit_id must be 1-128 ASCII letters, digits, '.', '_' or '-', "
            "and must start with a letter or digit"
        )
    return value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class RuntimeUnit:
    runtime_unit_id: str
    bot_ids: tuple[str, ...]
    shared_process: bool = True
    adapter: str = "unavailable"

    def __post_init__(self) -> None:
        validate_runtime_unit_id(self.runtime_unit_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_unit_id": self.runtime_unit_id,
            "bot_ids": list(self.bot_ids),
            "shared_process": self.shared_process,
            "adapter": self.adapter,
        }


@dataclass(slots=True)
class RuntimeUnitStatus:
    runtime_unit_id: str
    runtime_state: RuntimeState = "unknown"
    health: str = "unknown"
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_runtime_unit_id(self.runtime_unit_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_unit_id": self.runtime_unit_id,
            "runtime_state": self.runtime_state,
            "health": self.health,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass(slots=True)
class RuntimeLogs:
    runtime_unit_id: str
    text: str
    source: str
    lines: int
    truncated: bool = False

    def __post_init__(self) -> None:
        if self.runtime_unit_id != "runtime":
            validate_runtime_unit_id(self.runtime_unit_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_unit_id": self.runtime_unit_id,
            "text": self.text,
            "source": self.source,
            "lines": self.lines,
            "truncated": self.truncated,
        }


@dataclass(slots=True)
class ManagerOperation:
    operation_id: str
    runtime_unit_id: str
    action: ManagerAction
    status: OperationStatus
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, runtime_unit_id: str, action: ManagerAction) -> "ManagerOperation":
        validate_runtime_unit_id(runtime_unit_id)
        if action not in VALID_ACTIONS:
            raise ValueError(f"Unsupported Manager action: {action}")
        now = utc_now()
        return cls(uuid4().hex, runtime_unit_id, action, "queued", now, now)

    def transition(
        self,
        status: OperationStatus,
        *,
        message: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now()
        self.status = status
        self.updated_at = now
        if status == "running" and self.started_at is None:
            self.started_at = now
        if status in {"succeeded", "failed", "rejected", "interrupted"}:
            self.finished_at = now
        self.message = message
        self.detail = detail or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "runtime_unit_id": self.runtime_unit_id,
            "action": self.action,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "message": self.message,
            "detail": self.detail,
        }
