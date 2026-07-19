from __future__ import annotations

from enum import Enum
from types import MappingProxyType
from typing import Mapping


class LogErrorCode(str, Enum):
    """Stable domain error codes for command-layer localization."""

    CURRENT_LOG_REQUIRED = "current_log_required"
    LOG_NOT_FOUND = "log_not_found"
    ACTIVE_LOG_NAME_UNKNOWN = "active_log_name_unknown"
    LOG_IS_RECORDING = "log_is_recording"
    INVALID_NAME = "invalid_name"


class LogServiceError(Exception):
    """Base class for errors raised by the log domain service."""


class LogDomainError(LogServiceError):
    """An expected business rejection, safe for command-layer translation."""

    def __init__(self, code: LogErrorCode, **context: str) -> None:
        self.code = code
        self.context: Mapping[str, str] = MappingProxyType(dict(context))
        super().__init__(code.value)


class LogInvariantError(LogServiceError):
    """Persistent state violates a lifecycle invariant."""
