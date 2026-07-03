"""Runtime backend factory for Dashboard Manager."""

from __future__ import annotations

from ...config import ManagerRuntimeSettings
from ..docker_compose_runtime import DockerComposeRuntimeBackend
from ..process_runtime import ProcessRuntimeBackend
from ..runtime import RuntimeBackend, UnavailableRuntimeBackend


class UnsupportedRuntimeBackend(ValueError):
    """Raised when Manager is configured with an unsupported runtime backend."""


def create_runtime_backend(
    settings: ManagerRuntimeSettings | None = None,
) -> RuntimeBackend:
    """Create the configured runtime backend.

    Non-placeholder backends are intentionally opt-in and require explicit
    command templates.
    """
    settings = settings or ManagerRuntimeSettings.from_env()
    runtime = settings.runtime.strip().lower()
    if runtime == "" or runtime == "unavailable":
        return UnavailableRuntimeBackend()
    if runtime == "process":
        if not settings.process_command.strip():
            raise UnsupportedRuntimeBackend(
                "Process manager runtime requires DICEPP_MANAGER_PROCESS_COMMAND"
            )
        try:
            return ProcessRuntimeBackend(
                command=settings.process_command,
                cwd=settings.process_cwd,
                stop_timeout=settings.process_stop_timeout,
            )
        except ValueError as exc:
            raise UnsupportedRuntimeBackend(str(exc)) from exc
    if runtime == "docker-compose":
        if not settings.docker_command.strip():
            raise UnsupportedRuntimeBackend(
                "Docker Compose manager runtime requires DICEPP_MANAGER_DOCKER_COMMAND"
            )
        if not settings.docker_service_template.strip():
            raise UnsupportedRuntimeBackend(
                "Docker Compose manager runtime requires DICEPP_MANAGER_DOCKER_SERVICE"
            )
        try:
            return DockerComposeRuntimeBackend(
                command=settings.docker_command,
                service_template=settings.docker_service_template,
                cwd=settings.docker_cwd,
                timeout=settings.docker_timeout,
            )
        except ValueError as exc:
            raise UnsupportedRuntimeBackend(str(exc)) from exc
    raise UnsupportedRuntimeBackend(
        f"Unsupported manager runtime backend: {settings.runtime!r}"
    )
