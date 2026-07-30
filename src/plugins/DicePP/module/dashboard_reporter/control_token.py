"""Dedicated Bot↔Manager control credential.

The token is stored at ``manager/control/control-token``, outside the
Dashboard mount boundary.  It is intentionally unrelated to the Manager HTTP
API bearer token and never falls back to the legacy ``data/dicepp.db`` value.
"""
from pathlib import Path

from dicepp_data import InstanceLayout
from dicepp_manager.auth import (
    TokenSecurityError,
    ensure_private_token,
    read_private_token,
)


def _layout_for(project_root: Path) -> InstanceLayout:
    return InstanceLayout.from_root(Path(project_root).expanduser().resolve())


def token_path(project_root: Path) -> Path:
    """Return the Manager-owned token location for one instance."""
    return _layout_for(project_root).manager_control_token


def ensure_token(project_root: Path) -> str:
    """Read or atomically bootstrap the dedicated, private control credential."""
    return ensure_private_token(
        token_path(project_root),
        token_bytes=32,
        min_length=1,
        exclusive_create=True,
    )


def read_token(project_root: Path) -> str | None:
    """Read only the dedicated token, never the legacy instance database."""
    return _read_token_file(token_path(project_root))


def _read_token_file(path: Path) -> str | None:
    try:
        token = read_private_token(path)
    except (FileNotFoundError, OSError, TokenSecurityError, ValueError):
        return None
    return token or None
