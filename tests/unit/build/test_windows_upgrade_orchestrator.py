from __future__ import annotations

from scripts.build.windows_upgrade_orchestrator import (
    _runtime_is_healthy,
    _wait_runtime_healthy,
)


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


def test_runtime_health_waits_for_restarted_runtime_snapshot() -> None:
    class Client:
        def __init__(self) -> None:
            self.calls = 0

        async def status(self) -> dict:
            self.calls += 1
            state = "stopped" if self.calls == 1 else "running"
            health = "unavailable" if self.calls == 1 else "healthy"
            return {
                "runtime_units": [
                    {
                        "runtime": {
                            "runtime_state": state,
                            "health": health,
                        }
                    }
                ]
            }

    client = Client()

    status = _wait_runtime_healthy(client, timeout=1)  # type: ignore[arg-type]

    assert client.calls == 2
    assert _runtime_is_healthy(status["runtime_units"]) is True
