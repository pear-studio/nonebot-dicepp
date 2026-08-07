from __future__ import annotations

from scripts.build.windows_upgrade_orchestrator import _runtime_is_healthy


def test_runtime_health_uses_manager_status_runtime_envelope() -> None:
    units = [
        {
            "runtime_unit_id": "dicepp-runtime",
            "runtime": {
                "runtime_state": "running",
                "health": "healthy",
                "detail": {"pid": 1234},
            },
            "manager": {"operation_status": "idle"},
        }
    ]

    assert _runtime_is_healthy(units) is True
    assert (
        _runtime_is_healthy([{**units[0], "runtime": {"health": "healthy"}}])
        is False
    )
    assert _runtime_is_healthy(
        [{"runtime_state": "running", "health": "healthy"}]
    ) is False
