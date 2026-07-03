"""Opt-in local process runtime backend for Dashboard Manager."""

from __future__ import annotations

import asyncio
import ctypes
import os
import shlex
import subprocess
import threading
from pathlib import Path
from typing import BinaryIO

from ..runtime_log import append_runtime_log_line, runtime_log_path
from .models import BotRuntimeStatus, ManagerAction, RuntimeLogs
from .runtime import RuntimeOperationUnsupported


class ProcessRuntimeBackend:
    """Manage one local subprocess per bot id using an explicit command template."""

    def __init__(
        self,
        *,
        command: str,
        cwd: str | os.PathLike[str] | None = None,
        stop_timeout: float = 2.0,
        log_path: str | os.PathLike[str] | None = None,
    ) -> None:
        command = command.strip()
        if not command:
            raise ValueError("Process runtime command must not be empty")
        if stop_timeout <= 0:
            raise ValueError("Process runtime stop timeout must be greater than 0")

        self._argv_template = _split_command(command)
        if not self._argv_template:
            raise ValueError("Process runtime command must not be empty")
        self._cwd = Path(cwd) if cwd else Path.cwd()
        self._stop_timeout = stop_timeout
        self._log_path = Path(log_path) if log_path is not None else runtime_log_path()
        self._processes: dict[str, subprocess.Popen] = {}
        self._log_handles: dict[str, BinaryIO] = {}
        self._lock = threading.Lock()

    async def status(self, bot_ids: list[str]) -> dict[str, BotRuntimeStatus]:
        with self._lock:
            return {bot_id: self._status_unlocked(bot_id) for bot_id in bot_ids}

    async def operate(
        self,
        bot_id: str,
        action: ManagerAction,
        request_detail: dict | None = None,
    ) -> BotRuntimeStatus:
        if action == "start":
            with self._lock:
                return self._start_unlocked(bot_id)
        if action == "stop":
            process = None
            with self._lock:
                process, early_result = self._begin_stop_unlocked(bot_id)
            if process is None:
                return early_result  # type: ignore[return-value]
            message = await self._wait_for_stop(process, bot_id)
            with self._lock:
                return self._finish_stop_unlocked(bot_id, process, message)
        if action == "restart":
            process = None
            with self._lock:
                process, early_result = self._begin_stop_unlocked(bot_id)
            if process is not None:
                message = await self._wait_for_stop(process, bot_id)
                with self._lock:
                    self._finish_stop_unlocked(bot_id, process, message)
            with self._lock:
                return self._start_unlocked(bot_id)
        raise ValueError(f"Unsupported manager action: {action}")

    async def logs(self, bot_id: str, lines: int) -> RuntimeLogs:
        return self._read_runtime_log(bot_id, lines)

    async def runtime_logs(self, lines: int) -> RuntimeLogs:
        return self._read_runtime_log("runtime", lines)

    def _read_runtime_log(self, bot_id: str, lines: int) -> RuntimeLogs:
        if lines <= 0:
            raise ValueError("lines must be greater than 0")
        if not self._log_path.exists():
            return RuntimeLogs(
                bot_id=bot_id,
                text="",
                source=str(self._log_path),
                lines=lines,
                truncated=False,
            )
        all_lines = self._log_path.read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines()
        selected = all_lines[-lines:]
        return RuntimeLogs(
            bot_id=bot_id,
            text="\n".join(selected),
            source=str(self._log_path),
            lines=lines,
            truncated=len(all_lines) > lines,
        )

    def _argv_for(self, bot_id: str) -> list[str]:
        return [part.replace("{bot_id}", bot_id) for part in self._argv_template]

    def _status_unlocked(self, bot_id: str) -> BotRuntimeStatus:
        process = self._processes.get(bot_id)
        if process is None:
            return BotRuntimeStatus(
                bot_id=bot_id,
                runtime_state="stopped",
                health="stopped",
                message="Process not started by Manager",
            )

        returncode = process.poll()
        if returncode is None:
            return BotRuntimeStatus(
                bot_id=bot_id,
                runtime_state="running",
                health="healthy",
                message="Process is running",
                detail={"pid": process.pid},
            )

        self._processes.pop(bot_id, None)
        self._close_log_handle(bot_id)
        return BotRuntimeStatus(
            bot_id=bot_id,
            runtime_state="stopped",
            health="stopped" if returncode == 0 else "unhealthy",
            message="Process exited",
            detail={"returncode": returncode},
        )

    def _start_unlocked(self, bot_id: str) -> BotRuntimeStatus:
        current = self._status_unlocked(bot_id)
        if current.runtime_state == "running":
            return current

        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        append_runtime_log_line(
            f"runtime | starting {bot_id}: {' '.join(self._argv_for(bot_id))}",
            path=self._log_path,
        )
        log_handle = self._log_path.open("ab", buffering=0)
        try:
            env = os.environ.copy()
            env.setdefault("PYTHONIOENCODING", "utf-8")
            process = subprocess.Popen(
                self._argv_for(bot_id),
                cwd=str(self._cwd),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=_creationflags(),
            )
        except Exception:
            log_handle.close()
            raise
        self._log_handles[bot_id] = log_handle
        self._processes[bot_id] = process
        return BotRuntimeStatus(
            bot_id=bot_id,
            runtime_state="running",
            health="healthy",
            message="Process started",
            detail={"pid": process.pid},
        )

    def _begin_stop_unlocked(
        self,
        bot_id: str,
    ) -> tuple[subprocess.Popen | None, BotRuntimeStatus | None]:
        """Phase 1 (under lock): signal termination, return process to wait on.

        Returns ``(None, early_result)`` when the process is not running and
        no async wait is needed.  Returns ``(process, None)`` when the caller
        must ``await _wait_for_stop(process, bot_id)`` and then call
        ``_finish_stop_unlocked`` under the lock.
        """
        process = self._processes.get(bot_id)
        if process is None:
            return None, BotRuntimeStatus(
                bot_id=bot_id,
                runtime_state="stopped",
                health="stopped",
                message="Process not running",
            )

        returncode = process.poll()
        if returncode is not None:
            self._processes.pop(bot_id, None)
            self._close_log_handle(bot_id)
            return None, BotRuntimeStatus(
                bot_id=bot_id,
                runtime_state="stopped",
                health="stopped" if returncode == 0 else "unhealthy",
                message="Process already exited",
                detail={"returncode": returncode},
            )

        append_runtime_log_line(f"runtime | stopping {bot_id}", path=self._log_path)
        process.terminate()
        return process, None

    async def _wait_for_stop(
        self,
        process: subprocess.Popen,
        bot_id: str,
    ) -> str:
        """Phase 2 (outside lock): wait for process to exit."""
        try:
            await asyncio.to_thread(process.wait, timeout=self._stop_timeout)
            message = "Process stopped"
            append_runtime_log_line(
                f"runtime | stopped {bot_id} returncode={process.returncode}",
                path=self._log_path,
            )
        except subprocess.TimeoutExpired:
            append_runtime_log_line(
                f"runtime | stop timeout for {bot_id}; killing process",
                path=self._log_path,
            )
            process.kill()
            await asyncio.to_thread(process.wait)
            message = "Process killed after stop timeout"
            append_runtime_log_line(
                f"runtime | killed {bot_id} returncode={process.returncode}",
                path=self._log_path,
            )
        return message

    def _finish_stop_unlocked(
        self,
        bot_id: str,
        process: subprocess.Popen,
        message: str,
    ) -> BotRuntimeStatus:
        """Phase 3 (under lock): cleanup after process has exited."""
        self._processes.pop(bot_id, None)
        self._close_log_handle(bot_id)
        return BotRuntimeStatus(
            bot_id=bot_id,
            runtime_state="stopped",
            health="stopped",
            message=message,
            detail={"returncode": process.returncode},
        )

    def _close_log_handle(self, bot_id: str) -> None:
        handle = self._log_handles.pop(bot_id, None)
        if handle is not None:
            handle.close()


def _split_command(command: str) -> list[str]:
    if os.name != "nt":
        return shlex.split(command)

    argc = ctypes.c_int()
    ctypes.windll.shell32.CommandLineToArgvW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_int),
    ]
    ctypes.windll.shell32.CommandLineToArgvW.restype = ctypes.POINTER(
        ctypes.c_wchar_p
    )
    ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    ctypes.windll.kernel32.LocalFree.restype = ctypes.c_void_p

    argv = ctypes.windll.shell32.CommandLineToArgvW(command, ctypes.byref(argc))
    if not argv:
        raise ValueError("Process runtime command could not be parsed")
    try:
        return [argv[index] for index in range(argc.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(argv)


def _creationflags(os_name: str | None = None) -> int:
    os_name = os.name if os_name is None else os_name
    if os_name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)
