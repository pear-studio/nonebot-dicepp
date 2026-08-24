"""DicePP lifecycle Manager.

This package is intentionally independent from the Dashboard package. The
Manager owns configuration, archive, and Bot control-channel coordination;
Dashboard owns the Bot process lifecycle.
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
from .models import ManagerOperation
from .owner import ManagerAlreadyRunning
from .service import ManagerService

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
]
