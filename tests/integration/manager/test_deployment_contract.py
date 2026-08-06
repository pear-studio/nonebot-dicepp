from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import yaml

import dashboard.src.app as dashboard_app
import dashboard.src.launcher as dashboard_launcher
import dashboard.src.manager as dashboard_manager
from dicepp_data import InstanceLayout
from dicepp_manager import factory as manager_factory
from dicepp_manager.config import ManagerSettings
from dicepp_manager.upgrade import SimpleWindowsVelopackUpgradeAdapter


def test_standard_compose_has_manager_boundary_and_socket_exclusivity() -> None:
    root = Path(inspect.getfile(dashboard_app)).resolve().parents[2]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert set(services) == {"bot", "dashboard", "manager"}
    assert services["manager"]["command"] == ["python", "-m", "dicepp_manager"]
    assert services["manager"]["ports"] == ["127.0.0.1:4091:4091"]
    assert "/var/run/docker.sock:/var/run/docker.sock" in services["manager"]["volumes"]
    dashboard_volumes = services["dashboard"]["volumes"]
    assert not any("docker.sock" in volume for volume in dashboard_volumes)
    assert "./manager/state:/app/manager/state:ro" in dashboard_volumes
    assert not any("manager/control" in volume for volume in dashboard_volumes)
    assert "./manager/control:/app/manager/control:ro" in services["bot"]["volumes"]
    assert services["dashboard"]["depends_on"]["manager"]["condition"] == "service_healthy"
    assert services["bot"]["depends_on"]["manager"]["condition"] == "service_healthy"
    assert "DICEPP_MANAGER_URL=http://manager:4091" in services["bot"]["environment"]


def test_dashboard_import_boundary_exposes_only_manager_client() -> None:
    source = inspect.getsource(dashboard_app)
    assert "ManagerService" not in source
    assert "DockerRuntimeAdapter" not in source
    assert "ProcessRuntimeAdapter" not in source
    assert not hasattr(dashboard_manager, "ManagerService")
    assert hasattr(dashboard_manager, "ManagerClient")


def test_windows_launcher_starts_dashboard_before_manager_recovery() -> None:
    source = inspect.getsource(dashboard_launcher.run_windows_launcher)
    assert source.index("_start_dashboard_server") < source.index(
        "manager_server = _start_server"
    )


def test_windows_factory_waits_for_launcher_exit_and_restarts_in_background(
    monkeypatch,
    tmp_path: Path,
) -> None:
    for name in ("Update.exe", "DicePP.exe"):
        (tmp_path / name).write_bytes(name.encode())
    monkeypatch.setattr(
        manager_factory,
        "os",
        SimpleNamespace(name="nt", environ={"DICEPP_VELOPACK_APPLY_COMMAND": ""}),
    )
    service = manager_factory.create_manager_service(
        ManagerSettings(
            layout=InstanceLayout.from_root(tmp_path),
            runtime="unavailable",
            release_scheduler_enabled=False,
        )
    )
    try:
        adapter = service.upgrade_coordinator.platform_adapter
        assert isinstance(adapter, SimpleWindowsVelopackUpgradeAdapter)
        assert "--norestart" not in adapter.install_command
        wait_index = adapter.install_command.index("--waitPid")
        assert adapter.install_command[wait_index + 1] == "{wait_pid}"
        assert adapter.install_command[-2:] == ["--", "--background"]
    finally:
        service.close()


def test_factory_binds_archive_control_health_to_manager_service(tmp_path: Path) -> None:
    service = manager_factory.create_manager_service(
        ManagerSettings(
            layout=InstanceLayout.from_root(tmp_path),
            runtime="unavailable",
            release_scheduler_enabled=False,
        )
    )
    try:
        assert service.control_service is not None
        assert service.archive_coordinator.control_probe.__self__ is service.control_service
        assert service.archive_coordinator.control_probe()["message"] == "No Bot control heartbeat"
    finally:
        service.close()


def test_factory_has_no_dashboard_health_recovery_dependency() -> None:
    source = inspect.getsource(manager_factory)
    assert "DICEPP_DASHBOARD_HEALTH_URL" not in source
    assert "_dashboard_probe" not in source
