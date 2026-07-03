"""Opt-in Docker Compose runtime backend for Dashboard Manager."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

from .models import BotRuntimeStatus, ManagerAction, RuntimeLogs
from .process_runtime import _split_command


class _DockerComposeCommandError(RuntimeError):
    def __init__(self, message: str, detail: dict[str, object]) -> None:
        self.detail = detail
        super().__init__(message)


class DockerComposeRuntimeBackend:
    """Manage bot services through an explicit Docker Compose command."""

    def __init__(
        self,
        *,
        command: str,
        service_template: str,
        cwd: str | os.PathLike[str] | None = None,
        timeout: float = 10.0,
    ) -> None:
        command = command.strip()
        service_template = service_template.strip()
        if not command:
            raise ValueError("Docker Compose runtime command must not be empty")
        if not service_template:
            raise ValueError("Docker Compose runtime service must not be empty")
        if timeout <= 0:
            raise ValueError("Docker Compose runtime timeout must be greater than 0")

        self._command_argv = _split_command(command)
        if not self._command_argv:
            raise ValueError("Docker Compose runtime command must not be empty")
        self._service_template = service_template
        self._cwd = Path(cwd) if cwd else None
        self._timeout = timeout
        self._lock = asyncio.Lock()

    async def status(self, bot_ids: list[str]) -> dict[str, BotRuntimeStatus]:
        async with self._lock:
            statuses = await asyncio.gather(
                *(self._status_one(bot_id) for bot_id in bot_ids)
            )
            return dict(zip(bot_ids, statuses, strict=True))

    async def operate(
        self,
        bot_id: str,
        action: ManagerAction,
        request_detail: dict | None = None,
    ) -> BotRuntimeStatus:
        async with self._lock:
            service = self._service_for(bot_id)
            if action == "start":
                result = await self._run("compose", "up", "-d", service)
                return BotRuntimeStatus(
                    bot_id=bot_id,
                    runtime_state="running",
                    health="healthy",
                    message="Docker Compose service started",
                    detail=self._detail(service, result),
                )
            if action == "stop":
                result = await self._run("compose", "stop", service)
                return BotRuntimeStatus(
                    bot_id=bot_id,
                    runtime_state="stopped",
                    health="stopped",
                    message="Docker Compose service stopped",
                    detail=self._detail(service, result),
                )
            if action == "restart":
                result = await self._run("compose", "restart", service)
                return BotRuntimeStatus(
                    bot_id=bot_id,
                    runtime_state="running",
                    health="healthy",
                    message="Docker Compose service restarted",
                    detail=self._detail(service, result),
                )
            raise ValueError(f"Unsupported manager action: {action}")

    async def logs(self, bot_id: str, lines: int) -> RuntimeLogs:
        async with self._lock:
            service = self._service_for(bot_id)
            result = await self._run("compose", "logs", "--tail", str(lines), service)
            text, truncated = _limit_text_with_flag(result.stdout)
            return RuntimeLogs(
                bot_id=bot_id,
                text=text,
                source=f"docker-compose:{service}",
                lines=lines,
                truncated=truncated,
            )

    async def _status_one(self, bot_id: str) -> BotRuntimeStatus:
        service = self._service_for(bot_id)
        result = await self._run("compose", "ps", service)
        output = _limited_output(result)
        output_lower = output.lower()
        if "running" in output_lower or " up " in f" {output_lower} ":
            runtime_state = "running"
            health = "healthy"
        elif any(token in output_lower for token in ("stopped", "exited", "down")):
            runtime_state = "stopped"
            health = "stopped"
        else:
            runtime_state = "unknown"
            health = "unknown"
        return BotRuntimeStatus(
            bot_id=bot_id,
            runtime_state=runtime_state,
            health=health,
            message="Docker Compose service status queried",
            detail=self._detail(service, result),
        )

    def _service_for(self, bot_id: str) -> str:
        return self._service_template.replace("{bot_id}", bot_id)

    async def _run(
        self,
        *args: str,
    ) -> subprocess.CompletedProcess[str]:
        argv = [*self._command_argv, *args]
        return await asyncio.to_thread(self._run_sync, argv)

    def _run_sync(
        self,
        argv: list[str],
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                argv,
                cwd=str(self._cwd) if self._cwd else None,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            message = f"Docker Compose command timed out after {self._timeout:g}s"
            raise _DockerComposeCommandError(
                message,
                {"error": message},
            ) from exc
        if result.returncode != 0:
            output = _limited_output(result)
            message = f"Docker Compose command failed with exit code {result.returncode}"
            if output:
                message = f"{message}: {output}"
            raise _DockerComposeCommandError(
                message,
                {
                    **self._result_detail(result),
                    "error": message,
                },
            )
        return result

    @staticmethod
    def _detail(
        service: str,
        result: subprocess.CompletedProcess[str],
    ) -> dict[str, object]:
        return {
            "service": service,
            **DockerComposeRuntimeBackend._result_detail(result),
        }

    @staticmethod
    def _result_detail(
        result: subprocess.CompletedProcess[str],
    ) -> dict[str, object]:
        return {
            "returncode": result.returncode,
            "stdout": _limit_text(result.stdout),
            "stderr": _limit_text(result.stderr),
        }


def _limited_output(result: subprocess.CompletedProcess[str]) -> str:
    return _limit_text("\n".join(part for part in (result.stdout, result.stderr) if part))


def _limit_text(value: str | None, limit: int = 4000) -> str:
    return _limit_text_with_flag(value, limit=limit)[0]


def _limit_text_with_flag(value: str | None, limit: int = 4000) -> tuple[str, bool]:
    if not value:
        return "", False
    value = value.strip()
    if len(value) <= limit:
        return value, False
    return value[: limit - 3] + "...", True
