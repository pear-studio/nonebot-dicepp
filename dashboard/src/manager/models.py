"""Manager state models for the Dashboard-local foundation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from typing import Any, Literal
from uuid import uuid4

ManagerAction = Literal["start", "stop", "restart"]
OperationStatus = Literal["queued", "running", "succeeded", "failed", "rejected"]
RuntimeState = Literal["unknown", "stopped", "running"]

MANAGER_API_VERSION = 1
OPERATION_SCHEMA_VERSION = 1
_DICEPP_PACKAGE_NAME = "dicepp"

VALID_ACTIONS: set[str] = {"start", "stop", "restart"}


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp with a stable timezone marker."""
    return datetime.now(timezone.utc).isoformat()


def get_dicepp_version() -> str:
    """Return the installed DicePP package version for Manager health metadata."""
    try:
        return importlib_metadata.version(_DICEPP_PACKAGE_NAME)
    except Exception:
        return "unknown"


@dataclass
class BotRuntimeStatus:
    bot_id: str
    runtime_state: RuntimeState = "unknown"
    health: str = "unknown"
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bot_id": self.bot_id,
            "runtime_state": self.runtime_state,
            "health": self.health,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass
class RuntimeLogs:
    bot_id: str
    text: str
    source: str
    lines: int
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "bot_id": self.bot_id,
            "text": self.text,
            "source": self.source,
            "lines": self.lines,
            "truncated": self.truncated,
        }


@dataclass
class ManagerOperation:
    operation_id: str
    bot_id: str
    action: ManagerAction
    status: OperationStatus
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, bot_id: str, action: ManagerAction) -> "ManagerOperation":
        now = utc_now()
        return cls(
            operation_id=uuid4().hex,
            bot_id=bot_id,
            action=action,
            status="queued",
            created_at=now,
            updated_at=now,
        )

    def mark_running(self) -> None:
        now = utc_now()
        self.status = "running"
        self.started_at = now
        self.updated_at = now

    def mark_succeeded(self, message: str = "", detail: dict[str, Any] | None = None) -> None:
        now = utc_now()
        self.status = "succeeded"
        self.finished_at = now
        self.updated_at = now
        self.message = message
        self.detail = detail or {}

    def mark_failed(self, message: str, detail: dict[str, Any] | None = None) -> None:
        now = utc_now()
        self.status = "failed"
        self.finished_at = now
        self.updated_at = now
        self.message = message
        self.detail = detail or {}

    def mark_rejected(self, message: str, detail: dict[str, Any] | None = None) -> None:
        now = utc_now()
        self.status = "rejected"
        self.finished_at = now
        self.updated_at = now
        self.message = message
        self.detail = detail or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "bot_id": self.bot_id,
            "action": self.action,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "message": self.message,
            "detail": self.detail,
        }
