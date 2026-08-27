"""Persistent, isolated workspaces for DicePP Shell sessions."""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil
from filelock import FileLock, Timeout as FileLockTimeout

from plugins.DicePP.frozen import get_project_root
from plugins.DicePP.utils.network import format_url_host


SHELL_DIR = Path(get_project_root()) / ".dicepp-shell"
_LOCKS_DIR = SHELL_DIR / ".locks"


def _session_lock_path(name: str) -> Path:
    """Return the external lifecycle-lock path for *name*.

    The lock lives **outside** the session directory so that
    ``shutil.rmtree(session_dir)`` cannot delete a lock that is still held.
    """
    _LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    return _LOCKS_DIR / f"{name}.lock"

_WORKSPACE_DIRS = (
    "config/bots",
    "data/bots",
    "data/local_images",
    "content/characters",
    "content/queries",
    "content/decks",
    "content/random",
    "dashboard/data",
)


@dataclass(frozen=True)
class RuntimeInfo:
    pid: int
    process_created_at: float
    host: str
    port: int
    bot_id: str
    started_at: float

    @property
    def base_url(self) -> str:
        return f"http://{format_url_host(self.host)}:{self.port}"


class RuntimeAlreadyActive(RuntimeError):
    """Raised when an operation conflicts with a live session runtime."""


class SessionRuntimeLease:
    """Exclusive process lease for one long-running shell session.

    Uses an OS-level advisory lock (``filelock.FileLock``) placed **outside**
    the session directory (``.dicepp-shell/.locks/<name>.lock``).  The OS
    releases the lock when the owning process exits — even on hard crash — so
    there is no need for manual stale-file detection or cleanup.

    ``runtime.json`` is still the two-phase publish step: acquire the lock
    first, then publish the runtime address once the server is ready.
    ``read_runtime_info`` uses the pid+create_time recorded inside
    runtime.json to judge liveness; it only removes a stale runtime.json and
    **never** touches the external lock file.
    """

    def __init__(self, session_dir: Path) -> None:
        self.session_dir = session_dir
        self.runtime_path = session_dir / "runtime.json"
        self._lock_path = _session_lock_path(session_dir.name)
        self.pid = os.getpid()
        self.process_created_at = psutil.Process(self.pid).create_time()
        self._acquired = False
        self._lock: FileLock | None = None

    def acquire(self) -> "SessionRuntimeLease":
        lock = FileLock(str(self._lock_path))
        try:
            lock.acquire(timeout=0)
        except FileLockTimeout:
            # The OS-level advisory lock is held.  Read runtime.json so the
            # error message can include the holder's address, or indicate
            # that the holder is still starting (lock held, no publish yet).
            active = read_runtime_info(self.session_dir)
            if active is not None:
                raise RuntimeAlreadyActive(
                    f"Session runtime is already active at {active.base_url} "
                    f"(pid={active.pid})"
                )
            raise RuntimeAlreadyActive(
                "Session runtime is starting — lock is held but the holder "
                "has not published its address yet"
            )
        self._lock = lock
        self._acquired = True
        return self

    def publish(self, *, host: str, port: int, bot_id: str) -> RuntimeInfo:
        if not self._acquired:
            raise RuntimeError("Runtime lease has not been acquired")
        info = RuntimeInfo(
            pid=self.pid,
            process_created_at=self.process_created_at,
            host=host,
            port=port,
            bot_id=bot_id,
            started_at=time.time(),
        )
        _write_json_atomic(self.runtime_path, asdict(info))
        return info

    def release(self) -> None:
        """Release the OS-level lock and remove runtime artifacts.

        Only acts on the lock that *this* instance acquired — never a lock
        another process took over after reclaiming a stale workspace.
        """
        if not self._acquired:
            return
        # runtime.json belongs to this publish cycle only if the lock is ours,
        # and we hold the lock here, so it is safe to clean it up.
        self.runtime_path.unlink(missing_ok=True)
        if self._lock is not None:
            self._lock.release()
        self._acquired = False

    def __enter__(self) -> "SessionRuntimeLease":
        return self.acquire()

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.release()


def _validate_session_name(name: str) -> None:
    if not name:
        raise ValueError("Session name cannot be empty")
    if len(name) > 32:
        raise ValueError("Session name too long (max 32)")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    if not all(c in allowed for c in name):
        raise ValueError(
            "Session name contains invalid characters "
            "(allowed: a-z, A-Z, 0-9, _, -)"
        )


def get_session_dir(name: str) -> Path:
    _validate_session_name(name)
    return SHELL_DIR / name


def session_exists(name: str) -> bool:
    return get_session_dir(name).exists()


def bot_id_for_session(name: str) -> str:
    _validate_session_name(name)
    return f"shell_{name}"


def create_session(name: str, group_id: str = "test_group") -> Path:
    """Create (or re-enter) a session workspace.

    Briefly acquires the lifecycle lock so an ``init`` cannot race with a
    concurrent ``rm`` on the same session.
    """
    session_dir = get_session_dir(name)
    lock = FileLock(str(_session_lock_path(name)))
    try:
        lock.acquire(timeout=0)
    except FileLockTimeout:
        raise RuntimeAlreadyActive(
            f"Session '{name}' is currently in use; cannot create/init"
        )
    try:
        session_dir.mkdir(parents=True, exist_ok=True)

        meta_path = session_dir / "meta.json"
        if not meta_path.exists():
            now = time.time()
            meta_path.write_text(
                json.dumps(
                    {
                        "name": name,
                        "group_id": group_id,
                        "created": now,
                        "last_used": now,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

        _ensure_workspace(session_dir)
        return session_dir
    finally:
        lock.release()


def load_session(name: str) -> Optional[Dict[str, Any]]:
    session_dir = get_session_dir(name)
    meta_path = session_dir / "meta.json"
    if not meta_path.exists():
        return None

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["last_used"] = time.time()
        meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return meta
    except (json.JSONDecodeError, OSError):
        return None


def list_sessions() -> List[Dict[str, Any]]:
    if not SHELL_DIR.exists():
        return []

    sessions = []
    for item in SHELL_DIR.iterdir():
        if not item.is_dir():
            continue
        meta_path = item / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            total_size = sum(
                path.stat().st_size for path in item.rglob("*") if path.is_file()
            )
            runtime = read_runtime_info(item)
            sessions.append(
                {
                    "name": meta.get("name", item.name),
                    "group_id": meta.get("group_id", "unknown"),
                    "created": meta.get("created", 0),
                    "last_used": meta.get("last_used", 0),
                    "size_bytes": total_size,
                    "runtime": asdict(runtime) if runtime else None,
                }
            )
        except (json.JSONDecodeError, OSError):
            continue

    sessions.sort(key=lambda item: item["last_used"], reverse=True)
    return sessions


def delete_session(name: str) -> bool:
    """Remove a stopped session workspace.

    Acquires the lifecycle lock before ``rmtree`` so a concurrent ``serve``
    cannot start while the directory is being deleted.  The lock file lives
    outside the session directory (``.dicepp-shell/.locks/``), so rmtree
    never deletes a lock that is still held.
    """
    session_dir = get_session_dir(name)
    if not session_dir.exists():
        return False
    lock = FileLock(str(_session_lock_path(name)))
    try:
        lock.acquire(timeout=0)
    except FileLockTimeout:
        raise RuntimeAlreadyActive(
            f"Session '{name}' is currently in use; "
            "stop it before deleting"
        )
    try:
        shutil.rmtree(session_dir)
        return True
    except OSError:
        return False
    finally:
        lock.release()


def format_session_info(session: Dict[str, Any]) -> str:
    name = session["name"]
    group_id = session["group_id"]
    size = session["size_bytes"]
    if size < 1024:
        size_str = f"{size}B"
    elif size < 1024 * 1024:
        size_str = f"{size / 1024:.1f}KB"
    else:
        size_str = f"{size / (1024 * 1024):.1f}MB"

    ago = time.time() - session["last_used"]
    if ago < 60:
        time_str = "just now"
    elif ago < 3600:
        time_str = f"{int(ago / 60)}m ago"
    elif ago < 86400:
        time_str = f"{int(ago / 3600)}h ago"
    else:
        time_str = f"{int(ago / 86400)}d ago"

    state = "running" if session.get("runtime") else "stopped"
    return (
        f"{name:16} {group_id:16} {size_str:>8} "
        f"{time_str:>10} {state:>8}"
    )


def read_runtime_info(session_dir: Path) -> RuntimeInfo | None:
    raw = _read_json(session_dir / "runtime.json")
    if not raw:
        return None
    try:
        info = RuntimeInfo(
            pid=int(raw["pid"]),
            process_created_at=float(raw["process_created_at"]),
            host=str(raw["host"]),
            port=int(raw["port"]),
            bot_id=str(raw["bot_id"]),
            started_at=float(raw["started_at"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if _same_process(info.pid, info.process_created_at):
        return info
    # Stale runtime.json from a dead process.  Only remove runtime.json;
    # the external lifecycle lock (.dicepp-shell/.locks/) is managed
    # exclusively by FileLock — the OS released it on process exit.
    (session_dir / "runtime.json").unlink(missing_ok=True)
    return None


def _ensure_workspace(session_dir: Path) -> None:
    for relative in _WORKSPACE_DIRS:
        (session_dir / relative).mkdir(parents=True, exist_ok=True)


def _same_process(pid: int, created_at: float) -> bool:
    try:
        process = psutil.Process(pid)
        # Tolerance widened to 1.0s to absorb cross-platform create_time precision
        # differences (Docker overlayfs / cgroups / /proc/<pid>/stat can report at
        # 0.01~0.1s resolution). PID reuse creates new processes whose create_time
        # differs by far more than 1s, so no realistic misidentification risk.
        return process.is_running() and abs(process.create_time() - created_at) < 1.0
    except (psutil.Error, OSError):
        return False


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, path)
