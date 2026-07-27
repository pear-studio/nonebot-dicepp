"""Stable-root Windows UpdateGuard process.

The guard never discovers processes by name.  It accepts exactly one persisted
PID/start-time/executable identity, waits for that process to exit, executes
the configured Velopack boundary, and records a durable health or rollback
marker for the restarted Manager.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import faulthandler
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from dicepp_meta import get_version

from ._file_utils import _atomic_json, _read_json_object
from .upgrade import _validate_process_identity

_QUERY_IMAGE_BUFFER_SIZE = 32768
_TRACE_FILENAME = "guard-trace.jsonl"
_CRASH_FILENAME = "guard-crash.log"


class UpdateGuardError(RuntimeError):
    pass


def _append_guard_trace(
    request_path: Path,
    stage: str,
    **detail: Any,
) -> None:
    """Best-effort durable trace for cross-process Windows diagnosis."""

    payload = {
        "format_version": 1,
        "timestamp": _utc_now(),
        "stage": stage,
        "pid": os.getpid(),
        "ppid": os.getppid(),
        **detail,
    }
    try:
        with (request_path.parent / _TRACE_FILENAME).open(
            "a",
            encoding="utf-8",
            newline="\n",
        ) as handle:
            handle.write(
                json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
    except (OSError, TypeError, ValueError):
        # Diagnostics must never replace the guarded upgrade failure semantics.
        pass


class ProcessIdentityHandle(Protocol):
    identity: dict[str, Any]

    def wait(self, timeout: float) -> bool: ...

    def terminate(self, timeout: float) -> bool: ...

    def close(self) -> None: ...


def inspect_process_identity(pid: int) -> dict[str, Any] | None:
    """Return an exact identity for PID, or ``None`` when it no longer exists."""
    if pid <= 0:
        return None
    if os.name == "nt":
        return _inspect_windows_process(pid)
    return _inspect_proc_process(pid)


def current_process_identity() -> dict[str, Any]:
    identity = inspect_process_identity(os.getpid())
    if identity is None:
        raise UpdateGuardError("Current process identity is unavailable")
    return identity


def open_process_identity_handle(
    identity: dict[str, Any],
) -> ProcessIdentityHandle | None:
    """Open the exact process once so identity validation and termination race."""
    expected = _validate_process_identity(identity)
    if os.name == "nt":
        return _open_windows_process_handle(expected)
    actual = _inspect_proc_process(expected["pid"])
    if actual != expected:
        return None
    return _PosixProcessIdentityHandle(expected)


def run_guard(
    request_path: Path,
    *,
    inspect_identity: Callable[[int], dict[str, Any] | None] = inspect_process_identity,
    run_command: Callable[[list[str]], None] | None = None,
    start_command: Callable[[list[str]], Any] | None = None,
    open_identity_handle: Callable[
        [dict[str, Any]], ProcessIdentityHandle | None
    ] = open_process_identity_handle,
    health_probe: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    _append_guard_trace(request_path, "run_guard_enter")
    request = _validate_request(_read_json_object(request_path))
    _append_guard_trace(
        request_path,
        "request_validated",
        transaction_id=request["transaction_id"],
        source_version=request["source_version"],
        target_version=request["target_version"],
    )
    guard_marker = Path(request["guard_marker"])
    guard_identity = current_process_identity()
    _append_guard_trace(
        request_path,
        "guard_identity_acquired",
        identity=guard_identity,
    )
    if guard_marker.is_file() and not guard_marker.is_symlink():
        existing_guard = _read_json_object(guard_marker)
        existing_identity = existing_guard.get("guard_identity")
        if (
            existing_guard.get("status") == "running"
            and isinstance(existing_identity, dict)
            and inspect_identity(existing_identity.get("pid", 0))
            == existing_identity
            and existing_identity != guard_identity
        ):
            raise UpdateGuardError("Another exact UpdateGuard is already running")
    _write_guard_marker(request, guard_identity, status="running")
    _append_guard_trace(request_path, "guard_marker_running")
    identity = request["manager_identity"]
    runner = run_command or _run_command
    starter = start_command or _start_command
    _append_guard_trace(
        request_path,
        "manager_exit_wait_started",
        manager_identity=identity,
    )
    if not _wait_known_process_exit(
        identity,
        timeout=float(request["manager_exit_timeout_seconds"]),
        inspect_identity=inspect_identity,
        open_identity_handle=open_identity_handle,
        sleep=sleep,
    ):
        raise UpdateGuardError("Known Manager did not exit before guard timeout")
    _append_guard_trace(request_path, "manager_exit_confirmed")

    install_command = list(request["install_command"])
    rollback_command = list(request["rollback_command"])
    restart_command = list(request["restart_command"])
    started_marker = Path(request["started_marker"])
    health_marker = Path(request["health_marker"])
    rollback_marker = Path(request["rollback_marker"])
    if rollback_marker.is_file() and not rollback_marker.is_symlink():
        existing_rollback = _read_json_object(rollback_marker)
        if (
            existing_rollback.get("format_version") == 2
            and existing_rollback.get("transaction_id")
            == request["transaction_id"]
            and existing_rollback.get("target_version")
            == request["target_version"]
            and existing_rollback.get("source_version")
            == request["source_version"]
        ):
            if existing_rollback.get("status") == "program_rollback_started":
                _append_guard_trace(
                    request_path,
                    "durable_rollback_resume_started",
                )
                _verify_file_digest(
                    Path(request["rollback_package"]),
                    request["rollback_package_sha256"],
                )
                runner(rollback_command)
                _append_guard_trace(
                    request_path,
                    "durable_rollback_apply_completed",
                )
                existing_rollback.update(
                    {"status": "program_rolled_back", "updated_at": _utc_now()}
                )
                _atomic_json(rollback_marker, existing_rollback)
                starter(restart_command)
                _append_guard_trace(
                    request_path,
                    "source_manager_restart_requested",
                )
                _write_guard_marker(
                    request,
                    guard_identity,
                    status="exited",
                    result=existing_rollback,
                )
                return existing_rollback
            if existing_rollback.get("status") == "program_rolled_back":
                # The rollback package was already applied and durably
                # acknowledged before a crash.  Only the old Manager launch
                # remains; never apply either package again.
                starter(restart_command)
                _append_guard_trace(
                    request_path,
                    "source_manager_restart_requested",
                )
                _write_guard_marker(
                    request,
                    guard_identity,
                    status="exited",
                    result=existing_rollback,
                )
                return existing_rollback
            if existing_rollback.get("status") == "program_rollback_failed":
                raise UpdateGuardError(
                    str(
                        existing_rollback.get("rollback_error")
                        or "Persisted program rollback failure"
                    )
                )
    _verify_file_digest(Path(request["package"]), request["package_sha256"])
    _verify_file_digest(
        Path(request["rollback_package"]), request["rollback_package_sha256"]
    )
    _append_guard_trace(request_path, "package_digests_verified")
    try:
        persisted_health = None
        if health_marker.is_file() and not health_marker.is_symlink():
            candidate_health = _read_json_object(health_marker)
            if (
                candidate_health.get("format_version") == 2
                and candidate_health.get("transaction_id")
                == request["transaction_id"]
                and candidate_health.get("target_version")
                == request["target_version"]
                and candidate_health.get("status") in {"healthy", "failed"}
            ):
                persisted_health = candidate_health
        if (
            persisted_health is not None
            and persisted_health.get("status") == "failed"
        ):
            raise UpdateGuardError(
                str(
                    persisted_health.get("error")
                    or "Persisted new Manager health failure"
                )
            )
        started = None
        if started_marker.is_file() and not started_marker.is_symlink():
            candidate = _read_json_object(started_marker)
            if (
                candidate.get("format_version") == 2
                and candidate.get("transaction_id")
                == request["transaction_id"]
                and candidate.get("target_version")
                == request["target_version"]
                and candidate.get("status") in {"started", "failed"}
            ):
                started = candidate
        if started is None:
            _append_guard_trace(request_path, "target_apply_started")
            runner(install_command)
            _append_guard_trace(request_path, "target_apply_completed")
            restarted = starter(restart_command)
            _append_guard_trace(
                request_path,
                "target_manager_restart_requested",
                spawned_pid=getattr(restarted, "pid", None),
            )
            _append_guard_trace(request_path, "started_marker_wait_started")
            started = _wait_marker(
                started_marker,
                transaction_id=request["transaction_id"],
                target_version=request["target_version"],
                statuses={"started", "failed"},
                timeout=float(request["health_timeout_seconds"]),
                sleep=sleep,
            )
            _append_guard_trace(
                request_path,
                "started_marker_received",
                status=started.get("status"),
            )
        elif (
            started.get("status") == "started"
            and persisted_health is None
        ):
            # The Manager that published this marker is the process the
            # startup recovery just asked to exit.  Keep the already-switched
            # target program, remove only the stale transaction marker, and
            # start a fresh target Manager without reapplying the package.
            started_marker.unlink()
            _append_guard_trace(
                request_path,
                "stale_started_marker_removed",
            )
            restarted = starter(restart_command)
            _append_guard_trace(
                request_path,
                "target_manager_restart_requested",
                spawned_pid=getattr(restarted, "pid", None),
            )
            _append_guard_trace(request_path, "started_marker_wait_started")
            started = _wait_marker(
                started_marker,
                transaction_id=request["transaction_id"],
                target_version=request["target_version"],
                statuses={"started", "failed"},
                timeout=float(request["health_timeout_seconds"]),
                sleep=sleep,
            )
            _append_guard_trace(
                request_path,
                "started_marker_received",
                status=started.get("status"),
            )
        if started.get("status") != "started":
            raise UpdateGuardError(
                str(started.get("error") or "Updated Manager rejected hand-off")
            )
        new_identity = _validate_process_identity(started["manager_identity"])
        _append_guard_trace(
            request_path,
            "authenticated_health_wait_started",
            manager_identity=new_identity,
        )
        health = _wait_authenticated_health(
            request,
            expected_identity=new_identity,
            probe=health_probe or _probe_manager_health,
            timeout=float(request["health_timeout_seconds"]),
            sleep=sleep,
        )
        _append_guard_trace(request_path, "authenticated_health_received")
        _append_guard_trace(request_path, "health_marker_wait_started")
        marker = _wait_marker(
            health_marker,
            transaction_id=request["transaction_id"],
            target_version=request["target_version"],
            statuses={"healthy", "failed"},
            timeout=float(request["health_timeout_seconds"]),
            sleep=sleep,
        )
        _append_guard_trace(
            request_path,
            "health_marker_received",
            status=marker.get("status"),
        )
        if marker.get("status") != "healthy":
            raise UpdateGuardError(
                str(marker.get("error") or "New Manager hard health failed")
            )
        if _validate_process_identity(marker["manager_identity"]) != new_identity:
            raise UpdateGuardError("Health marker Manager identity mismatch")
        if marker != health:
            raise UpdateGuardError("Authenticated health and marker disagree")
        _write_guard_marker(
            request, guard_identity, status="exited", result=health
        )
        _append_guard_trace(request_path, "guard_completed_healthy")
        return health
    except Exception as install_exc:
        _append_guard_trace(
            request_path,
            "target_handoff_failed",
            error_type=type(install_exc).__name__,
            error=str(install_exc) or type(install_exc).__name__,
        )
        try:
            started = _read_json_object(started_marker)
        except (OSError, ValueError, json.JSONDecodeError):
            started = {}
        new_identity = started.get("manager_identity")
        if isinstance(new_identity, dict):
            new_identity = _validate_process_identity(new_identity)
            _terminate_known_process(
                new_identity,
                timeout=float(request["manager_exit_timeout_seconds"]),
                open_identity_handle=open_identity_handle,
            )
        rollback: dict[str, Any] = {
            "format_version": 2,
            "transaction_id": request["transaction_id"],
            "target_version": request["target_version"],
            "source_version": request["source_version"],
            "manager_identity": new_identity or request["manager_identity"],
            "status": "program_rollback_started",
            "install_error": str(install_exc) or type(install_exc).__name__,
            "updated_at": _utc_now(),
        }
        _atomic_json(rollback_marker, rollback)
        _append_guard_trace(request_path, "rollback_marker_started")
        try:
            _verify_file_digest(
                Path(request["rollback_package"]),
                request["rollback_package_sha256"],
            )
            _append_guard_trace(request_path, "rollback_apply_started")
            runner(rollback_command)
            _append_guard_trace(request_path, "rollback_apply_completed")
        except Exception as rollback_exc:
            rollback.update(
                {
                    "status": "program_rollback_failed",
                    "rollback_error": str(rollback_exc)
                    or type(rollback_exc).__name__,
                    "updated_at": _utc_now(),
                }
            )
            _atomic_json(rollback_marker, rollback)
            _append_guard_trace(
                request_path,
                "rollback_failed",
                error_type=type(rollback_exc).__name__,
                error=str(rollback_exc) or type(rollback_exc).__name__,
            )
            raise UpdateGuardError(rollback["rollback_error"]) from rollback_exc
        rollback.update(
            {"status": "program_rolled_back", "updated_at": _utc_now()}
        )
        _atomic_json(rollback_marker, rollback)
        _append_guard_trace(request_path, "rollback_marker_completed")
        # The terminal marker describes the durable program-directory state,
        # so publish it before launching the old Manager.  A crash after this
        # point is restart-only recovery and must never reapply the package.
        restarted = starter(restart_command)
        _append_guard_trace(
            request_path,
            "source_manager_restart_requested",
            spawned_pid=getattr(restarted, "pid", None),
        )
        _write_guard_marker(
            request, guard_identity, status="exited", result=rollback
        )
        _append_guard_trace(request_path, "guard_completed_rollback")
        return rollback


def _wait_known_process_exit(
    identity: dict[str, Any],
    *,
    timeout: float,
    inspect_identity: Callable[[int], dict[str, Any] | None],
    open_identity_handle: Callable[
        [dict[str, Any]], ProcessIdentityHandle | None
    ] = open_process_identity_handle,
    sleep: Callable[[float], None],
) -> bool:
    handle = open_identity_handle(identity)
    if handle is not None:
        try:
            return handle.wait(timeout)
        finally:
            handle.close()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        actual = inspect_identity(identity["pid"])
        if actual is None or actual != identity:
            # PID reuse is not the known process and must never be touched.
            return True
        sleep(0.2)
    return False


def _wait_marker(
    path: Path,
    *,
    transaction_id: str,
    target_version: str,
    statuses: set[str],
    timeout: float,
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and not path.is_symlink():
            try:
                value = _read_json_object(path)
            except (OSError, ValueError, json.JSONDecodeError):
                value = {}
            if (
                value.get("transaction_id") == transaction_id
                and value.get("target_version") == target_version
                and value.get("status") in statuses
            ):
                return value
        sleep(0.25)
    raise UpdateGuardError(f"New Manager marker timed out: {path.name}")


def _validate_request(value: dict[str, Any]) -> dict[str, Any]:
    required = {
        "format_version",
        "transaction_id",
        "target_version",
        "source_version",
        "package",
        "package_sha256",
        "rollback_package",
        "rollback_package_sha256",
        "manager_identity",
        "guard_marker",
        "started_marker",
        "health_marker",
        "rollback_marker",
        "health_url",
        "auth_token_path",
        "install_command",
        "rollback_command",
        "restart_command",
        "manager_exit_timeout_seconds",
        "health_timeout_seconds",
        "requested_at",
    }
    if set(value) != required or value.get("format_version") != 2:
        raise UpdateGuardError("UpdateGuard request fields mismatch")
    value["manager_identity"] = _validate_process_identity(
        value["manager_identity"]
    )
    for key in (
        "transaction_id",
        "target_version",
        "source_version",
        "requested_at",
        "health_url",
    ):
        if not isinstance(value[key], str) or not value[key]:
            raise UpdateGuardError(f"UpdateGuard {key} is invalid")
    health_url = urllib.parse.urlsplit(value["health_url"])
    if (
        health_url.scheme != "http"
        or health_url.hostname not in {"127.0.0.1", "::1", "localhost"}
        or not health_url.path.endswith("/v1/health")
    ):
        raise UpdateGuardError("UpdateGuard health_url must be local /v1/health")
    for key in (
        "package",
        "rollback_package",
        "guard_marker",
        "started_marker",
        "health_marker",
        "rollback_marker",
        "auth_token_path",
    ):
        if not isinstance(value[key], str) or not Path(value[key]).is_absolute():
            raise UpdateGuardError(f"UpdateGuard {key} must be absolute")
    for key in ("package_sha256", "rollback_package_sha256"):
        if (
            not isinstance(value[key], str)
            or len(value[key]) != 64
            or any(character not in "0123456789abcdef" for character in value[key])
        ):
            raise UpdateGuardError(f"UpdateGuard {key} is invalid")
    for key in ("install_command", "rollback_command", "restart_command"):
        command = value[key]
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(item, str) or not item for item in command)
        ):
            raise UpdateGuardError(f"UpdateGuard {key} is invalid")
    for key in ("manager_exit_timeout_seconds", "health_timeout_seconds"):
        if (
            not isinstance(value[key], (int, float))
            or isinstance(value[key], bool)
            or value[key] <= 0
        ):
            raise UpdateGuardError(f"UpdateGuard {key} is invalid")
    return value


def _write_guard_marker(
    request: dict[str, Any],
    identity: dict[str, Any],
    *,
    status: str,
    result: dict[str, Any] | None = None,
) -> None:
    marker = {
        "format_version": 2,
        "transaction_id": request["transaction_id"],
        "target_version": request["target_version"],
        "status": status,
        "guard_identity": identity,
        "updated_at": _utc_now(),
    }
    if result is not None:
        marker["result_status"] = result.get("status")
    _atomic_json(Path(request["guard_marker"]), marker)


def _wait_authenticated_health(
    request: dict[str, Any],
    *,
    expected_identity: dict[str, Any],
    probe: Callable[[dict[str, Any]], dict[str, Any]],
    timeout: float,
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error = "Manager health endpoint is unavailable"
    while time.monotonic() < deadline:
        try:
            value = probe(request)
            handoff = value.get("upgrade_handoff")
            if not isinstance(handoff, dict):
                raise UpdateGuardError("Manager health omitted upgrade hand-off")
            if (
                handoff.get("transaction_id") != request["transaction_id"]
                or handoff.get("target_version") != request["target_version"]
                or value.get("dicepp_version") != request["target_version"]
                or _validate_process_identity(handoff.get("manager_identity"))
                != expected_identity
            ):
                raise UpdateGuardError("Authenticated Manager identity/version mismatch")
            status = handoff.get("status")
            if status == "healthy":
                return handoff
            if status == "failed":
                raise UpdateGuardError(
                    str(handoff.get("error") or "New Manager hard health failed")
                )
        except (
            OSError,
            ValueError,
            KeyError,
            json.JSONDecodeError,
            urllib.error.URLError,
            UpdateGuardError,
        ) as exc:
            last_error = str(exc) or type(exc).__name__
        sleep(0.25)
    raise UpdateGuardError(last_error)


def _probe_manager_health(request: dict[str, Any]) -> dict[str, Any]:
    token_path = Path(request["auth_token_path"])
    if token_path.is_symlink() or not token_path.is_file():
        raise UpdateGuardError("Manager API token file is unavailable")
    token = token_path.read_text(encoding="utf-8").strip()
    if not token:
        raise UpdateGuardError("Manager API token is empty")
    http_request = urllib.request.Request(
        request["health_url"],
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    with urllib.request.urlopen(http_request, timeout=2) as response:
        if response.status != 200:
            raise UpdateGuardError(
                f"Manager health returned HTTP {response.status}"
            )
        raw = response.read(64 * 1024)
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict) or value.get("ok") is not True:
        raise UpdateGuardError("Manager health response is invalid")
    return value


def _verify_file_digest(path: Path, expected: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise UpdateGuardError(f"Update package is unavailable: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise UpdateGuardError(f"Update package digest mismatch: {path.name}")


def _terminate_known_process(
    identity: dict[str, Any],
    *,
    timeout: float,
    open_identity_handle: Callable[
        [dict[str, Any]], ProcessIdentityHandle | None
    ],
) -> None:
    handle = open_identity_handle(identity)
    if handle is None:
        return
    try:
        if not handle.terminate(timeout):
            raise UpdateGuardError("New Manager did not exit before program rollback")
    finally:
        handle.close()


def _run_command(argv: list[str]) -> None:
    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=300,
            check=False,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if os.name == "nt"
                else 0
            ),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateGuardError(f"Update command could not run: {exc}") from exc
    if result.returncode:
        output = (result.stdout or "")[-4000:]
        raise UpdateGuardError(
            f"Update command failed with exit code {result.returncode}: {output}"
        )


def _start_command(argv: list[str]):
    try:
        return subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if os.name == "nt"
                else 0
            ),
        )
    except OSError as exc:
        raise UpdateGuardError(f"Updated Manager could not start: {exc}") from exc


class _PosixProcessIdentityHandle:
    """Best-effort POSIX fallback; Windows uses a race-free kernel handle."""

    def __init__(self, identity: dict[str, Any]) -> None:
        self.identity = identity

    def wait(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _inspect_proc_process(self.identity["pid"]) != self.identity:
                return True
            time.sleep(0.05)
        return False

    def terminate(self, timeout: float) -> bool:
        if _inspect_proc_process(self.identity["pid"]) != self.identity:
            return True
        os.kill(self.identity["pid"], signal.SIGTERM)
        if self.wait(timeout):
            return True
        if _inspect_proc_process(self.identity["pid"]) != self.identity:
            return True
        os.kill(self.identity["pid"], getattr(signal, "SIGKILL", signal.SIGTERM))
        return self.wait(timeout)

    def close(self) -> None:
        return None


class _WindowsProcessIdentityHandle:
    def __init__(self, handle: int, identity: dict[str, Any]) -> None:
        self._handle = handle
        self.identity = identity

    def wait(self, timeout: float) -> bool:
        milliseconds = max(0, min(int(timeout * 1000), 0xFFFFFFFE))
        result = ctypes.windll.kernel32.WaitForSingleObject(
            self._handle, milliseconds
        )
        if result == 0:
            return True
        if result == 0x102:
            return False
        raise UpdateGuardError("Waiting for the Manager process handle failed")

    def terminate(self, timeout: float) -> bool:
        if self.wait(0):
            return True
        if not ctypes.windll.kernel32.TerminateProcess(self._handle, 1):
            raise UpdateGuardError("Terminating the Manager process handle failed")
        return self.wait(timeout)

    def close(self) -> None:
        if self._handle:
            ctypes.windll.kernel32.CloseHandle(self._handle)
            self._handle = 0


def _open_windows_process_handle(
    expected: dict[str, Any],
) -> _WindowsProcessIdentityHandle | None:
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    access = 0x1000 | 0x00100000 | 0x0001
    handle = kernel32.OpenProcess(access, False, expected["pid"])
    if not handle:
        return None
    try:
        actual = _identity_from_windows_handle(handle, expected["pid"])
        if actual != expected:
            kernel32.CloseHandle(handle)
            return None
        return _WindowsProcessIdentityHandle(handle, actual)
    except BaseException:
        kernel32.CloseHandle(handle)
        raise


def _inspect_proc_process(pid: int) -> dict[str, Any] | None:
    proc = Path("/proc") / str(pid)
    try:
        executable = str((proc / "exe").resolve(strict=True))
        fields = (proc / "stat").read_text(encoding="utf-8").split()
        started_at = fields[21]
    except (FileNotFoundError, ProcessLookupError, PermissionError, IndexError, OSError):
        return None
    return {"pid": pid, "started_at": started_at, "executable": executable}


def _inspect_windows_process(pid: int) -> dict[str, Any] | None:
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    query = 0x1000
    handle = kernel32.OpenProcess(query, False, pid)
    if not handle:
        return None
    try:
        return _identity_from_windows_handle(handle, pid)
    finally:
        kernel32.CloseHandle(handle)


def _identity_from_windows_handle(
    handle: int, pid: int
) -> dict[str, Any] | None:
    kernel32 = ctypes.windll.kernel32
    creation = wintypes.FILETIME()
    exit_time = wintypes.FILETIME()
    kernel = wintypes.FILETIME()
    user = wintypes.FILETIME()
    if not kernel32.GetProcessTimes(
        handle,
        ctypes.byref(creation),
        ctypes.byref(exit_time),
        ctypes.byref(kernel),
        ctypes.byref(user),
    ):
        return None
    size = ctypes.c_ulong(_QUERY_IMAGE_BUFFER_SIZE)
    buffer = ctypes.create_unicode_buffer(size.value)
    if not kernel32.QueryFullProcessImageNameW(
        handle, 0, buffer, ctypes.byref(size)
    ):
        return None
    return {
        "pid": pid,
        "started_at": str(
            (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        ),
        "executable": str(Path(buffer.value).resolve()),
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="DicePP-UpdateGuard")
    parser.add_argument("--request", type=Path)
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--smoke-check", action="store_true")
    args = parser.parse_args(argv)
    if args.version:
        print(get_version())
        return 0
    if args.smoke_check:
        return 0
    if args.request is None:
        parser.error("--request is required")
    request_path = args.request.resolve()
    crash_handle = None
    faulthandler_enabled_here = False
    try:
        try:
            crash_handle = (request_path.parent / _CRASH_FILENAME).open("ab", buffering=0)
            if not faulthandler.is_enabled():
                faulthandler.enable(file=crash_handle, all_threads=True)
                faulthandler_enabled_here = True
        except (OSError, RuntimeError, ValueError) as exc:
            _append_guard_trace(
                request_path,
                "faulthandler_enable_failed",
                error_type=type(exc).__name__,
                error=str(exc) or type(exc).__name__,
            )
            if crash_handle is not None:
                crash_handle.close()
                crash_handle = None
        _append_guard_trace(request_path, "main_enter")
        try:
            result = run_guard(request_path)
        except (OSError, ValueError, UpdateGuardError) as exc:
            _append_guard_trace(
                request_path,
                "main_expected_failure",
                error_type=type(exc).__name__,
                error=str(exc) or type(exc).__name__,
            )
            print(f"UpdateGuard failed: {exc}", file=sys.stderr)
            return 1
        except BaseException as exc:
            _append_guard_trace(
                request_path,
                "main_unhandled_failure",
                error_type=type(exc).__name__,
                error=str(exc) or type(exc).__name__,
            )
            raise
        exit_code = (
            0
            if result.get("status") in {"healthy", "program_rolled_back"}
            else 1
        )
        _append_guard_trace(
            request_path,
            "main_exit",
            result_status=result.get("status"),
            exit_code=exit_code,
        )
        return exit_code
    finally:
        if faulthandler_enabled_here:
            faulthandler.disable()
        if crash_handle is not None:
            crash_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
