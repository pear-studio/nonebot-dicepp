from __future__ import annotations

import inspect
import io
import json
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import yaml

import dashboard.src.app as dashboard_app
import dashboard.src.manager as dashboard_manager
import dashboard.src.launcher as dashboard_launcher
from dicepp_manager import factory as manager_factory


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
    assert services["manager"]["depends_on"]["dashboard"]["condition"] == "service_healthy"
    assert "healthcheck" in services["dashboard"]
    assert (
        "DICEPP_DASHBOARD_HEALTH_URL=http://dashboard:4090/api/health"
        in services["manager"]["environment"]
    )
    assert not any("dashboard/data" in volume for volume in services["manager"]["volumes"])
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


def test_windows_launcher_starts_dashboard_before_manager_recovery() -> None:
    source = inspect.getsource(dashboard_launcher.run_windows_launcher)

    assert source.index("_start_dashboard_server") < source.index(
        "manager_server = _start_server"
    )


def test_dashboard_probe_requires_semantic_health_and_rejects_404(monkeypatch) -> None:
    error = urllib.error.HTTPError(
        "http://dashboard/api/health",
        404,
        "not found",
        {},
        io.BytesIO(b"{}"),
    )
    monkeypatch.setattr(
        manager_factory.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    assert manager_factory._dashboard_probe()["status"] == "failed"


def test_control_probe_reads_semantic_dashboard_health_over_http(monkeypatch) -> None:
    heartbeat = datetime.now(timezone.utc).isoformat()

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return json.dumps(
                {
                    "status": "ok",
                    "component": "dashboard",
                    "control": {"latest_heartbeat": heartbeat},
                }
            ).encode()

    monkeypatch.setattr(
        manager_factory.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: Response(),
    )

    assert manager_factory._dashboard_probe()["status"] == "ok"
    assert manager_factory._control_channel_probe()["heartbeat"] == heartbeat
