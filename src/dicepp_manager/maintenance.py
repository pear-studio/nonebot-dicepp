"""Instance-wide exclusive maintenance lease."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from filelock import FileLock, Timeout


class MaintenanceConflict(RuntimeError):
    pass


class MaintenanceLock:
    def __init__(self, state_dir: str | Path) -> None:
        self.path = Path(state_dir) / "maintenance.lock"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(self.path))

    @contextmanager
    def acquire(self, *, timeout: float = 0) -> Iterator[None]:
        try:
            self._lock.acquire(timeout=timeout)
        except Timeout as exc:
            raise MaintenanceConflict("Another instance maintenance operation is active") from exc
        try:
            yield
        finally:
            self._lock.release()
