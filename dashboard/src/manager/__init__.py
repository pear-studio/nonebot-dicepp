"""Dashboard-local Manager foundation."""

from .backends import UnsupportedRuntimeBackend, create_runtime_backend
from .docker_compose_runtime import DockerComposeRuntimeBackend
from .models import RuntimeLogs
from .process_runtime import ProcessRuntimeBackend
from .runtime import RuntimeBackend, RuntimeOperationUnsupported, UnavailableRuntimeBackend
from .service import ManagerService, OperationConflict, OperationFailed, UnknownBot

__all__ = [
    "DockerComposeRuntimeBackend",
    "ManagerService",
    "OperationConflict",
    "OperationFailed",
    "ProcessRuntimeBackend",
    "RuntimeBackend",
    "RuntimeOperationUnsupported",
    "RuntimeLogs",
    "UnsupportedRuntimeBackend",
    "UnavailableRuntimeBackend",
    "UnknownBot",
    "create_runtime_backend",
]
