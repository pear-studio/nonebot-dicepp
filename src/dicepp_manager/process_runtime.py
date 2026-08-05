"""Single-process RuntimeUnit adapter used by the Windows Manager."""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import shlex
import subprocess
import threading
from pathlib import Path
from typing import Any, BinaryIO, Callable

from .models import ManagerAction, RuntimeLogs, RuntimeUnitStatus
from .runtime_log import append_runtime_log_line, runtime_log_path


class ProcessRuntimeAdapter:
    def __init__(
        self,
        *,
        runtime_unit_id: str,
        command: str,
        cwd: str | os.PathLike[str] | None = None,
        stop_timeout: float = 2.0,
        log_path: str | os.PathLike[str] | None = None,
        identity_path: str | os.PathLike[str] | None = None,
        identity_handle_opener: Callable[[dict], Any | None] | None = None,
    ) -> None:
        if not command.strip():
            raise ValueError("Process runtime command must not be empty")
        if stop_timeout <= 0:
            raise ValueError("Process stop timeout must be greater than zero")
        self.runtime_unit_id = runtime_unit_id
        self._argv = _split_command(command)
        self._cwd = Path(cwd or Path.cwd())
        self._stop_timeout = stop_timeout
        self._log_path = Path(log_path) if log_path else runtime_log_path()
        self._identity_path = Path(identity_path) if identity_path else None
        self._identity_handle_opener = identity_handle_opener
        self._process: subprocess.Popen | None = None
        self._log_handle: BinaryIO | None = None
        self._lock = threading.RLock()

    def _check_unit(self, runtime_unit_id: str) -> None:
        if runtime_unit_id != self.runtime_unit_id:
            raise ValueError(f"RuntimeUnit is not owned by this adapter: {runtime_unit_id}")

    async def status(self, runtime_unit_ids: list[str]) -> dict[str, RuntimeUnitStatus]:
        return {unit_id: self._status_one(unit_id) for unit_id in runtime_unit_ids}

    def _status_one(self, runtime_unit_id: str) -> RuntimeUnitStatus:
        self._check_unit(runtime_unit_id)
        with self._lock:
            process = self._process
            if process is None:
                identity = self._persisted_identity()
                if identity is not None and self._identity_is_live(identity):
                    return RuntimeUnitStatus(
                        runtime_unit_id,
                        "running",
                        "healthy",
                        "Known process is running",
                        {
                            "pid": identity["pid"],
                            "started_at": identity["started_at"],
                            "executable": identity["executable"],
                            "adopted": True,
                        },
                    )
                self._remove_identity()
                return RuntimeUnitStatus(runtime_unit_id, "stopped", "stopped", "Process is not running")
            returncode = process.poll()
            if returncode is None:
                return RuntimeUnitStatus(
                    runtime_unit_id,
                    "running",
                    "healthy",
                    "Process is running",
                    {"pid": process.pid},
                )
            self._cleanup()
            return RuntimeUnitStatus(
                runtime_unit_id,
                "stopped",
                "stopped" if returncode == 0 else "unhealthy",
                "Process exited",
                {"returncode": returncode},
            )

    async def operate(self, runtime_unit_id: str, action: ManagerAction) -> RuntimeUnitStatus:
        self._check_unit(runtime_unit_id)
        if action == "start":
            return self._start(runtime_unit_id)
        if action in {"stop", "restart"}:
            stopped = await self._stop(runtime_unit_id)
            return self._start(runtime_unit_id) if action == "restart" else stopped
        raise ValueError(f"Unsupported Manager action: {action}")

    def _start(self, runtime_unit_id: str) -> RuntimeUnitStatus:
        with self._lock:
            current = self._status_one(runtime_unit_id)
            if current.runtime_state == "running":
                return current
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            append_runtime_log_line(f"runtime | starting {runtime_unit_id}", path=self._log_path)
            handle = self._log_path.open("ab", buffering=0)
            try:
                env = os.environ.copy()
                env["PYTHONIOENCODING"] = "utf-8"
                process = subprocess.Popen(
                    self._argv,
                    cwd=str(self._cwd),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0),
                )
            except Exception:
                handle.close()
                raise
            self._process = process
            self._log_handle = handle
            if self._identity_path is not None:
                identity = self._inspect_identity(process.pid)
                if identity is None:
                    process.terminate()
                    process.wait(timeout=self._stop_timeout)
                    self._cleanup()
                    raise RuntimeError("Started process identity could not be persisted")
                self._write_identity(identity)
            return RuntimeUnitStatus(
                runtime_unit_id,
                "running",
                "healthy",
                "Process started",
                {"pid": process.pid},
            )

    async def _stop(self, runtime_unit_id: str) -> RuntimeUnitStatus:
        adopted_handle = None
        with self._lock:
            process = self._process
            identity = self._persisted_identity()
            if process is None and identity is not None and self._identity_is_live(identity):
                adopted_handle = self._open_identity_handle(identity)
                if adopted_handle is None:
                    self._cleanup()
                    return RuntimeUnitStatus(
                        runtime_unit_id,
                        "stopped",
                        "stopped",
                        "Known process identity changed before stop",
                    )
            elif process is None or process.poll() is not None:
                self._cleanup()
                return RuntimeUnitStatus(runtime_unit_id, "stopped", "stopped", "Process is not running")
            if process is not None:
                process.terminate()
        if process is None:
            stopped = False
            try:
                stopped = await asyncio.to_thread(
                    adopted_handle.terminate, self._stop_timeout
                )
                if not stopped:
                    raise RuntimeError(
                        "Known process did not exit after termination"
                    )
            finally:
                adopted_handle.close()
                if stopped:
                    with self._lock:
                        self._cleanup()
            append_runtime_log_line(f"runtime | stopped {runtime_unit_id}", path=self._log_path)
            return RuntimeUnitStatus(
                runtime_unit_id,
                "stopped",
                "stopped",
                "Known process stopped",
                {"pid": identity["pid"], "adopted": True},
            )
        try:
            await asyncio.to_thread(process.wait, timeout=self._stop_timeout)
            message = "Process stopped"
        except subprocess.TimeoutExpired:
            process.kill()
            await asyncio.to_thread(process.wait)
            message = "Process killed after stop timeout"
        with self._lock:
            returncode = process.returncode
            self._cleanup()
        append_runtime_log_line(f"runtime | stopped {runtime_unit_id}", path=self._log_path)
        return RuntimeUnitStatus(runtime_unit_id, "stopped", "stopped", message, {"returncode": returncode})

    async def logs(self, runtime_unit_id: str, lines: int) -> RuntimeLogs:
        self._check_unit(runtime_unit_id)
        return self._read_logs(runtime_unit_id, lines)

    async def runtime_logs(self, lines: int) -> RuntimeLogs:
        return self._read_logs("runtime", lines)

    def _read_logs(self, runtime_unit_id: str, lines: int) -> RuntimeLogs:
        if lines <= 0:
            raise ValueError("lines must be greater than zero")
        if not self._log_path.is_file():
            return RuntimeLogs(runtime_unit_id, "", str(self._log_path), lines)
        all_lines = self._log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return RuntimeLogs(
            runtime_unit_id,
            "\n".join(all_lines[-lines:]),
            str(self._log_path),
            lines,
            len(all_lines) > lines,
        )

    def _cleanup(self) -> None:
        self._process = None
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
        self._remove_identity()

    def _persisted_identity(self) -> dict | None:
        if self._identity_path is None or not self._identity_path.is_file():
            return None
        try:
            value = json.loads(self._identity_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if (
            not isinstance(value, dict)
            or type(value.get("pid")) is not int
            or value["pid"] <= 0
            or not isinstance(value.get("started_at"), str)
            or not isinstance(value.get("executable"), str)
            or not Path(value["executable"]).is_absolute()
        ):
            return None
        return {
            "pid": value["pid"],
            "started_at": value["started_at"],
            "executable": value["executable"],
        }

    def _inspect_identity(self, pid: int) -> dict | None:
        from .process_identity import inspect_process_identity

        return inspect_process_identity(pid)

    def _identity_is_live(self, identity: dict) -> bool:
        return self._inspect_identity(identity["pid"]) == identity

    def _open_identity_handle(self, identity: dict):
        if self._identity_handle_opener is not None:
            return self._identity_handle_opener(identity)
        from .process_identity import open_process_identity_handle

        return open_process_identity_handle(identity)

    def _write_identity(self, identity: dict) -> None:
        if self._identity_path is None:
            return
        from .upgrade import _atomic_json

        _atomic_json(self._identity_path, identity)

    def _remove_identity(self) -> None:
        if self._identity_path is not None:
            self._identity_path.unlink(missing_ok=True)


def _split_command(command: str) -> list[str]:
    if os.name != "nt":
        return shlex.split(command)
    argc = ctypes.c_int()
    ctypes.windll.shell32.CommandLineToArgvW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_int),
    ]
    ctypes.windll.shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    argv = ctypes.windll.shell32.CommandLineToArgvW(command, ctypes.byref(argc))
    if not argv:
        raise ValueError("Process runtime command could not be parsed")
    try:
        return [argv[index] for index in range(argc.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(argv)
