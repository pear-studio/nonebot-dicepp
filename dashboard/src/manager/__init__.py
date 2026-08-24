"""Compatibility import for the Dashboard's external Manager client only.

Manager configuration/archive client types live in :mod:`dicepp_manager`;
Bot process control belongs to :mod:`dashboard.src.bot_process`.
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
