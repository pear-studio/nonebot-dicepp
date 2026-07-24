"""Shared atomic file operations used across Manager modules."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4


def _atomic_copy(source: Path, target: Path) -> None:
    """Atomically copy a binary file to *target* via a sibling temporary file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        with source.open("rb") as input_handle, temporary.open("xb") as output:
            shutil.copyfileobj(input_handle, output, 1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    """Atomically write a JSON object to *path* via a sibling temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json_object(path: Path) -> dict[str, Any]:
    """Read a JSON file and assert its root is a dict object."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object")
    return value
