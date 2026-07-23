"""Compatibility import for the Dashboard's external Manager client only.

Manager core/runtime adapters live in :mod:`dicepp_manager`; keeping those
inside the Dashboard would reintroduce the unsupported direct-control path.
"""

from dicepp_manager.client import (
    ManagerClient,
    ManagerClientError,
    ManagerIncompatible,
    ManagerUnavailable,
)

__all__ = [
    "ManagerClient",
    "ManagerClientError",
    "ManagerIncompatible",
    "ManagerUnavailable",
]
