"""Scenario-sequence tests for the Linux upgrade orchestrator.

Mock the ``_http_json`` and ``_docker`` module-level seams to exercise the
full four-scenario flows (preview → confirm → status polling, health-failure
injection, corrupt-bundle rejection) without Docker or a live Manager.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

import scripts.build.linux_upgrade_orchestrator as orch_module
from scripts.build.linux_upgrade_orchestrator import (
    SCENARIO_ASSERTIONS,
    SCENARIO_OBSERVATION_FIELDS,
    _LinuxUpgradeOrchestrator,
    _ManagerApiClient,
    _ManagerApiError,
    _OrchestratorUnavailable,
    _ScenarioExpectationFailure,
)
from tests.support.linux_bundle import write_linux_bundle

SOURCE_VERSION = "3.0.0rc19"
TARGET_VERSION = "3.1.0"
SOURCE_BOT_ID = "sha256:" + "a" * 64
SOURCE_DASH_ID = "sha256:" + "b" * 64
TARGET_BOT_ID = "sha256:" + "c" * 64
TARGET_DASH_ID = "sha256:" + "d" * 64

COMPOSE = (
    "services:\n"
    "  manager:\n"
    "    image: test:${DICEPP_IMAGE_TAG:-latest}\n"
    "  bot:\n"
    "    image: test:${DICEPP_IMAGE_TAG:-latest}\n"
)

BOT_NAME = "dicepp-bot"
DASHBOARD_NAME = "dicepp-dashboard"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeDocker:
    """Records Docker CLI calls; ``inspect`` reports the bot image id."""

    def __init__(self, bot_image_id: str = SOURCE_BOT_ID) -> None:
        self.calls: list[tuple[list[str], dict]] = []
        self.stopped: list[str] = []
        self.bot_image_id = bot_image_id
        self.dashboard_image_id = SOURCE_DASH_ID

    def switch_to_target(self) -> None:
        self.bot_image_id = TARGET_BOT_ID
        self.dashboard_image_id = TARGET_DASH_ID

    def restore_source(self) -> None:
        self.bot_image_id = SOURCE_BOT_ID
        self.dashboard_image_id = SOURCE_DASH_ID

    def __call__(
        self,
        *args: str,
        timeout: float = 60,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        argv = list(args)
        self.calls.append((argv, {"cwd": cwd, "env": env}))
        if "network" in argv and "create" in argv:
            return subprocess.CompletedProcess(
                argv, 0, stdout=("e" * 64) + "\n", stderr=""
            )
        if "ps" in argv:
            if any("service=dashboard" in value for value in argv):
                name = DASHBOARD_NAME
            else:
                name = BOT_NAME
            return subprocess.CompletedProcess(
                argv, 0, stdout=name + "\n", stderr=""
            )
        if "inspect" in argv:
            name = argv[-1]
            image_id = (
                self.dashboard_image_id
                if name == DASHBOARD_NAME
                else self.bot_image_id
            )
            if "{{json .}}" in argv:
                stdout = json.dumps(
                    {
                        "Image": image_id,
                        "State": {
                            "Running": True,
                            "Status": "running",
                            "StartedAt": "2026-01-01T00:00:00Z",
                        },
                    }
                )
            else:
                stdout = image_id
            return subprocess.CompletedProcess(
                argv, 0, stdout=stdout + "\n", stderr=""
            )
        if "stop" in argv:
            self.stopped.append(argv[-1])
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


class _FakeManagerApi:
    """Serves the Manager API surface against recorded expectations."""

    def __init__(
        self,
        *,
        health_version: str = SOURCE_VERSION,
        confirm_error: _ManagerApiError | None = None,
        on_confirm=None,
    ) -> None:
        self.calls: list[tuple[str, str, str | None, dict | None]] = []
        self.health_version = health_version
        self.status_responses: list[dict] = []
        self.confirm_error = confirm_error
        self.on_confirm = on_confirm
        self.on_rollback = None
        self.token = ""

    def __call__(
        self,
        method: str,
        url: str,
        *,
        token: str,
        body: dict | None = None,
        timeout: float = 5.0,
    ) -> tuple[int, dict]:
        self.calls.append((method, url, token, body))
        self.token = token
        if url.endswith("/v1/health"):
            return 200, {"ok": True, "dicepp_version": self.health_version}
        if "/v1/upgrades/preview" in url:
            return 200, {
                "ok": True,
                "preview": {
                    "confirmation_token": "tok-123",
                    "version": TARGET_VERSION,
                },
            }
        if url.endswith("/v1/upgrades/confirm"):
            if self.confirm_error is not None:
                raise self.confirm_error
            if self.on_confirm is not None:
                self.on_confirm()
            return 202, {
                "ok": True,
                "operation": {"status": "running", "action": "upgrade.install"},
            }
        if url.endswith("/v1/upgrades/status"):
            if self.status_responses:
                response = self.status_responses.pop(0)
                if (
                    response.get("last_operation", {}).get("detail", {}).get(
                        "rolled_back"
                    )
                    is True
                    and self.on_rollback is not None
                ):
                    self.on_rollback()
                return 200, response
            return 200, {
                "ok": True,
                "active_operation": None,
                "last_operation": {"status": "succeeded", "detail": {}},
            }
        raise AssertionError(f"unexpected API call: {method} {url}")


def _running_status() -> dict:
    return {
        "ok": True,
        "active_operation": {"status": "running"},
        "last_operation": {"status": "running", "detail": {}},
    }


def _succeeded_status() -> dict:
    return {
        "ok": True,
        "active_operation": None,
        "last_operation": {
            "status": "succeeded",
            "message": "upgrade committed",
            "detail": {"rolled_back": False},
        },
    }


def _rolled_back_status() -> dict:
    return {
        "ok": True,
        "active_operation": None,
        "last_operation": {
            "status": "failed",
            "message": "upgrade rolled back",
            "detail": {"rolled_back": True, "rollback_status": "succeeded"},
        },
    }


def _rolled_back_journal() -> dict:
    return {
        "status": "rolled_back",
        "phase": "rolled_back",
        "detail": {
            "rollback_result": {
                "succeeded": True,
                "program_restored": True,
                "data_restored": True,
                "program": {"status": "restored", "roles": ["bot", "dashboard"]},
            }
        },
    }


class _InfiniteRunning:
    """A status sequence that always reports a running operation."""

    def __bool__(self) -> bool:
        return True

    def pop(self, _index: int = 0) -> dict:
        return _running_status()


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise the orchestrator's polling sleeps for fast tests."""
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)


def _make_orchestrator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    fake_api: _FakeManagerApi,
    fake_docker: _FakeDocker,
) -> _LinuxUpgradeOrchestrator:
    work = tmp_path / "work"
    token_dir = work / "instance" / "manager" / "state"
    token_dir.mkdir(parents=True)
    (token_dir / "api-token").write_text("test-token\n", encoding="utf-8")

    source_bundle = write_linux_bundle(
        tmp_path / "source.zip", version=SOURCE_VERSION, compose=COMPOSE
    )
    target_bundle = write_linux_bundle(
        tmp_path / "target.zip",
        version=TARGET_VERSION,
        compose=COMPOSE,
        bot_image_id=TARGET_BOT_ID,
        dashboard_image_id=TARGET_DASH_ID,
    )
    monkeypatch.setattr(orch_module, "_http_json", fake_api)
    monkeypatch.setattr(orch_module, "_docker", fake_docker)
    monkeypatch.setattr(
        orch_module, "_docker_object_exists", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(
        orch_module, "_optional_docker_image_id", lambda _ref, **_kwargs: None
    )
    monkeypatch.setattr(orch_module._DockerSocketProxy, "start", lambda _self: None)
    monkeypatch.setattr(orch_module._DockerSocketProxy, "stop", lambda _self: None)
    monkeypatch.setattr(
        orch_module,
        "_load_docker_image_from_bundle",
        lambda bundle, work_dir, label, **_kwargs: (
            {"bot": SOURCE_BOT_ID, "dashboard": SOURCE_DASH_ID}
            if label == "source"
            else {"bot": TARGET_BOT_ID, "dashboard": TARGET_DASH_ID}
        ),
    )
    orch = _LinuxUpgradeOrchestrator(
        source_bundle=source_bundle,
        source_version=SOURCE_VERSION,
        target_bundle=target_bundle,
        target_version=TARGET_VERSION,
        work_dir=work,
    )

    def restore_source_and_sentinels() -> None:
        fake_docker.restore_source()
        if orch._instance_dir is None:
            return
        for relative, payload in orch._sentinel_original_bytes.items():
            (orch._instance_dir / relative).write_bytes(payload)

    fake_api.on_rollback = restore_source_and_sentinels
    return orch


# ---------------------------------------------------------------------------
# healthy_commit
# ---------------------------------------------------------------------------


def test_healthy_commit_full_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
) -> None:
    fake_api = _FakeManagerApi()
    fake_api.status_responses = [_running_status(), _succeeded_status()]
    fake_docker = _FakeDocker()

    def on_confirm() -> None:
        fake_docker.switch_to_target()

    fake_api.on_confirm = on_confirm
    orch = _make_orchestrator(tmp_path, monkeypatch, fake_api=fake_api, fake_docker=fake_docker)
    monkeypatch.setattr(orch, "_read_journal", lambda: {"status": "committed"})

    result = orch.healthy_commit()

    assert result["status"] == "passed"
    assert result["scenario"] == "healthy_commit"
    assert result["assertions"] == {
        key: True for key in SCENARIO_ASSERTIONS["healthy_commit"]
    }
    assert result["observations"] == {
        "source_version_before": SOURCE_VERSION,
        "target_version_after": TARGET_VERSION,
        "journal_status": "committed",
        "health_status": "healthy",
    }

    # compose up pinned the source image tag for every service.
    compose_calls = [
        call for call in fake_docker.calls if "compose" in call[0] and "up" in call[0]
    ]
    assert compose_calls
    assert compose_calls[0][1]["env"]["DICEPP_IMAGE_TAG"] == "v3.0.0rc19"

    # preview then confirm with the returned token.
    confirm_calls = [call for call in fake_api.calls if "confirm" in call[1]]
    assert confirm_calls
    assert confirm_calls[0][3] == {
        "version": TARGET_VERSION,
        "confirmation_token": "tok-123",
    }
    assert fake_api.token == "test-token"


def test_healthy_commit_fails_when_journal_not_committed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
) -> None:
    fake_api = _FakeManagerApi()
    fake_api.status_responses = [_running_status(), _succeeded_status()]
    fake_docker = _FakeDocker()
    fake_api.on_confirm = fake_docker.switch_to_target
    orch = _make_orchestrator(tmp_path, monkeypatch, fake_api=fake_api, fake_docker=fake_docker)
    monkeypatch.setattr(orch, "_read_journal", lambda: {"status": "interrupted"})

    result = orch.healthy_commit()

    assert result["status"] == "failed"
    assert result["assertions"] == {
        key: False for key in SCENARIO_ASSERTIONS["healthy_commit"]
    }
    assert "journal" in result["observations"]["reason"]


# ---------------------------------------------------------------------------
# target_health_failure_rollback
# ---------------------------------------------------------------------------


def test_target_health_failure_injects_docker_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
) -> None:
    fake_api = _FakeManagerApi()
    fake_api.status_responses = [_running_status(), _rolled_back_status()]
    fake_docker = _FakeDocker()
    fake_api.on_confirm = fake_docker.switch_to_target
    orch = _make_orchestrator(tmp_path, monkeypatch, fake_api=fake_api, fake_docker=fake_docker)
    monkeypatch.setattr(orch, "_read_journal", _rolled_back_journal)

    result = orch.target_health_failure_rollback()

    assert result["status"] == "passed"
    assert result["assertions"] == {
        key: True
        for key in SCENARIO_ASSERTIONS["target_health_failure_rollback"]
    }
    assert result["observations"] == {
        "target_version_observed": TARGET_VERSION,
        "restored_version": SOURCE_VERSION,
        "journal_status": "rolled_back",
        "rollback_marker_status": "restored",
    }
    # The injected failure is a docker stop on the managed bot container.
    assert fake_docker.stopped == [BOT_NAME]


def test_target_health_failure_rollback_fails_on_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
) -> None:
    fake_api = _FakeManagerApi()
    fake_api.status_responses = [_running_status(), _succeeded_status()]
    fake_docker = _FakeDocker()
    fake_api.on_confirm = fake_docker.switch_to_target
    orch = _make_orchestrator(tmp_path, monkeypatch, fake_api=fake_api, fake_docker=fake_docker)
    monkeypatch.setattr(orch, "_read_journal", _rolled_back_journal)

    result = orch.target_health_failure_rollback()

    assert result["status"] == "failed"
    assert "expected a rollback" in result["observations"]["reason"]


def test_target_health_failure_fails_when_catalog_sentinels_are_not_restored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
) -> None:
    fake_api = _FakeManagerApi()
    fake_api.status_responses = [_running_status(), _rolled_back_status()]
    fake_docker = _FakeDocker()
    fake_api.on_confirm = fake_docker.switch_to_target
    orch = _make_orchestrator(
        tmp_path,
        monkeypatch,
        fake_api=fake_api,
        fake_docker=fake_docker,
    )
    # Program restoration alone is insufficient: deliberately leave both
    # catalog-owned sentinels in their post-archive damaged state.
    fake_api.on_rollback = fake_docker.restore_source
    monkeypatch.setattr(orch, "_read_journal", _rolled_back_journal)

    result = orch.target_health_failure_rollback()

    assert orch._sentinels_mutated is True
    assert result["status"] == "failed"
    assert "sentinel" in result["observations"]["reason"]


# ---------------------------------------------------------------------------
# retry_after_rollback
# ---------------------------------------------------------------------------


def test_retry_after_rollback_full_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
) -> None:
    fake_api = _FakeManagerApi()
    fake_api.status_responses = [
        _running_status(),
        _rolled_back_status(),
        _running_status(),
        _succeeded_status(),
    ]
    fake_docker = _FakeDocker()
    fake_api.on_confirm = fake_docker.switch_to_target
    orch = _make_orchestrator(tmp_path, monkeypatch, fake_api=fake_api, fake_docker=fake_docker)
    journals = iter([_rolled_back_journal(), {"status": "committed"}])
    monkeypatch.setattr(orch, "_read_journal", lambda: next(journals))

    result = orch.retry_after_rollback()

    assert result["status"] == "passed"
    assert result["assertions"] == {
        key: True for key in SCENARIO_ASSERTIONS["retry_after_rollback"]
    }
    assert result["observations"] == {
        "first_transaction_status": "rolled_back",
        "retry_transaction_status": "committed",
        "final_version": TARGET_VERSION,
    }
    # Two upgrades were triggered on the same instance.
    confirm_calls = [call for call in fake_api.calls if "confirm" in call[1]]
    assert len(confirm_calls) == 2
    assert fake_docker.stopped == [BOT_NAME]


# ---------------------------------------------------------------------------
# apply_failure_before_target_execution
# ---------------------------------------------------------------------------


def test_apply_failure_is_injected_after_transaction_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
) -> None:
    fake_api = _FakeManagerApi()
    fake_api.status_responses = [_running_status(), _rolled_back_status()]
    fake_docker = _FakeDocker()  # bot never switches to the target image
    orch = _make_orchestrator(tmp_path, monkeypatch, fake_api=fake_api, fake_docker=fake_docker)
    assert orch._docker_proxy is None

    original_prepare = orch._prepare_compose

    def prepare() -> None:
        original_prepare()
        assert orch._docker_proxy is not None

        def inject() -> None:
            assert orch._docker_proxy is not None
            assert orch._docker_proxy._on_create_failure is not None
            orch._docker_proxy._on_create_failure()
            orch._docker_proxy.failure_count = 1
            orch._docker_proxy.failure_status = 500

        monkeypatch.setattr(
            orch._docker_proxy,
            "arm_container_create_failure",
            inject,
        )

    monkeypatch.setattr(orch, "_prepare_compose", prepare)
    monkeypatch.setattr(orch, "_read_journal", _rolled_back_journal)

    result = orch.apply_failure_before_target_execution()

    assert result["status"] == "passed"
    assert result["assertions"] == {
        key: True
        for key in SCENARIO_ASSERTIONS["apply_failure_before_target_execution"]
    }
    assert result["observations"] == {
        "target_process_start_count": 0,
        "source_version_after": SOURCE_VERSION,
        "journal_status": "rolled_back",
        "apply_exit_code": 500,
    }
    assert any("confirm" in call[1] for call in fake_api.calls)
    assert any("status" in call[1] for call in fake_api.calls)


def test_apply_failure_fails_when_confirm_unexpectedly_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
) -> None:
    fake_api = _FakeManagerApi()
    fake_api.status_responses = [_succeeded_status()]
    fake_docker = _FakeDocker()
    orch = _make_orchestrator(tmp_path, monkeypatch, fake_api=fake_api, fake_docker=fake_docker)

    result = orch.apply_failure_before_target_execution()

    assert result["status"] == "failed"
    assert "expected Docker apply failure" in result["observations"]["reason"]


def test_apply_failure_fails_on_unexpected_confirm_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
) -> None:
    fake_api = _FakeManagerApi(confirm_error=_ManagerApiError(400, "bad request"))
    fake_docker = _FakeDocker()
    orch = _make_orchestrator(tmp_path, monkeypatch, fake_api=fake_api, fake_docker=fake_docker)

    result = orch.apply_failure_before_target_execution()

    assert result["status"] == "failed"
    assert "upgrade trigger was rejected" in result["observations"]["reason"]


# ---------------------------------------------------------------------------
# Rollback / completion detection and timeouts
# ---------------------------------------------------------------------------


def test_wait_rollback_complete_detects_rolled_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
) -> None:
    fake_api = _FakeManagerApi()
    fake_api.status_responses = [_running_status(), _rolled_back_status()]
    fake_docker = _FakeDocker()
    orch = _make_orchestrator(tmp_path, monkeypatch, fake_api=fake_api, fake_docker=fake_docker)
    orch._api = _ManagerApiClient()
    orch._api.token = "test-token"

    orch._wait_rollback_complete("target_health_failure_rollback", timeout=30)


def test_wait_rollback_complete_rejects_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
) -> None:
    fake_api = _FakeManagerApi()
    fake_api.status_responses = [_succeeded_status()]
    fake_docker = _FakeDocker()
    orch = _make_orchestrator(tmp_path, monkeypatch, fake_api=fake_api, fake_docker=fake_docker)
    orch._api = _ManagerApiClient()
    orch._api.token = "test-token"

    with pytest.raises(_ScenarioExpectationFailure, match="expected a rollback"):
        orch._wait_rollback_complete("target_health_failure_rollback", timeout=30)


def test_wait_upgrade_complete_times_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
) -> None:
    fake_api = _FakeManagerApi()
    # Polling is unthrottled by no_sleep, so a finite response list could be
    # exhausted before the deadline; an infinite running sequence keeps the
    # loop polling until the timeout fires.
    fake_api.status_responses = _InfiniteRunning()
    fake_docker = _FakeDocker()
    orch = _make_orchestrator(tmp_path, monkeypatch, fake_api=fake_api, fake_docker=fake_docker)
    orch._api = _ManagerApiClient()
    orch._api.token = "test-token"

    with pytest.raises(_ScenarioExpectationFailure, match="did not complete"):
        orch._wait_upgrade_complete("healthy_commit", timeout=0.2)


def test_wait_health_times_out_when_api_unreachable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
) -> None:
    def broken_api(
        method: str,
        url: str,
        *,
        token: str,
        body: dict | None = None,
        timeout: float = 5.0,
    ) -> tuple[int, dict]:
        raise _OrchestratorUnavailable("Manager API is unreachable: refused")

    monkeypatch.setattr(orch_module, "_http_json", broken_api)
    orch = _LinuxUpgradeOrchestrator(
        source_bundle=tmp_path / "src.zip",
        source_version=SOURCE_VERSION,
        target_bundle=tmp_path / "tgt.zip",
        target_version=TARGET_VERSION,
        work_dir=tmp_path / "work",
    )
    orch._api = _ManagerApiClient()
    orch._api.token = "test-token"

    with pytest.raises(_OrchestratorUnavailable, match="did not report"):
        orch._wait_health(SOURCE_VERSION, timeout=0.2)


def test_trigger_upgrade_rejected_preview_fails_scenario(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
) -> None:
    def rejecting_preview(
        method: str,
        url: str,
        *,
        token: str,
        body: dict | None = None,
        timeout: float = 5.0,
    ) -> tuple[int, dict]:
        if "preview" in url:
            raise _ManagerApiError(409, "candidate is not compatible")
        return 200, {"ok": True}

    monkeypatch.setattr(orch_module, "_http_json", rejecting_preview)
    monkeypatch.setattr(orch_module, "_docker", _FakeDocker())
    orch = _LinuxUpgradeOrchestrator(
        source_bundle=tmp_path / "src.zip",
        source_version=SOURCE_VERSION,
        target_bundle=tmp_path / "tgt.zip",
        target_version=TARGET_VERSION,
        work_dir=tmp_path / "work",
    )
    orch._api = _ManagerApiClient()
    orch._api.token = "test-token"

    with pytest.raises(_ScenarioExpectationFailure, match="rejected"):
        orch._trigger_upgrade("healthy_commit")
