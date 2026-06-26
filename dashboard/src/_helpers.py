"""Shared helpers for the DicePP Dashboard.

No imports from any dashboard module — this is the leaf dependency
that app.py, persona.py and persona_routes.py all import from.
"""

import json
import logging
from pathlib import Path

from fastapi import HTTPException

logger = logging.getLogger("dashboard.helpers")


# ── Response helpers ──────────────────────────────────────────────────────────


def _ok(data: dict = None) -> dict:
    """Wrap success response."""
    result = {"ok": True}
    if data:
        result.update(data)
    return result


def _err(message: str, status_code: int = 400) -> HTTPException:
    """Raise an error response."""
    raise HTTPException(status_code=status_code, detail={"ok": False, "message": message})


# ── File helpers ──────────────────────────────────────────────────────────────


def _read_json_safe(path: Path) -> dict:
    """Read a JSON file, return empty dict if missing or corrupted."""
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.warning("Skipping unreadable file: %s", path)
        return {}


# ── Path traversal guard ──────────────────────────────────────────────────────


def _is_path_traversal(path: str, base: Path) -> bool:
    """Check if the resolved path escapes the given base directory."""
    if not path:
        return True
    try:
        resolved = (base / path).resolve()
        return not resolved.is_relative_to(base.resolve())
    except (ValueError, OSError):
        return True
