"""Dedicated Bot↔Manager control credential.

The token is stored at ``manager/control/control-token``, outside the
Dashboard mount boundary.  It is intentionally unrelated to the Manager HTTP
API bearer token and never falls back to the legacy ``data/dicepp.db`` value.
"""
import os
import secrets
import time
from pathlib import Path

from dicepp_data import InstanceLayout


def _layout_for(project_root: Path) -> InstanceLayout:
    return InstanceLayout.from_root(Path(project_root).expanduser().resolve())


def token_path(project_root: Path) -> Path:
    """Return the Manager-owned token location for one instance."""
    return _layout_for(project_root).manager_control_token


def ensure_token(project_root: Path) -> str:
    """Read an existing token or atomically bootstrap the dedicated file."""
    path = token_path(project_root)
    existing = _read_token_file(path)
    if existing is not None:
        return existing

    path.parent.mkdir(parents=True, exist_ok=True)
    generated = secrets.token_urlsafe(32)
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        # The other peer may have created the file but not finished its
        # fsynced write yet.  Both startup orders therefore converge on the
        # same token instead of treating this narrow race as a fatal boot.
        for _ in range(20):
            existing = _read_token_file(path)
            if existing is not None:
                return existing
            time.sleep(0.01)
        raise RuntimeError("Manager control token file is invalid")

    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(generated)
            target.flush()
            os.fsync(target.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return generated


def read_token(project_root: Path) -> str | None:
    """Read only the dedicated token, never the legacy instance database."""
    return _read_token_file(token_path(project_root))


def _read_token_file(path: Path) -> str | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None
