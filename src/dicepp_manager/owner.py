"""Cross-process ownership lock for one Manager instance."""

from __future__ import annotations

from pathlib import Path

from filelock import FileLock, Timeout


class ManagerAlreadyRunning(RuntimeError):
    pass


class ManagerOwnerLock:
    def __init__(self, state_dir: str | Path) -> None:
        self.path = Path(state_dir) / "owner.lock"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(self.path))
        self._held = False

    def acquire(self) -> None:
        try:
            self._lock.acquire(timeout=0)
        except Timeout as exc:
            raise ManagerAlreadyRunning("Another Manager already owns this instance") from exc
        self._held = True

    def release(self) -> None:
        if self._held:
            self._lock.release()
            self._held = False
