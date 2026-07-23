from __future__ import annotations

import inspect
from pathlib import Path

import yaml

import dashboard.src.app as dashboard_app
import dashboard.src.manager as dashboard_manager


def test_standard_compose_has_manager_boundary_and_socket_exclusivity() -> None:
    root = Path(inspect.getfile(dashboard_app)).resolve().parents[2]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert set(services) == {"bot", "dashboard", "manager"}
    assert services["manager"]["command"] == ["python", "-m", "dicepp_manager"]
    assert services["manager"].get("ports") is None
    assert "4091" in services["manager"]["expose"]
    assert "/var/run/docker.sock:/var/run/docker.sock" in services["manager"]["volumes"]
    assert not any("docker.sock" in volume for volume in services["dashboard"]["volumes"])
    assert "manager-net" in services["manager"]["networks"]
    assert "manager-net" in services["dashboard"]["networks"]
    assert services["bot"]["labels"] == {
        "io.dicepp.managed": "true",
        "io.dicepp.runtime-unit": "dicepp-runtime",
        "io.dicepp.deployment-schema": "2",
    }


def test_dashboard_import_boundary_exposes_only_manager_client() -> None:
    source = inspect.getsource(dashboard_app)
    assert "ManagerService" not in source
    assert "DockerRuntimeAdapter" not in source
    assert "ProcessRuntimeAdapter" not in source
    assert not hasattr(dashboard_manager, "ManagerService")
    assert hasattr(dashboard_manager, "ManagerClient")
