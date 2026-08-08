"""Unit tests for the Linux upgrade orchestrator — no Docker required."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import socket
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from scripts.build.linux_upgrade_orchestrator import (
    SCENARIO_ASSERTIONS,
    SCENARIO_OBSERVATION_FIELDS,
    _DockerSocketProxy,
    _LinuxUpgradeOrchestrator,
    _ManagerApiClient,
    _ManagerApiError,
    _OrchestratorUnavailable,
    _build_scenario_result,
    _docker,
    _docker_available,
    _find_bundle,
    _http_json,
    _image_ids_by_role,
    _load_docker_image_from_bundle,
    _minimal_test_compose,
    _read_bundle_manifest,
    _result,
    _sha256_file,
    _unavailable,
)
from scripts.build.upgrade_evidence import (
    LINUX_REQUIRED_SCENARIOS,
    SCENARIO_ASSERTIONS as EVIDENCE_ASSERTIONS,
    SCENARIO_OBSERVATION_FIELDS as EVIDENCE_OBSERVATIONS,
)
from tests.support.linux_bundle import (
    build_bundle_bytes_with_non_object_manifest,
    build_bundle_bytes_without_manifest,
    read_bundle_member,
    write_linux_bundle,
)
from tests.support.manager_journal import write_manager_journal


def test_docker_proxy_failpoint_rejects_one_container_create() -> None:
    injected: list[str] = []
    proxy = _DockerSocketProxy(
        Path("unused"), on_create_failure=lambda: injected.append("before-create")
    )
    proxy.arm_container_create_failure()
    client, server = socket.socketpair()
    worker = threading.Thread(target=proxy._handle, args=(server,))
    worker.start()
    client.sendall(
        b"POST /v1.47/containers/create?name=dicepp HTTP/1.1\r\n"
        b"Host: docker\r\nContent-Length: 2\r\n\r\n{}"
    )
    response = bytearray()
    while True:
        chunk = client.recv(4096)
        if not chunk:
            break
        response.extend(chunk)
    client.close()
    worker.join(timeout=2)

    assert b"500 Internal Server Error" in response
    assert proxy.failure_count == 1
    assert proxy.failure_status == 500
    assert injected == ["before-create"]


# ---------------------------------------------------------------------------
# Contract consistency
# ---------------------------------------------------------------------------


def test_orchestrator_scenario_assertions_match_upgrade_evidence() -> None:
    """The orchestrator's assertion keys must match upgrade_evidence exactly."""
    assert tuple(SCENARIO_ASSERTIONS) == LINUX_REQUIRED_SCENARIOS
    for scenario in SCENARIO_ASSERTIONS:
        assert SCENARIO_ASSERTIONS[scenario] == EVIDENCE_ASSERTIONS[scenario], (
            f"Scenario {scenario!r} assertions differ from upgrade_evidence"
        )


def test_orchestrator_scenario_observations_match_upgrade_evidence() -> None:
    """The orchestrator's observation keys must match upgrade_evidence exactly."""
    assert tuple(SCENARIO_OBSERVATION_FIELDS) == LINUX_REQUIRED_SCENARIOS
    for scenario in SCENARIO_OBSERVATION_FIELDS:
        assert (
            SCENARIO_OBSERVATION_FIELDS[scenario]
            == EVIDENCE_OBSERVATIONS[scenario]
        ), (
            f"Scenario {scenario!r} observation fields differ from upgrade_evidence"
        )


# ---------------------------------------------------------------------------
# _find_bundle
# ---------------------------------------------------------------------------


def test_find_bundle_finds_matching_purpose() -> None:
    assets = [
        {"name": "a", "purpose": "linux-bundle", "path": "/tmp/a"},
        {"name": "b", "purpose": "other", "path": "/tmp/b"},
    ]
    found = _find_bundle(assets, "linux-bundle")
    assert found is not None
    assert found["name"] == "a"


def test_find_bundle_returns_none_for_missing_purpose() -> None:
    assets = [{"name": "a", "purpose": "other"}]
    assert _find_bundle(assets, "linux-bundle") is None


def test_find_bundle_returns_none_for_empty_list() -> None:
    assert _find_bundle([], "linux-bundle") is None


def test_find_bundle_handles_missing_purpose_key() -> None:
    assets = [{"name": "no-purpose"}]
    assert _find_bundle(assets, "linux-bundle") is None


# ---------------------------------------------------------------------------
# _unavailable
# ---------------------------------------------------------------------------


def test_unavailable_result_contract() -> None:
    context = {
        "contract_version": 1,
        "platform": "linux",
        "arch": "amd64",
        "source_version": "3.0.0rc19",
        "target_version": "3.1.0",
        "scenario": "healthy_commit",
        "source_assets": [],
        "target_assets": [],
        "target_commit_sha": "a" * 40,
    }
    result = _unavailable(context, "Docker is unavailable")

    assert result["contract_version"] == 1
    assert result["platform"] == "linux"
    assert result["arch"] == "amd64"
    assert result["status"] == "unavailable"
    assert result["assertions"] == {}
    assert result["observations"]["reason"] == "Docker is unavailable"


def test_unavailable_uses_defaults_for_missing_context_keys() -> None:
    result = _unavailable({}, "test reason")
    assert result["platform"] == "linux"
    assert result["arch"] == "amd64"
    assert result["source_version"] == ""
    assert result["target_version"] == ""
    assert result["scenario"] == ""
    assert result["status"] == "unavailable"
    assert result["observations"]["reason"] == "test reason"


# ---------------------------------------------------------------------------
# _result
# ---------------------------------------------------------------------------


def test_result_passed_contract() -> None:
    context = {
        "platform": "linux",
        "arch": "amd64",
        "source_version": "3.0.0rc19",
        "target_version": "3.1.0",
    }
    result = _result(
        context,
        "healthy_commit",
        passed=True,
        assertions={"source_started": True},
        observations={"health_status": "healthy"},
    )
    assert result["contract_version"] == 1
    assert result["status"] == "passed"
    assert result["assertions"] == {"source_started": True}


def test_result_failed_contract() -> None:
    context = {
        "platform": "linux",
        "arch": "amd64",
        "source_version": "3.0.0rc19",
        "target_version": "3.1.0",
    }
    result = _result(
        context,
        "healthy_commit",
        passed=False,
        assertions={"source_started": False},
        observations={"health_status": "unhealthy"},
    )
    assert result["status"] == "failed"


# ---------------------------------------------------------------------------
# _build_scenario_result
# ---------------------------------------------------------------------------


def test_build_scenario_result_uses_orchestrator_versions(tmp_path: Path) -> None:
    """_build_scenario_result reads version info from the orchestrator instance."""
    orch = _LinuxUpgradeOrchestrator(
        source_bundle=tmp_path / "src.zip",
        source_version="3.0.0rc19",
        target_bundle=tmp_path / "tgt.zip",
        target_version="3.1.0",
        work_dir=tmp_path,
    )
    result = _build_scenario_result(
        orch,
        "healthy_commit",
        assertions={"source_started": True},
        observations={"health_status": "healthy"},
    )
    assert result["platform"] == "linux"
    assert result["arch"] == "amd64"
    assert result["source_version"] == "3.0.0rc19"
    assert result["target_version"] == "3.1.0"
    assert result["scenario"] == "healthy_commit"
    assert result["status"] == "passed"


# ---------------------------------------------------------------------------
# _minimal_test_compose
# ---------------------------------------------------------------------------


_VALID_SHIPPED_COMPOSE = """\
services:
  manager:
    image: ghcr.io/pear-studio/nonebot-dicepp:${DICEPP_IMAGE_TAG:-latest}
    volumes: ["./config:/app/config"]
  bot:
    image: ghcr.io/pear-studio/nonebot-dicepp:${DICEPP_IMAGE_TAG:-latest}
    volumes: ["./config:/app/config"]
  dashboard:
    image: ghcr.io/pear-studio/dicepp-dashboard:${DICEPP_IMAGE_TAG:-latest}
    volumes: ["./config:/app/config"]
"""

_MINIMAL_SHIPPED_COMPOSE = """\
services:
  manager:
    image: ghcr.io/pear-studio/nonebot-dicepp:${DICEPP_IMAGE_TAG:-latest}
  bot:
    image: ghcr.io/pear-studio/nonebot-dicepp:${DICEPP_IMAGE_TAG:-latest}
"""


def test_minimal_test_compose_is_byte_identical_passthrough(tmp_path: Path) -> None:
    content = _minimal_test_compose(
        _VALID_SHIPPED_COMPOSE, tmp_path, "3.0.0rc19"
    )
    assert content == _VALID_SHIPPED_COMPOSE


def test_minimal_test_compose_keeps_manager_and_bot(tmp_path: Path) -> None:
    content = _minimal_test_compose(
        _VALID_SHIPPED_COMPOSE, tmp_path, "3.0.0rc19"
    )
    assert "manager:" in content
    assert "bot:" in content
    assert "ghcr.io/pear-studio/nonebot-dicepp:${DICEPP_IMAGE_TAG:-latest}" in content


def test_minimal_test_compose_includes_dashboard_when_present(tmp_path: Path) -> None:
    content = _minimal_test_compose(
        _VALID_SHIPPED_COMPOSE, tmp_path, "3.0.0rc19"
    )
    assert "dashboard:" in content
    assert "dicepp-dashboard:${DICEPP_IMAGE_TAG:-latest}" in content


def test_minimal_test_compose_omits_dashboard_when_absent(tmp_path: Path) -> None:
    content = _minimal_test_compose(
        _MINIMAL_SHIPPED_COMPOSE, tmp_path, "3.0.0rc19"
    )
    assert "dashboard:" not in content


def test_minimal_test_compose_rejects_missing_services_block(tmp_path: Path) -> None:
    with pytest.raises(_OrchestratorUnavailable, match="does not define any services"):
        _minimal_test_compose("version: '3.8'\n", tmp_path, "3.0.0rc19")


def test_minimal_test_compose_rejects_missing_manager_service(tmp_path: Path) -> None:
    with pytest.raises(_OrchestratorUnavailable, match="does not define a manager"):
        _minimal_test_compose(
            "services:\n  bot:\n    image: test\n", tmp_path, "3.0.0rc19"
        )


# ---------------------------------------------------------------------------
# _read_bundle_manifest / _image_ids_by_role
# ---------------------------------------------------------------------------


def test_read_bundle_manifest_extracts_package_json(tmp_path: Path) -> None:
    bundle = write_linux_bundle(
        tmp_path / "bundle.zip",
        version="3.1.0",
        compose="services:\n  manager:\n    image: test:${DICEPP_IMAGE_TAG:-latest}\n",
    )
    manifest = _read_bundle_manifest(bundle)
    assert manifest["version"] == "3.1.0"
    assert len(manifest["images"]) == 2


def test_read_bundle_manifest_rejects_missing_member(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.zip"
    bundle.write_bytes(build_bundle_bytes_without_manifest())
    with pytest.raises(_OrchestratorUnavailable, match="cannot read"):
        _read_bundle_manifest(bundle)


def test_read_bundle_manifest_rejects_corrupt_zip(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.zip"
    bundle.write_bytes(b"not a zip file at all")
    with pytest.raises(_OrchestratorUnavailable, match="cannot read"):
        _read_bundle_manifest(bundle)


def test_read_bundle_manifest_rejects_non_object_json(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.zip"
    bundle.write_bytes(build_bundle_bytes_with_non_object_manifest())
    with pytest.raises(_OrchestratorUnavailable, match="must be an object"):
        _read_bundle_manifest(bundle)


def test_image_ids_by_role_maps_roles() -> None:
    manifest = {
        "images": [
            {
                "role": "bot",
                "reference": "ghcr.io/pear-studio/nonebot-dicepp:v3.1.0",
                "image_id": "sha256:" + "a" * 64,
            },
            {
                "role": "dashboard",
                "reference": "ghcr.io/pear-studio/dicepp-dashboard:v3.1.0",
                "image_id": "sha256:" + "b" * 64,
            },
        ]
    }
    assert _image_ids_by_role(manifest) == {
        "bot": "sha256:" + "a" * 64,
        "dashboard": "sha256:" + "b" * 64,
    }


def test_image_ids_by_role_rejects_missing_images() -> None:
    with pytest.raises(_OrchestratorUnavailable, match="no image records"):
        _image_ids_by_role({"version": "3.1.0"})


def test_image_ids_by_role_rejects_invalid_record() -> None:
    with pytest.raises(_OrchestratorUnavailable, match="invalid"):
        _image_ids_by_role({"images": [{"role": "bot"}]})


# ---------------------------------------------------------------------------
# _load_docker_image_from_bundle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "member_path",
    ["/etc/passwd", "../escape.tar", "images/../../escape.tar"],
)
def test_load_docker_image_from_bundle_rejects_unsafe_member_path(
    tmp_path: Path, member_path: str
) -> None:
    """Zip-slip member paths must fail closed before any extraction."""
    bundle = write_linux_bundle(
        tmp_path / "bundle.zip",
        version="3.1.0",
        image_archive_path=member_path,
    )
    with pytest.raises(_OrchestratorUnavailable, match="unsafe"):
        _load_docker_image_from_bundle(bundle, tmp_path / "work", "source")


def test_load_docker_image_from_bundle_raises_when_inspect_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inspect failure after docker load must be fail-closed, not silent."""
    bundle = write_linux_bundle(
        tmp_path / "bundle.zip",
        version="3.1.0",
        archive_member=b"stub archive",
    )

    def fake_run(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess:
        if argv[:2] == ["docker", "image"]:
            raise subprocess.CalledProcessError(1, argv, stderr="no such image")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(_OrchestratorUnavailable, match="cannot inspect"):
        _load_docker_image_from_bundle(bundle, tmp_path / "work", "source")


# ---------------------------------------------------------------------------
# _http_json seam
# ---------------------------------------------------------------------------


class _FakeHttpResponse:
    def __init__(self, status: int, payload: dict) -> None:
        self.status = status
        self._raw = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._raw


def test_http_json_get_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: urllib.request.Request, timeout: object) -> object:
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["method"] = request.get_method()
        return _FakeHttpResponse(200, {"ok": True, "dicepp_version": "3.0.0rc19"})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    status, payload = _http_json(
        "GET", "http://127.0.0.1:4091/v1/health", token="secret"
    )
    assert status == 200
    assert payload["dicepp_version"] == "3.0.0rc19"
    assert captured["headers"] == {"Authorization": "Bearer secret"}
    assert captured["method"] == "GET"


def test_http_json_post_sends_json_body(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: urllib.request.Request, timeout: object) -> object:
        captured["body"] = request.data
        captured["headers"] = dict(request.header_items())
        return _FakeHttpResponse(202, {"ok": True})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    _http_json(
        "POST",
        "http://127.0.0.1:4091/v1/upgrades/confirm",
        token="secret",
        body={"version": "3.1.0", "confirmation_token": "tok-123"},
    )
    assert json.loads(captured["body"]) == {
        "version": "3.1.0",
        "confirmation_token": "tok-123",
    }
    # urllib normalises header keys; compare case-insensitively.
    normalized_headers = {key.lower(): value for key, value in captured["headers"].items()}
    assert normalized_headers["content-type"] == "application/json"


def test_http_json_http_error_becomes_manager_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request: urllib.request.Request, timeout: object) -> object:
        raise urllib.error.HTTPError(
            request.full_url,
            409,
            "Conflict",
            {},
            io.BytesIO(b'{"message": "rejected by manager"}'),
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(_ManagerApiError) as exc_info:
        _http_json(
            "GET",
            "http://127.0.0.1:4091/v1/upgrades/preview?version=3.1.0",
            token="secret",
        )
    assert exc_info.value.status == 409
    assert exc_info.value.detail == "rejected by manager"


def test_http_json_connection_error_becomes_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request: urllib.request.Request, timeout: object) -> object:
        raise urllib.error.URLError(ConnectionRefusedError("refused"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(_OrchestratorUnavailable, match="unreachable"):
        _http_json("GET", "http://127.0.0.1:4091/v1/health", token="secret")


def test_http_json_direct_connection_reset_becomes_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manager replacement may reset a live response outside URLError."""

    def fake_urlopen(request: urllib.request.Request, timeout: object) -> object:
        raise ConnectionResetError(104, "peer reset during manager switch")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(_OrchestratorUnavailable, match="unreachable"):
        _http_json("GET", "http://127.0.0.1:4091/v1/health", token="secret")


def test_http_json_rejects_non_object_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ListResponse:
        status = 200

        def __enter__(self) -> "_ListResponse":
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

        def read(self) -> bytes:
            return b"[1, 2]"

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout: _ListResponse()
    )
    with pytest.raises(_OrchestratorUnavailable, match="non-object"):
        _http_json("GET", "http://127.0.0.1:4091/v1/health", token="secret")


# ---------------------------------------------------------------------------
# _docker seam
# ---------------------------------------------------------------------------


def test_docker_returns_completed_process(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        captured["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _docker("docker", "info", "--format", "{{.ServerVersion}}")
    assert result.returncode == 0
    assert result.stdout == "ok"
    assert captured["argv"] == ["docker", "info", "--format", "{{.ServerVersion}}"]
    assert captured["argv"] is not None


def test_docker_raises_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(_OrchestratorUnavailable, match="boom"):
        _docker("docker", "fail")


def test_docker_raises_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        raise subprocess.TimeoutExpired(argv, 60)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(_OrchestratorUnavailable, match="docker command failed"):
        _docker("docker", "hang")


def test_docker_available_returns_false_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TimeoutExpired is a SubprocessError, not an OSError — it must still
    report Docker as unavailable instead of leaking."""

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        raise subprocess.TimeoutExpired(argv, 15)

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _docker_available() is False


def test_docker_available_returns_false_when_docker_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert _docker_available() is False


# ---------------------------------------------------------------------------
# _ManagerApiClient
# ---------------------------------------------------------------------------


def test_manager_api_client_builds_urls_and_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.build.linux_upgrade_orchestrator as orch_module

    calls: list[tuple[object, ...]] = []

    def fake_http_json(
        method: str, url: str, *, token: str, body: dict | None = None, timeout: float = 5.0
    ) -> tuple[int, dict]:
        calls.append((method, url, token, body))
        return 200, {"ok": True}

    monkeypatch.setattr(orch_module, "_http_json", fake_http_json)
    client = _ManagerApiClient(base_url="http://127.0.0.1:4091/")
    client.token = "secret"

    client.health()
    assert calls[0] == (
        "GET",
        "http://127.0.0.1:4091/v1/health",
        "secret",
        None,
    )

    client.preview("3.1.0")
    assert calls[1][1] == (
        "http://127.0.0.1:4091/v1/upgrades/preview?version=3.1.0"
    )

    client.confirm("3.1.0", "tok-123")
    assert calls[2][1] == "http://127.0.0.1:4091/v1/upgrades/confirm"
    assert calls[2][3] == {"version": "3.1.0", "confirmation_token": "tok-123"}

    client.status()
    assert calls[3][1] == "http://127.0.0.1:4091/v1/upgrades/status"


def test_manager_api_client_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.build.linux_upgrade_orchestrator as orch_module

    monkeypatch.setattr(
        orch_module,
        "_http_json",
        lambda method, url, *, token, body=None, timeout=5.0: (200, {}),
    )
    client = _ManagerApiClient()
    with pytest.raises(_OrchestratorUnavailable, match="token"):
        client.health()


# ---------------------------------------------------------------------------
# Orchestrator initialisation
# ---------------------------------------------------------------------------


def test_orchestrator_stores_public_versions(tmp_path: Path) -> None:
    orch = _LinuxUpgradeOrchestrator(
        source_bundle=tmp_path / "src.zip",
        source_version="3.0.0rc19",
        target_bundle=tmp_path / "tgt.zip",
        target_version="3.1.0",
        work_dir=tmp_path,
    )
    assert orch.source_version == "3.0.0rc19"
    assert orch.target_version == "3.1.0"


def test_trigger_upgrade_tolerates_confirm_connection_loss(tmp_path: Path) -> None:
    """An accepted confirm may lose its response while Manager is replaced."""

    class SwitchingApi:
        def preview(self, version: str) -> dict:
            assert version == "3.1.0"
            return {"preview": {"confirmation_token": "tok-123"}}

        def confirm(self, version: str, confirmation_token: str) -> dict:
            assert (version, confirmation_token) == ("3.1.0", "tok-123")
            raise _OrchestratorUnavailable("Manager API is unreachable: reset")

    orch = _LinuxUpgradeOrchestrator(
        source_bundle=tmp_path / "src.zip",
        source_version="3.0.0rc19",
        target_bundle=tmp_path / "tgt.zip",
        target_version="3.1.0",
        work_dir=tmp_path,
    )
    orch._api = SwitchingApi()  # type: ignore[assignment]

    orch._trigger_upgrade("healthy_commit")


def test_orchestrator_generates_unique_compose_project(tmp_path: Path) -> None:
    orch = _LinuxUpgradeOrchestrator(
        source_bundle=tmp_path / "src.zip",
        source_version="3.0.0rc19",
        target_bundle=tmp_path / "tgt.zip",
        target_version="3.1.0",
        work_dir=tmp_path,
    )
    assert orch._compose_project.startswith("dicepp-upgrade-")
    assert orch._compose_project != "dicepp-upgrade-"
    # Different instance → different project names (pids may clash in tests
    # but the pattern must be stable).
    assert "-" in orch._compose_project


def test_orchestrator_builds_image_tag_with_v_prefix(tmp_path: Path) -> None:
    orch = _LinuxUpgradeOrchestrator(
        source_bundle=tmp_path / "src.zip",
        source_version="3.0.0rc19",
        target_bundle=tmp_path / "tgt.zip",
        target_version="3.1.0",
        work_dir=tmp_path,
    )
    assert orch._image_tag == "v3.0.0rc19"


def test_cleanup_removes_only_exact_compose_project_containers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _LinuxUpgradeOrchestrator(
        source_bundle=tmp_path / "src.zip",
        source_version="3.0.0rc20",
        target_bundle=tmp_path / "tgt.zip",
        target_version="3.0.0rc21",
        work_dir=tmp_path,
    )
    calls: list[tuple[str, ...]] = []

    def docker_cmd(*args: str, **_kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:2] == ("ps", "-aq"):
            stdout = "container-b\ncontainer-a\n"
        elif args[0] == "inspect":
            stdout = orch._compose_project + "\n"
        else:
            stdout = ""
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(orch, "_docker_cmd", docker_cmd)

    orch._cleanup_compose_project_containers()

    for container_id in ("container-a", "container-b"):
        assert (
            "inspect",
            "--format",
            '{{index .Config.Labels "com.docker.compose.project"}}',
            container_id,
        ) in calls
        assert ("rm", "-f", container_id) in calls


def test_cleanup_rechecks_owned_image_users_after_handoff_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _LinuxUpgradeOrchestrator(
        source_bundle=tmp_path / "src.zip",
        source_version="3.0.0rc20",
        target_bundle=tmp_path / "tgt.zip",
        target_version="3.0.0rc21",
        work_dir=tmp_path,
    )
    orch._transaction_ids.add("tx-owned")
    calls: list[tuple[str, ...]] = []

    def docker_cmd(*args: str, **_kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:2] == ("ps", "-aq"):
            stdout = "compose-owned\ntransaction-owned\n"
        elif args[-1] == "compose-owned":
            stdout = json.dumps({"com.docker.compose.project": orch._compose_project})
        elif args[-1] == "transaction-owned":
            stdout = json.dumps({"io.dicepp.upgrade-transaction": "tx-owned"})
        else:
            stdout = ""
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(orch, "_docker_cmd", docker_cmd)

    orch._cleanup_owned_image_containers("example.invalid/dicepp:v3.0.0rc20")

    assert ("rm", "-f", "compose-owned") in calls
    assert ("rm", "-f", "transaction-owned") in calls


def test_cleanup_refuses_unowned_image_user(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _LinuxUpgradeOrchestrator(
        source_bundle=tmp_path / "src.zip",
        source_version="3.0.0rc20",
        target_bundle=tmp_path / "tgt.zip",
        target_version="3.0.0rc21",
        work_dir=tmp_path,
    )

    def docker_cmd(*args: str, **_kwargs) -> subprocess.CompletedProcess[str]:
        stdout = "unowned\n" if args[:2] == ("ps", "-aq") else "{}"
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(orch, "_docker_cmd", docker_cmd)

    with pytest.raises(_OrchestratorUnavailable, match="outside the isolated project"):
        orch._cleanup_owned_image_containers("example.invalid/dicepp:v3.0.0rc20")


def test_cleanup_discovers_transaction_from_bound_isolated_request(
    tmp_path: Path,
) -> None:
    orch = _LinuxUpgradeOrchestrator(
        source_bundle=tmp_path / "src.zip",
        source_version="3.0.0rc20",
        target_bundle=tmp_path / "tgt.zip",
        target_version="3.0.0rc21",
        work_dir=tmp_path,
    )
    orch._instance_dir = tmp_path / "instance"
    orch._source_image_ids = {"bot": "source-bot", "dashboard": "source-dashboard"}
    orch._target_image_ids = {"bot": "target-bot", "dashboard": "target-dashboard"}
    transaction_id = "a" * 32
    request_dir = orch._instance_dir / "manager" / "recovery" / transaction_id
    request_dir.mkdir(parents=True)
    request = {
        "transaction_id": transaction_id,
        "operation_id": "b" * 32,
        "compose_project": orch._compose_project,
        "source_version": orch.source_version,
        "target_version": orch.target_version,
        "manager": {"image_id": "source-dashboard"},
        "target_manager_image_id": "target-dashboard",
        "bot": {"image_id": "source-bot"},
        "dashboard": {"image_id": "source-dashboard"},
        "target_images": {"bot": "target-bot", "dashboard": "target-dashboard"},
    }
    (request_dir / "linux-manager-switch.request.json").write_text(
        json.dumps(request), encoding="utf-8"
    )

    orch._discover_cleanup_transaction_ids()

    assert orch._transaction_ids == {transaction_id}


def test_cleanup_refuses_foreign_persisted_handoff_request(tmp_path: Path) -> None:
    orch = _LinuxUpgradeOrchestrator(
        source_bundle=tmp_path / "src.zip",
        source_version="3.0.0rc20",
        target_bundle=tmp_path / "tgt.zip",
        target_version="3.0.0rc21",
        work_dir=tmp_path,
    )
    orch._instance_dir = tmp_path / "instance"
    transaction_id = "a" * 32
    request_dir = orch._instance_dir / "manager" / "recovery" / transaction_id
    request_dir.mkdir(parents=True)
    (request_dir / "linux-manager-switch.request.json").write_text(
        json.dumps(
            {
                "transaction_id": transaction_id,
                "operation_id": "b" * 32,
                "compose_project": "another-project",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(_OrchestratorUnavailable, match="not bound"):
        orch._discover_cleanup_transaction_ids()

    assert orch._transaction_ids == set()


# ---------------------------------------------------------------------------
# _read_journal
# ---------------------------------------------------------------------------


def test_read_journal_returns_empty_when_no_instance_dir(tmp_path: Path) -> None:
    orch = _LinuxUpgradeOrchestrator(
        source_bundle=tmp_path / "src.zip",
        source_version="3.0.0rc19",
        target_bundle=tmp_path / "tgt.zip",
        target_version="3.1.0",
        work_dir=tmp_path,
    )
    assert orch._read_journal() == {}


def test_read_journal_returns_unavailable_when_db_missing(tmp_path: Path) -> None:
    orch = _LinuxUpgradeOrchestrator(
        source_bundle=tmp_path / "src.zip",
        source_version="3.0.0rc19",
        target_bundle=tmp_path / "tgt.zip",
        target_version="3.1.0",
        work_dir=tmp_path,
    )
    orch._instance_dir = tmp_path
    assert orch._read_journal() == {"status": "unavailable"}


def test_read_journal_returns_unavailable_for_corrupt_db(tmp_path: Path) -> None:
    orch = _LinuxUpgradeOrchestrator(
        source_bundle=tmp_path / "src.zip",
        source_version="3.0.0rc19",
        target_bundle=tmp_path / "tgt.zip",
        target_version="3.1.0",
        work_dir=tmp_path,
    )
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    db_dir = instance_dir / "manager" / "state"
    db_dir.mkdir(parents=True)
    (db_dir / "manager.db").write_bytes(b"not a sqlite database")
    orch._instance_dir = instance_dir
    result = orch._read_journal()
    assert result["status"] == "unavailable"
    assert "error" in result


def test_read_journal_works_with_real_manager_schema(tmp_path: Path) -> None:
    """The journal query must match the real Manager schema (``updated_at``)."""
    orch = _LinuxUpgradeOrchestrator(
        source_bundle=tmp_path / "src.zip",
        source_version="3.0.0rc19",
        target_bundle=tmp_path / "tgt.zip",
        target_version="3.1.0",
        work_dir=tmp_path,
    )
    instance_dir = tmp_path / "instance"
    write_manager_journal(
        instance_dir / "manager" / "state" / "manager.db",
        [
            {
                "transaction_id": "tx-1",
                "kind": "upgrade",
                "phase": "apply",
                "status": "applying",
                "updated_at": "2026-08-04T01:00:00Z",
            },
            {
                "transaction_id": "tx-2",
                "kind": "upgrade",
                "phase": "commit",
                "status": "committed",
                "updated_at": "2026-08-04T02:00:00Z",
            },
        ],
    )
    orch._instance_dir = instance_dir
    # The real schema has no created_at column, and the latest updated_at row
    # wins the ordering — a created_at-based query would fail closed here.
    assert orch._read_journal() == {
        "transaction_id": "tx-2",
        "operation_id": None,
        "status": "committed",
        "phase": "commit",
        "detail": {},
    }


# ---------------------------------------------------------------------------
# Release state seeding
# ---------------------------------------------------------------------------


def test_prepare_compose_seeds_valid_release_state(tmp_path: Path) -> None:
    """The seeded release-state passes the Manager's own cached-latest check."""
    from dicepp_manager.release import _validate_cached_latest

    source_bundle = write_linux_bundle(
        tmp_path / "source.zip",
        version="3.0.0rc19",
        compose="services:\n  manager:\n    image: test:${DICEPP_IMAGE_TAG:-latest}\n  bot:\n    image: test:${DICEPP_IMAGE_TAG:-latest}\n",
    )
    target_bundle = write_linux_bundle(
        tmp_path / "target.zip",
        version="3.1.0",
        compose="services:\n  manager:\n    image: test:${DICEPP_IMAGE_TAG:-latest}\n  bot:\n    image: test:${DICEPP_IMAGE_TAG:-latest}\n",
    )
    orch = _LinuxUpgradeOrchestrator(
        source_bundle=source_bundle,
        source_version="3.0.0rc19",
        target_bundle=target_bundle,
        target_version="3.1.0",
        work_dir=tmp_path / "work",
    )
    orch._prepare_compose()

    state_path = orch._instance_dir / "manager" / "state" / "release-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["format_version"] == 1
    assert state["channel"] == "prerelease"
    available = state["available"]
    assert available["version"] == "3.1.0"
    assert available["channel"] == "prerelease"
    assert available["compatible"] is True
    assert available["compatibility"]["automatic_upgrade"] is True
    assert available["artifacts"][0]["purpose"] == "linux-bundle"

    # The exact schema the Manager validates on startup/status.
    _validate_cached_latest(
        available,
        channel="prerelease",
        current_version="3.0.0rc19",
        target=("linux", "amd64"),
    )


def test_prepare_compose_derives_non_promotable_bundle_for_manual_policy(
    tmp_path: Path,
) -> None:
    compose = (
        "services:\n"
        "  manager:\n"
        "    image: test:${DICEPP_IMAGE_TAG:-latest}\n"
        "  bot:\n"
        "    image: test:${DICEPP_IMAGE_TAG:-latest}\n"
    )
    source_bundle = write_linux_bundle(
        tmp_path / "source.zip",
        version="3.0.0rc20",
        compose=compose,
    )
    target_bundle = write_linux_bundle(
        tmp_path / "target.zip",
        version="3.0.0rc21",
        compose=compose,
        automatic_upgrade=False,
        archive_member=b"final-image-bytes",
        include_checksums=True,
    )
    original_digest = _sha256_file(target_bundle)
    orch = _LinuxUpgradeOrchestrator(
        source_bundle=source_bundle,
        source_version="3.0.0rc20",
        target_bundle=target_bundle,
        target_version="3.0.0rc21",
        work_dir=tmp_path / "work",
    )

    orch._prepare_compose()

    assert _sha256_file(target_bundle) == original_digest
    assert _read_bundle_manifest(target_bundle)["automatic_upgrade"] is False
    assert "linux_manager_handoff_protocol" not in _read_bundle_manifest(target_bundle)
    seeded = orch._seeded_bundle_path
    assert seeded is not None
    seeded_manifest = _read_bundle_manifest(seeded)
    assert seeded_manifest["automatic_upgrade"] is True
    assert seeded_manifest["linux_manager_handoff_protocol"] == 1
    final_image = read_bundle_member(target_bundle, "images/test.tar.zst")
    validation_image = read_bundle_member(seeded, "images/test.tar.zst")
    assert validation_image == final_image == b"final-image-bytes"
    final_checksums = read_bundle_member(target_bundle, "checksums.sha256")
    validation_checksums = read_bundle_member(seeded, "checksums.sha256")
    assert final_checksums != validation_checksums
    seeded_manifest_bytes = read_bundle_member(seeded, "dicepp-package.json")
    assert (
        f"{hashlib.sha256(seeded_manifest_bytes).hexdigest()}  dicepp-package.json"
        in validation_checksums.decode("utf-8").splitlines()
    )
    state = json.loads(
        (orch._instance_dir / "manager/state/release-state.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["available"]["compatibility"]["automatic_upgrade"] is True


def test_prepare_compose_seeds_verified_release_metadata(tmp_path: Path) -> None:
    target_bundle = write_linux_bundle(
        tmp_path / "target.zip",
        version="3.1.0",
        compose="services:\n  manager:\n    image: test:${DICEPP_IMAGE_TAG:-latest}\n  bot:\n    image: test:${DICEPP_IMAGE_TAG:-latest}\n",
    )
    source_bundle = write_linux_bundle(
        tmp_path / "source.zip",
        version="3.0.0rc19",
        compose="services:\n  manager:\n    image: test:${DICEPP_IMAGE_TAG:-latest}\n  bot:\n    image: test:${DICEPP_IMAGE_TAG:-latest}\n",
    )
    orch = _LinuxUpgradeOrchestrator(
        source_bundle=source_bundle,
        source_version="3.0.0rc19",
        target_bundle=target_bundle,
        target_version="3.1.0",
        work_dir=tmp_path / "work",
    )
    orch._prepare_compose()

    packages_dir = orch._instance_dir / "manager" / "packages" / "3.1.0"
    verified = json.loads(
        (packages_dir / "verified-release.json").read_text(encoding="utf-8")
    )
    assert verified["version"] == "3.1.0"
    assert verified["verified_path"] == "target.zip"
    assert verified["artifact"]["size"] == target_bundle.stat().st_size
    # The seeded copy is byte-identical to the target bundle.
    seeded = packages_dir / "target.zip"
    assert seeded.read_bytes() == target_bundle.read_bytes()
    # Image identities were read from both bundle manifests.
    assert orch._source_image_ids["bot"].startswith("sha256:")
    assert orch._target_image_ids["bot"].startswith("sha256:")
    assert orch._target_image_ids["dashboard"] != orch._target_image_ids["bot"]
