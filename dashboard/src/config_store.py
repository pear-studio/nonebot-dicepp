"""Dashboard-local configuration reads, validation and atomic writes."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4
from typing import Any

from .config_validation import (
    ConfigurationValidationError,
    read_config_object,
    validate_bot_candidate,
    validate_user_candidate,
)


def write_config_object(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


__all__ = [
    "ConfigurationValidationError",
    "read_config_object",
    "validate_bot_candidate",
    "validate_user_candidate",
    "write_config_object",
]
