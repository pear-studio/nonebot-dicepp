from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from core.data.models import LogSession


class LogOnAction(str, Enum):
    CREATED = "created"
    RESUMED = "resumed"
    ALREADY_RECORDING = "already_recording"
    SWITCHED = "switched"


class LogOffAction(str, Enum):
    STOPPED = "stopped"
    ALREADY_OFF = "already_off"


class LogExportReason(str, Enum):
    OFF = "off"
    SWITCH = "switch"
    MANUAL = "manual"


class LogExportView(str, Enum):
    CURATED = "curated"
    COMPLETE = "complete"


class LogExportFormat(str, Enum):
    TXT = "txt"
    DOCX = "docx"
    HTML = "html"


class LogGenerationStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


class LogDeliveryStatus(str, Enum):
    NOT_ATTEMPTED = "not_attempted"
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ExportRequest:
    request_id: str
    reason: LogExportReason
    log_id: str
    group_id: str
    log_name: str
    view: LogExportView
    formats: tuple[LogExportFormat, ...]
    record_upper_id: int
    requested_at: datetime
    requested_by: str


@dataclass(frozen=True, slots=True)
class LogOnResult:
    session: LogSession
    action: LogOnAction
    previous_session: LogSession | None = None
    export_request: ExportRequest | None = None


@dataclass(frozen=True, slots=True)
class LogOffResult:
    session: LogSession
    action: LogOffAction
    export_request: ExportRequest | None = None


@dataclass(frozen=True, slots=True)
class LogListItem:
    log_id: str
    group_id: str
    name: str
    is_current: bool
    recording: bool
    created_at: datetime
    last_message_at: datetime | None
    record_count: int
    last_export_at: datetime | None


@dataclass(frozen=True, slots=True)
class LogDeleteResult:
    session: LogSession
    current_cleared: bool
    had_export_history: bool
    had_publication_history: bool
