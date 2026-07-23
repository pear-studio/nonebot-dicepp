"""Private Manager API token handling."""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path


def ensure_api_token(path: str | os.PathLike[str]) -> str:
    token_path = Path(path)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    if token_path.is_file():
        token = token_path.read_text(encoding="utf-8").strip()
        if len(token) >= 32:
            return token
    token = secrets.token_urlsafe(48)
    temporary = token_path.with_suffix(token_path.suffix + ".tmp")
    temporary.write_text(token + "\n", encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(token_path)
    return token


def read_api_token(path: str | os.PathLike[str]) -> str:
    try:
        token = Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise FileNotFoundError("Manager API token is unavailable") from exc
    if not token:
        raise ValueError("Manager API token is empty")
    return token


def token_matches(expected: str, supplied: str) -> bool:
    return bool(supplied) and hmac.compare_digest(expected, supplied)
