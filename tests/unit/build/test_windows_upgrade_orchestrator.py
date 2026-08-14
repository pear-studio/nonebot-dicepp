from __future__ import annotations

from dicepp_data import InstanceLayout

from scripts.build.windows_upgrade_orchestrator import (
    _copy_runtime_diagnostics,
    _manager_environment,
    _manual_restore_journal_passed,
    _runtime_is_healthy,
    _wait_current_directory_releasable,
    _wait_launcher_started,
    _wait_manager,
    _wait_runtime_healthy,
)


def test_current_directory_release_probe_retries_transient_windows_lock(
    tmp_path, monkeypatch
) -> None:
    current = tmp_path / "current"
    current.mkdir()
    real_rename = type(current).rename
    attempts = 0

    def transiently_locked(path, target):
        nonlocal attempts
        if path == current and attempts < 2:
            attempts += 1
            raise PermissionError("directory still in use")
        return real_rename(path, target)

    monkeypatch.setattr(type(current), "rename", transiently_locked)

    _wait_current_directory_releasable(tmp_path, timeout=1)

    assert attempts == 2
    assert current.is_dir()
    assert not (tmp_path / "current.matrix-probe").exists()


def _healthy_runtime_units() -> list[dict]:
    return [
        {
            "runtime_unit_id": "dicepp-runtime",
            "runtime": {"runtime_state": "running", "health": "healthy"},
        }
    ]


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


def test_manual_restore_journal_is_stable_proof_after_marker_cleanup() -> None:
    last = {
        "status": "failed",
        "detail": {
            "phase": "manual_restored",
            "rollback_status": "succeeded",
            "rolled_back": True,
            "manual_restore": {
                "requested": True,
                "program_directory_restored": True,
                "data_runtime_restored": True,
            },
        },
    }

    assert _manual_restore_journal_passed(last, _healthy_runtime_units()) is True

    last["detail"]["manual_restore"]["program_directory_restored"] = False
    assert _manual_restore_journal_passed(last, _healthy_runtime_units()) is False


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


def test_launcher_waits_for_packaged_handoff_boundary(tmp_path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.runtime_log.parent.mkdir(parents=True)
    layout.runtime_log.write_text(
        "launcher | DicePPManager server started\n"
        "launcher | startup complete\n",
        encoding="utf-8",
    )

    _wait_launcher_started(tmp_path, timeout=0.1)


def test_isolated_manager_server_and_launcher_client_share_port(tmp_path) -> None:
    env = _manager_environment(tmp_path, 23456)

    assert env["DICEPP_MANAGER_PORT"] == "23456"
    assert env["DICEPP_MANAGER_URL"] == "http://127.0.0.1:23456"


def test_manager_readiness_opens_windows_startup_recovery_gate(
    tmp_path, monkeypatch
) -> None:
    calls: list[str] = []

    class Client:
        async def health(self) -> dict:
            calls.append("health")
            return {"dicepp_version": "3.0.0rc21"}

        async def status(self) -> dict:
            calls.append("status")
            return {"health": {"dicepp_version": "3.0.0rc21"}}

    client = Client()
    monkeypatch.setattr(
        "scripts.build.windows_upgrade_orchestrator._client",
        lambda _root, _port: client,
    )

    observed, status = _wait_manager(
        tmp_path, 4091, version="3.0.0rc21", timeout=1
    )

    assert observed is client
    assert status["health"]["dicepp_version"] == "3.0.0rc21"
    assert calls == ["health", "status"]


def test_runtime_diagnostics_survive_isolated_instance_cleanup(tmp_path) -> None:
    root = tmp_path / "instance"
    diagnostics = tmp_path / "diagnostics"
    layout = InstanceLayout.from_root(root)
    layout.runtime_log.parent.mkdir(parents=True)
    layout.runtime_log.write_text("startup recovery failed\n", encoding="utf-8")
    velopack = layout.manager_recovery_dir / ("a" * 32) / "velopack-output.log"
    velopack.parent.mkdir(parents=True)
    velopack.write_text("velopack output\n", encoding="utf-8")

    _copy_runtime_diagnostics(root, diagnostics)

    assert (diagnostics / "dicepp-runtime.log").read_text(encoding="utf-8") == (
        "startup recovery failed\n"
    )
    assert (diagnostics / f"velopack-{'a' * 32}.log").read_text(
        encoding="utf-8"
    ) == "velopack output\n"
