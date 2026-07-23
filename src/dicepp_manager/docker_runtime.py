"""Docker RuntimeUnit adapter with a closed command and label allowlist."""

from __future__ import annotations

import asyncio
import http.client
import json
import re
import shlex
import socket
import subprocess
import urllib.parse

from .deployment import DEPLOYMENT_SCHEMA_LABEL, DEPLOYMENT_SCHEMA_VERSION, RUNTIME_MANAGED_LABEL, RUNTIME_UNIT_LABEL
from .models import ManagerAction, RuntimeLogs, RuntimeUnitStatus

_SAFE_UNIT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class DockerRuntimeError(RuntimeError):
    def __init__(self, message: str, *, detail: dict | None = None) -> None:
        self.detail = detail or {}
        super().__init__(message)


class DockerRuntimeAdapter:
    """Control exactly one labelled DicePP container per RuntimeUnit.

    No compose path, service name, shell fragment or action template is
    accepted.  A container must match all DicePP labels before its Docker ID is
    passed to the fixed ``start/stop/restart/logs`` command set.
    """

    def __init__(
        self,
        *,
        docker_command: str = "docker",
        allowed_runtime_units: set[str],
        timeout: float = 30.0,
    ) -> None:
        if not allowed_runtime_units or any(not _SAFE_UNIT.fullmatch(value) for value in allowed_runtime_units):
            raise ValueError("Docker runtime units must be a non-empty safe allowlist")
        if timeout <= 0:
            raise ValueError("Docker timeout must be greater than zero")
        argv = shlex.split(docker_command, posix=True)
        if len(argv) != 1:
            raise ValueError("Docker command must be one executable path, not a command template")
        self._docker = argv[0]
        self._allowed = frozenset(allowed_runtime_units)
        self._timeout = timeout
        self._lock = asyncio.Lock()

    async def status(self, runtime_unit_ids: list[str]) -> dict[str, RuntimeUnitStatus]:
        async with self._lock:
            return {unit_id: await self._status_one(unit_id) for unit_id in runtime_unit_ids}

    async def operate(self, runtime_unit_id: str, action: ManagerAction) -> RuntimeUnitStatus:
        async with self._lock:
            container_id = await self._resolve_container(runtime_unit_id)
            if action not in {"start", "stop", "restart"}:
                raise ValueError(f"Unsupported Manager action: {action}")
            await self._run(action, container_id)
            state = "stopped" if action == "stop" else "running"
            return RuntimeUnitStatus(
                runtime_unit_id,
                state,
                "stopped" if state == "stopped" else "healthy",
                f"Docker container {action} completed",
                {"container_id": container_id},
            )

    async def logs(self, runtime_unit_id: str, lines: int) -> RuntimeLogs:
        if not 1 <= lines <= 1000:
            raise ValueError("lines must be between 1 and 1000")
        async with self._lock:
            container_id = await self._resolve_container(runtime_unit_id)
            result = await self._run("logs", "--tail", str(lines), container_id)
        text = (result.stdout or "").strip()
        truncated = len(text) > 100_000
        if truncated:
            text = text[-100_000:]
        return RuntimeLogs(runtime_unit_id, text, f"docker:{container_id}", lines, truncated)

    async def runtime_logs(self, lines: int) -> RuntimeLogs:
        if len(self._allowed) != 1:
            raise DockerRuntimeError("Global runtime logs require exactly one RuntimeUnit")
        return await self.logs(next(iter(self._allowed)), lines)

    async def _status_one(self, runtime_unit_id: str) -> RuntimeUnitStatus:
        try:
            container_id = await self._resolve_container(runtime_unit_id)
        except DockerRuntimeError as exc:
            if exc.detail.get("not_found"):
                return RuntimeUnitStatus(runtime_unit_id, "stopped", "stopped", str(exc))
            raise
        result = await self._run("inspect", "--format", "{{.State.Status}}", container_id)
        state_text = (result.stdout or "").strip().lower()
        running = state_text == "running"
        return RuntimeUnitStatus(
            runtime_unit_id,
            "running" if running else "stopped",
            "healthy" if running else "stopped",
            f"Docker container state: {state_text or 'unknown'}",
            {"container_id": container_id, "docker_state": state_text},
        )

    async def _resolve_container(self, runtime_unit_id: str) -> str:
        if runtime_unit_id not in self._allowed:
            raise DockerRuntimeError(f"RuntimeUnit is not in the Docker allowlist: {runtime_unit_id}")
        result = await self._run(
            "ps",
            "-a",
            "--filter", f"label={RUNTIME_MANAGED_LABEL}=true",
            "--filter", f"label={RUNTIME_UNIT_LABEL}={runtime_unit_id}",
            "--filter", f"label={DEPLOYMENT_SCHEMA_LABEL}={DEPLOYMENT_SCHEMA_VERSION}",
            "--format", "{{.ID}}",
        )
        ids = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
        if not ids:
            raise DockerRuntimeError(f"No labelled Docker container for RuntimeUnit {runtime_unit_id}", detail={"not_found": True})
        if len(ids) != 1:
            raise DockerRuntimeError(f"Expected exactly one labelled container for RuntimeUnit {runtime_unit_id}")
        if not re.fullmatch(r"[0-9a-fA-F]{12,64}", ids[0]):
            raise DockerRuntimeError("Docker returned an invalid container id")
        return ids[0]

    async def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return await asyncio.to_thread(self._run_sync, [self._docker, *args])

    def _run_sync(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise DockerRuntimeError(f"Docker command timed out after {self._timeout:g}s") from exc
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "Docker command failed").strip()
            raise DockerRuntimeError(
                f"Docker command failed with exit code {result.returncode}: {message}",
                detail={"returncode": result.returncode, "stderr": result.stderr[-4000:], "stdout": result.stdout[-4000:]},
            )
        return result


class _UnixSocketConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._socket_path)


class DockerSocketRuntimeAdapter:
    """Minimal Docker Engine client restricted to the Manager socket mount."""

    def __init__(
        self,
        *,
        socket_path: str = "/var/run/docker.sock",
        allowed_runtime_units: set[str],
        timeout: float = 30.0,
    ) -> None:
        if not allowed_runtime_units or any(not _SAFE_UNIT.fullmatch(value) for value in allowed_runtime_units):
            raise ValueError("Docker runtime units must be a non-empty safe allowlist")
        if timeout <= 0:
            raise ValueError("Docker timeout must be greater than zero")
        self._socket_path = socket_path
        self._allowed = frozenset(allowed_runtime_units)
        self._timeout = timeout
        self._lock = asyncio.Lock()

    async def status(self, runtime_unit_ids: list[str]) -> dict[str, RuntimeUnitStatus]:
        async with self._lock:
            return {unit_id: await self._status_one(unit_id) for unit_id in runtime_unit_ids}

    async def operate(self, runtime_unit_id: str, action: ManagerAction) -> RuntimeUnitStatus:
        if action not in {"start", "stop", "restart"}:
            raise ValueError(f"Unsupported Manager action: {action}")
        async with self._lock:
            container_id = await self._resolve_container(runtime_unit_id)
            suffix = "?t=10" if action in {"stop", "restart"} else ""
            await self._request("POST", f"/containers/{container_id}/{action}{suffix}", expected={204, 304})
        state = "stopped" if action == "stop" else "running"
        return RuntimeUnitStatus(
            runtime_unit_id,
            state,
            "stopped" if state == "stopped" else "healthy",
            f"Docker container {action} completed",
            {"container_id": container_id},
        )

    async def logs(self, runtime_unit_id: str, lines: int) -> RuntimeLogs:
        if not 1 <= lines <= 1000:
            raise ValueError("lines must be between 1 and 1000")
        async with self._lock:
            container_id = await self._resolve_container(runtime_unit_id)
            raw = await self._request(
                "GET",
                f"/containers/{container_id}/logs?stdout=1&stderr=1&tail={lines}",
                expected={200},
                raw=True,
            )
        text = _decode_docker_logs(raw)
        truncated = len(text) > 100_000
        if truncated:
            text = text[-100_000:]
        return RuntimeLogs(runtime_unit_id, text.strip(), f"docker:{container_id}", lines, truncated)

    async def runtime_logs(self, lines: int) -> RuntimeLogs:
        if len(self._allowed) != 1:
            raise DockerRuntimeError("Global logs require exactly one RuntimeUnit")
        return await self.logs(next(iter(self._allowed)), lines)

    async def _status_one(self, runtime_unit_id: str) -> RuntimeUnitStatus:
        try:
            container_id = await self._resolve_container(runtime_unit_id)
        except DockerRuntimeError as exc:
            if exc.detail.get("not_found"):
                return RuntimeUnitStatus(runtime_unit_id, "stopped", "stopped", str(exc))
            raise
        payload = await self._request("GET", f"/containers/{container_id}/json", expected={200})
        state = payload.get("State", {}) if isinstance(payload, dict) else {}
        running = state.get("Running") is True
        return RuntimeUnitStatus(
            runtime_unit_id,
            "running" if running else "stopped",
            "healthy" if running else "stopped",
            f"Docker container state: {state.get('Status', 'unknown')}",
            {"container_id": container_id, "docker_state": state.get("Status")},
        )

    async def _resolve_container(self, runtime_unit_id: str) -> str:
        if runtime_unit_id not in self._allowed:
            raise DockerRuntimeError(f"RuntimeUnit is not in the Docker allowlist: {runtime_unit_id}")
        filters = json.dumps({
            "label": [
                f"{RUNTIME_MANAGED_LABEL}=true",
                f"{RUNTIME_UNIT_LABEL}={runtime_unit_id}",
                f"{DEPLOYMENT_SCHEMA_LABEL}={DEPLOYMENT_SCHEMA_VERSION}",
            ]
        })
        payload = await self._request(
            "GET",
            "/containers/json?all=1&filters=" + urllib.parse.quote(filters),
            expected={200},
        )
        if not isinstance(payload, list) or not payload:
            raise DockerRuntimeError(
                f"No labelled Docker container for RuntimeUnit {runtime_unit_id}",
                detail={"not_found": True},
            )
        if len(payload) != 1:
            raise DockerRuntimeError(f"Expected exactly one labelled container for RuntimeUnit {runtime_unit_id}")
        container_id = payload[0].get("Id") if isinstance(payload[0], dict) else None
        if not isinstance(container_id, str) or not re.fullmatch(r"[0-9a-fA-F]{12,64}", container_id):
            raise DockerRuntimeError("Docker returned an invalid container id")
        return container_id

    async def _request(
        self,
        method: str,
        path: str,
        *,
        expected: set[int],
        raw: bool = False,
        json_body: dict | None = None,
    ):
        return await asyncio.to_thread(
            self._request_sync, method, path, expected, raw, json_body
        )

    def _request_sync(
        self,
        method: str,
        path: str,
        expected: set[int],
        raw: bool,
        json_body: dict | None = None,
    ):
        connection = _UnixSocketConnection(self._socket_path, self._timeout)
        try:
            body = (
                json.dumps(json_body, separators=(",", ":")).encode("utf-8")
                if json_body is not None
                else None
            )
            headers = (
                {
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                }
                if body is not None
                else {}
            )
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            body = response.read()
        except (OSError, http.client.HTTPException) as exc:
            raise DockerRuntimeError(f"Docker socket request failed: {exc}") from exc
        finally:
            connection.close()
        if response.status not in expected:
            message = body.decode("utf-8", errors="replace")[-4000:]
            raise DockerRuntimeError(
                f"Docker API returned HTTP {response.status}: {message}",
                detail={"status_code": response.status},
            )
        if raw:
            return body
        if not body:
            return {}
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DockerRuntimeError("Docker API returned invalid JSON") from exc


def _decode_docker_logs(raw: bytes) -> str:
    """Decode Docker's multiplexed stdout/stderr stream, or plain TTY bytes."""
    chunks: list[bytes] = []
    offset = 0
    while offset + 8 <= len(raw) and raw[offset] in {0, 1, 2}:
        size = int.from_bytes(raw[offset + 4 : offset + 8], "big")
        start = offset + 8
        end = start + size
        if end > len(raw):
            break
        chunks.append(raw[start:end])
        offset = end
    data = b"".join(chunks) if chunks and offset == len(raw) else raw
    return data.decode("utf-8", errors="replace")
