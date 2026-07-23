"""Dashboard archive boundary.

Archive storage and transaction logic deliberately lives in ``dicepp_manager``.
This module exists only as an architectural marker for integrations that used
to import Dashboard filesystem helpers; it must not read or write instance
data or archive files.
"""

from __future__ import annotations

from dicepp_manager.client import ManagerClient


async def reconnect_archive_operation(
    client: ManagerClient,
    operation_id: str,
) -> dict:
    """Reconnect to a Manager-owned archive operation by durable id."""
    return await client.get_operation(operation_id)


__all__ = ["reconnect_archive_operation"]
