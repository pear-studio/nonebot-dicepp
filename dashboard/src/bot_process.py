"""Single-Bot subprocess lifecycle control.

This module deliberately owns no RuntimeUnit identity, operation journal, or
restart policy. A caller supplies one command, working directory, environment
overlay, and Bot log path; the controller owns at most one child process for
that configuration.
"""

from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal


BotProcessState = Literal["running", "stopped"]


@dataclass(frozen=True, slots=True)
class BotProcessStatus:
    """Observable state of the one controlled Bot process."""

    state: BotProcessState
    pid: int | None = None
    returncode: int | None = None

    @property
    def running(self) -> bool:
        return self.state == "running"


class BotProcessController:
    """Control one injected Bot subprocess with synchronous lifecycle methods.

    ``env`` is an overlay on the current process environment so callers only
    need to provide Bot-specific values. The command is passed directly to
    ``subprocess.Popen`` without a shell, and stdout/stderr are merged into the
    injected Bot log path.
    """

    def __init__(
        self,
        *,
        command: Sequence[str],
        cwd: str | os.PathLike[str],
        env: Mapping[str, str] | None,
        log_path: str | os.PathLike[str],
        stop_timeout: float = 2.0,
    ) -> None:
        if isinstance(command, (str, bytes)):
            raise TypeError("Bot process command must be an argument sequence")
        argv = tuple(command)
        if not argv or any(not isinstance(part, str) or not part for part in argv):
            raise ValueError("Bot process command must contain non-empty strings")
        if stop_timeout <= 0:
            raise ValueError("Bot process stop timeout must be greater than zero")

        self._command = argv
        self._cwd = Path(cwd)
        self._env = os.environ.copy()
        if env is not None:
            if any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in env.items()
            ):
                raise TypeError("Bot process environment must map strings to strings")
            self._env.update(env)
        self._log_path = Path(log_path)
        self._stop_timeout = stop_timeout
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._log_handle: BinaryIO | None = None
        self._last_pid: int | None = None
        self._last_returncode: int | None = None

    def start(self) -> BotProcessStatus:
        """Start the Bot, or return the current running process unchanged."""

        with self._lock:
            return self._start_locked()

    def stop(self) -> BotProcessStatus:
        """Terminate the Bot, killing it if the graceful wait times out."""

        with self._lock:
            return self._stop_locked()

    def restart(self) -> BotProcessStatus:
        """Stop the current Bot and start a fresh child process."""

        with self._lock:
            self._stop_locked()
            return self._start_locked()

    def status(self) -> BotProcessStatus:
        """Return current state, including a naturally exited return code."""

        with self._lock:
            return self._status_locked()

    def tail_logs(self, lines: int) -> str:
        """Return the last ``lines`` from the injected Bot log path."""

        if lines <= 0:
            raise ValueError("lines must be greater than zero")
        with self._lock:
            if not self._log_path.is_file():
                return ""
            content = self._log_path.read_text(
                encoding="utf-8",
                errors="replace",
            )
            return "\n".join(content.splitlines()[-lines:])

    def shutdown(self) -> BotProcessStatus:
        """Guarantee that the owned Bot process is stopped."""

        return self.stop()

    def _start_locked(self) -> BotProcessStatus:
        current = self._status_locked()
        if current.running:
            return current

        self._last_pid = None
        self._last_returncode = None
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._log_path.open("ab", buffering=0)
        try:
            process = subprocess.Popen(
                self._command,
                cwd=str(self._cwd),
                env=self._env.copy(),
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                creationflags=(
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    if os.name == "nt"
                    else 0
                ),
            )
        except BaseException:
            handle.close()
            raise

        self._process = process
        self._log_handle = handle
        self._last_pid = process.pid
        return BotProcessStatus("running", pid=process.pid)

    def _stop_locked(self) -> BotProcessStatus:
        current = self._status_locked()
        process = self._process
        if process is None:
            return current

        pid = process.pid
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=self._stop_timeout)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            process.wait()

        returncode = process.returncode
        self._last_pid = pid
        self._last_returncode = returncode
        self._close_process_locked()
        return BotProcessStatus("stopped", returncode=returncode)

    def _status_locked(self) -> BotProcessStatus:
        process = self._process
        if process is None:
            return BotProcessStatus(
                "stopped",
                returncode=self._last_returncode,
            )

        returncode = process.poll()
        if returncode is None:
            return BotProcessStatus("running", pid=process.pid)

        self._last_pid = process.pid
        self._last_returncode = returncode
        self._close_process_locked()
        return BotProcessStatus("stopped", returncode=returncode)

    def _close_process_locked(self) -> None:
        self._process = None
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None


__all__ = ["BotProcessController", "BotProcessState", "BotProcessStatus"]
