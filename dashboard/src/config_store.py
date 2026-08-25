"""Dashboard-local configuration reads, validation and writes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config_validation import (
    ConfigurationValidationError,
    read_config_object,
    validate_bot_candidate,
    validate_user_candidate,
)


def write_config_object(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "ConfigurationValidationError",
    "read_config_object",
    "validate_bot_candidate",
    "validate_user_candidate",
    "write_config_object",
]
