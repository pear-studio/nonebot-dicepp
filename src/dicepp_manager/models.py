"""Platform-neutral Manager domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

OperationAction = str
OperationStatus = Literal["queued", "running", "succeeded", "failed", "rejected", "interrupted"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class ManagerOperation:
    operation_id: str
    target: str
    action: OperationAction
    status: OperationStatus
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, target: str, action: str) -> "ManagerOperation":
        if not isinstance(target, str) or not target or len(target) > 128:
            raise ValueError("Operation target must be 1-128 characters")
        if not isinstance(action, str) or not action or len(action) > 128:
            raise ValueError("Operation action must be 1-128 characters")
        now = utc_now()
        return cls(uuid4().hex, target, action, "queued", now, now)

    @classmethod
    def create_system(cls, action: str) -> "ManagerOperation":
        if not action or len(action) > 128:
            raise ValueError("System operation action must be 1-128 characters")
        now = utc_now()
        return cls(uuid4().hex, "instance", action, "queued", now, now)

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
            "target": self.target,
            "action": self.action,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "message": self.message,
            "detail": self.detail,
        }
