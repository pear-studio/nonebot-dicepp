"""DicePP lifecycle Manager.

This package is intentionally independent from the Dashboard package.  The
Dashboard talks to it through :class:`ManagerClient`; only the Manager imports
runtime adapters and owns lifecycle state.
"""

from .client import ManagerClient, ManagerClientError, ManagerIncompatible, ManagerUnavailable
from .deployment import (
    DASHBOARD_DEFAULT_PORT,
    DEPLOYMENT_SCHEMA_VERSION,
    MANAGER_API_VERSION,
    MANAGER_DEFAULT_PORT,
    MANAGER_VERSION,
    MINIMUM_DASHBOARD_API_VERSION,
)
from .models import ManagerOperation, RuntimeLogs, RuntimeUnit, RuntimeUnitStatus
from .owner import ManagerAlreadyRunning
from .service import ManagerService, OperationConflict, OperationFailed, UnknownRuntimeUnit

__all__ = [
    "DASHBOARD_DEFAULT_PORT",
    "DEPLOYMENT_SCHEMA_VERSION",
    "MANAGER_API_VERSION",
    "MANAGER_DEFAULT_PORT",
    "MANAGER_VERSION",
    "MINIMUM_DASHBOARD_API_VERSION",
    "ManagerClient",
    "ManagerClientError",
    "ManagerIncompatible",
    "ManagerAlreadyRunning",
    "ManagerOperation",
    "ManagerService",
    "ManagerUnavailable",
    "OperationConflict",
    "OperationFailed",
    "RuntimeLogs",
    "RuntimeUnit",
    "RuntimeUnitStatus",
    "UnknownRuntimeUnit",
]
