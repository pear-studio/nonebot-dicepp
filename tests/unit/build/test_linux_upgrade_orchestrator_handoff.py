"""Direct fail-closed tests for Linux Manager handoff orchestration."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import scripts.build.linux_upgrade_orchestrator as module
from scripts.build.linux_upgrade_orchestrator import (
    SCENARIO_ASSERTIONS,
    SCENARIO_METHODS,
    _DEFAULT_DIND_IMAGE,
    _DockerDaemonSandbox,
    _LinuxUpgradeOrchestrator,
    _ManagerApiClient,
    _OrchestratorUnavailable,
    _ScenarioExpectationFailure,
    run_linux_scenario,
)


def _context(scenario: str) -> dict[str, object]:
    return {
        "platform": "linux",
        "arch": "amd64",
        "scenario": scenario,
        "source_version": "3.0.0rc19",
        "target_version": "3.0.0rc20",
        "source_assets": [{"purpose": "linux-bundle", "path": "/source.zip"}],
        "target_assets": [{"purpose": "linux-bundle", "path": "/target.zip"}],
    }


@pytest.mark.parametrize(
    ("scenario", "method"),
    [
        ("manager_handoff_commit", "linux_manager_handoff_success"),
        ("manager_handoff_rollback", "linux_manager_handoff_target_crash"),
        (
            "manager_handoff_commit_crash_window",
            "linux_manager_handoff_daemon_restart",
        ),
    ],
)
def test_handoff_entry_dispatch_is_stable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    method: str,
) -> None:
    called: list[str] = []

    class FakeSandbox:
        docker_env = {"DOCKER_HOST": "tcp://isolated"}

        def __init__(self, _work_dir: Path) -> None:
            pass

        def start(self) -> None:
            called.append("sandbox.start")

        def cleanup(self) -> None:
            called.append("sandbox.cleanup")

    class FakeOrchestrator:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def cleanup(self) -> None:
            called.append("orchestrator.cleanup")

        def __getattr__(self, name: str):
            if name != method:
                raise AttributeError(name)

            def execute() -> dict[str, object]:
                called.append(name)
                return {"status": "passed", "scenario": scenario}

            return execute

    monkeypatch.setattr(module, "_docker_available", lambda: True)
    monkeypatch.setattr(module, "_DockerDaemonSandbox", FakeSandbox)
    monkeypatch.setattr(module, "_LinuxUpgradeOrchestrator", FakeOrchestrator)

    result = run_linux_scenario(_context(scenario), tmp_path)

    assert SCENARIO_METHODS[scenario] == method
    assert result == {"status": "passed", "scenario": scenario}
    assert method in called
    assert called[-1] == (
        "sandbox.cleanup"
        if scenario == "manager_handoff_commit_crash_window"
        else "orchestrator.cleanup"
    )


def test_entry_cleanup_failure_is_reported_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeOrchestrator:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def linux_manager_handoff_success(self) -> dict[str, str]:
            return {"status": "passed"}

        def cleanup(self) -> None:
            raise _OrchestratorUnavailable("owned object could not be removed")

    monkeypatch.setattr(module, "_docker_available", lambda: True)
    monkeypatch.setattr(module, "_LinuxUpgradeOrchestrator", FakeOrchestrator)

    result = run_linux_scenario(_context("manager_handoff_commit"), tmp_path)

    assert result["status"] == "unavailable"
    assert "owned object could not be removed" in result["observations"]["reason"]


@pytest.mark.parametrize(
    "scenario",
    [
        "manager_handoff_commit",
        "manager_handoff_rollback",
        "manager_handoff_commit_crash_window",
    ],
)
def test_handoff_entry_prerequisite_failure_is_structured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
) -> None:
    method = SCENARIO_METHODS[scenario]

    class FakeSandbox:
        docker_env = {"DOCKER_HOST": "tcp://isolated"}

        def __init__(self, _work_dir: Path) -> None:
            pass

        def start(self) -> None:
            pass

        def cleanup(self) -> None:
            pass

    class FakeOrchestrator:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def cleanup(self) -> None:
            pass

        def __getattr__(self, name: str):
            if name != method:
                raise AttributeError(name)

            def execute() -> dict[str, object]:
                raise _OrchestratorUnavailable("candidate prerequisite missing")

            return execute

    monkeypatch.setattr(module, "_docker_available", lambda: True)
    monkeypatch.setattr(module, "_DockerDaemonSandbox", FakeSandbox)
    monkeypatch.setattr(module, "_LinuxUpgradeOrchestrator", FakeOrchestrator)

    result = run_linux_scenario(_context(scenario), tmp_path)

    assert result["status"] == "unavailable"
    assert result["scenario"] == scenario
    assert result["observations"]["reason"] == "candidate prerequisite missing"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "docker:28.2.2-dind",
        "example.invalid/docker@sha256:" + "a" * 64,
    ],
)
def test_dind_rejects_unpinned_or_non_official_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("DICEPP_DIND_IMAGE", value)
    sandbox = _DockerDaemonSandbox(tmp_path)
    monkeypatch.setattr(
        sandbox,
        "_outer",
        lambda *_args, **_kwargs: pytest.fail("outer Docker must not be called"),
    )

    with pytest.raises(_OrchestratorUnavailable, match="digest-pinned"):
        sandbox.start()


def test_dind_uses_audited_official_digest_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DICEPP_DIND_IMAGE", raising=False)
    sandbox = _DockerDaemonSandbox(tmp_path)
    calls: list[tuple[str, ...]] = []

    def outer(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[0] == "run":
            return subprocess.CompletedProcess(args, 0, "a" * 64 + "\n", "")
        if args[0] == "port":
            return subprocess.CompletedProcess(args, 0, "127.0.0.1:32768\n", "")
        raise AssertionError(args)

    monkeypatch.setattr(sandbox, "_outer", outer)
    monkeypatch.setattr(sandbox, "_wait_inner", lambda: None)
    monkeypatch.setattr(sandbox, "_wait_local_inner", lambda: None)

    sandbox.start()

    run_call = next(call for call in calls if call[0] == "run")
    assert _DEFAULT_DIND_IMAGE in run_call


def test_dind_wait_requires_three_consecutive_connections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = _DockerDaemonSandbox(tmp_path)
    sandbox.docker_env = {"DOCKER_HOST": "tcp://127.0.0.1:32768"}
    attempts: list[int] = []

    def docker(*_args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        attempts.append(len(attempts) + 1)
        if len(attempts) == 2:
            raise _OrchestratorUnavailable("connection reset by peer")
        return subprocess.CompletedProcess([], 0, "27.0.0\n", "")

    monkeypatch.setattr(module, "_docker", docker)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    sandbox._wait_inner(timeout=5)

    assert attempts == [1, 2, 3, 4, 5]


def test_dind_waits_for_container_local_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = _DockerDaemonSandbox(tmp_path)
    sandbox.container_id = "a" * 64
    attempts: list[tuple[str, ...]] = []

    monkeypatch.setattr(sandbox, "_verify_owned", lambda: None)

    def outer(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        attempts.append(args)
        if len(attempts) < 3:
            raise _OrchestratorUnavailable("local socket is not ready")
        return subprocess.CompletedProcess(args, 0, "27.0.0\n", "")

    monkeypatch.setattr(sandbox, "_outer", outer)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    sandbox._wait_local_inner(timeout=5)

    assert len(attempts) == 3
    assert attempts[-1] == (
        "exec",
        "a" * 64,
        "docker",
        "info",
        "--format",
        "{{.ServerVersion}}",
    )


def test_dind_local_docker_command_verifies_owned_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = _DockerDaemonSandbox(tmp_path)
    sandbox.container_id = "a" * 64
    events: list[object] = []

    monkeypatch.setattr(sandbox, "_verify_owned", lambda: events.append("verified"))

    def outer(*args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
        events.append((args, timeout))
        return subprocess.CompletedProcess(args, 0, "nested\n", "")

    monkeypatch.setattr(sandbox, "_outer", outer)

    result = sandbox.docker_cmd("ps", "-aq", timeout=30)

    assert result.stdout == "nested\n"
    assert events == [
        "verified",
        (("exec", "a" * 64, "docker", "ps", "-aq"), 30),
    ]


def test_dind_local_compose_forwards_only_safe_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = _DockerDaemonSandbox(tmp_path)
    sandbox.container_id = "a" * 64
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(sandbox, "_verify_owned", lambda: None)

    def outer(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(sandbox, "_outer", outer)

    sandbox.docker_cmd(
        "compose",
        "up",
        cwd=tmp_path,
        env={"DICEPP_IMAGE_TAG": "v3.0.0rc20", "SECRET": "must-not-leak"},
    )

    assert calls == [
        (
            "exec",
            "--workdir",
            str(tmp_path),
            "--env",
            "DICEPP_IMAGE_TAG=v3.0.0rc20",
            "a" * 64,
            "docker",
            "compose",
            "up",
        )
    ]
    assert "must-not-leak" not in repr(calls)


def test_manager_api_client_uses_nested_requester_instead_of_host_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str, dict[str, object] | None]] = []

    def requester(
        method: str,
        path: str,
        token: str,
        body: dict[str, object] | None,
    ) -> tuple[int, dict[str, object]]:
        calls.append((method, path, token, body))
        return 200, {"ok": True, "dicepp_version": "3.0.0rc19"}

    monkeypatch.setattr(
        module,
        "_http_json",
        lambda *_args, **_kwargs: pytest.fail("host HTTP must not be used"),
    )
    client = _ManagerApiClient(requester=requester)
    client.token = "token"

    assert client.health()["dicepp_version"] == "3.0.0rc19"
    assert calls == [("GET", "/v1/health", "token", None)]


def test_dind_manager_api_bridge_executes_inside_exact_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = _DockerDaemonSandbox(tmp_path)
    sandbox.container_id = "a" * 64
    sandbox.docker_env = {"DOCKER_HOST": "tcp://127.0.0.1:32768"}
    calls: list[tuple[str, ...]] = []

    def outer(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(
            args,
            0,
            '{"status":200,"payload":{"ok":true}}\n',
            "",
        )

    monkeypatch.setattr(sandbox, "_verify_owned", lambda: None)
    monkeypatch.setattr(sandbox, "_outer", outer)

    status, payload = sandbox.manager_api_request(
        "dicepp-manager", "POST", "/v1/upgrades/confirm", "token", {"x": 1}
    )

    assert status == 200
    assert payload == {"ok": True}
    command = calls[0]
    assert command[:6] == (
        "exec",
        sandbox.container_id,
        "docker",
        "exec",
        "dicepp-manager",
        "python",
    )
    assert any("127.0.0.1:4091" in argument for argument in command)
    assert "token" not in command
    assert command[-3:] == (
        "POST",
        "/v1/upgrades/confirm",
        '{"x":1}',
    )


def test_dind_cleanup_failure_retains_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = _DockerDaemonSandbox(tmp_path)
    sandbox.container_id = "a" * 64
    sandbox.docker_env = {"DOCKER_HOST": "tcp://isolated"}

    def outer(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[0] == "inspect":
            return subprocess.CompletedProcess(
                args, 0, stdout=f"{sandbox.container_id}|{sandbox.identity}\n", stderr=""
            )
        raise _OrchestratorUnavailable("remove failed")

    monkeypatch.setattr(sandbox, "_outer", outer)

    with pytest.raises(_OrchestratorUnavailable, match="remove failed"):
        sandbox.cleanup()
    assert sandbox.container_id == "a" * 64
    assert sandbox.docker_env == {"DOCKER_HOST": "tcp://isolated"}


def test_dind_restart_refuses_changed_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = _DockerDaemonSandbox(tmp_path)
    sandbox.container_id = "a" * 64
    sandbox.docker_env = {"DOCKER_HOST": "tcp://isolated"}
    calls: list[tuple[str, ...]] = []

    def outer(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(
            args, 0, stdout=f"{sandbox.container_id}|wrong-owner\n", stderr=""
        )

    monkeypatch.setattr(sandbox, "_outer", outer)

    with pytest.raises(_OrchestratorUnavailable, match="identity changed"):
        sandbox.restart()
    assert all(call[0] != "restart" for call in calls)


def test_dind_restart_refreshes_changed_loopback_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = _DockerDaemonSandbox(tmp_path)
    sandbox.container_id = "a" * 64
    sandbox.docker_env = {"DOCKER_HOST": "tcp://127.0.0.1:32768"}
    calls: list[tuple[str, ...]] = []
    waited_with: list[str] = []

    def outer(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[0] == "inspect":
            stdout = f"{sandbox.container_id}|{sandbox.identity}\n"
        elif args[0] == "port":
            stdout = "127.0.0.1:32769\n"
        else:
            stdout = ""
        return subprocess.CompletedProcess(args, 0, stdout, "")

    def wait_inner() -> None:
        assert sandbox.docker_env is not None
        waited_with.append(sandbox.docker_env["DOCKER_HOST"])

    monkeypatch.setattr(sandbox, "_outer", outer)
    monkeypatch.setattr(sandbox, "_wait_inner", wait_inner)
    monkeypatch.setattr(
        sandbox, "_wait_local_inner", lambda: waited_with.append("local-socket")
    )

    sandbox.restart()

    assert calls == [
        (
            "inspect",
            "--format",
            '{{.Id}}|{{index .Config.Labels "io.dicepp.upgrade-harness"}}',
            sandbox.container_id,
        ),
        ("restart", "-t", "10", sandbox.container_id),
        ("port", sandbox.container_id, "2375/tcp"),
    ]
    assert waited_with == ["tcp://127.0.0.1:32769", "local-socket"]


@pytest.mark.parametrize(
    "stdout",
    [
        "0.0.0.0:32769\n",
        "127.0.0.1:32769\n127.0.0.1:32770\n",
        "127.0.0.1:32769\n[::1]:32769\n",
    ],
)
def test_dind_endpoint_refresh_rejects_non_unique_loopback_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
) -> None:
    sandbox = _DockerDaemonSandbox(tmp_path)
    sandbox.container_id = "a" * 64
    sandbox.docker_env = {"DOCKER_HOST": "tcp://127.0.0.1:32768"}
    monkeypatch.setattr(
        sandbox,
        "_outer",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout, ""),
    )

    with pytest.raises(_OrchestratorUnavailable, match="unique loopback"):
        sandbox._refresh_endpoint()
    assert sandbox.docker_env == {"DOCKER_HOST": "tcp://127.0.0.1:32768"}


def _orchestrator(tmp_path: Path, *, sandbox: object | None = None) -> _LinuxUpgradeOrchestrator:
    return _LinuxUpgradeOrchestrator(
        source_bundle=tmp_path / "source.zip",
        source_version="3.0.0rc19",
        target_bundle=tmp_path / "target.zip",
        target_version="3.0.0rc20",
        work_dir=tmp_path / "work",
        daemon_sandbox=sandbox,
    )


def test_dind_namespace_checks_use_local_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []

    class Sandbox:
        docker_env = {"DOCKER_HOST": "tcp://probe-only"}

        def docker_cmd(
            self, *args: str, **_kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, "", "")

    orch = _orchestrator(tmp_path, sandbox=Sandbox())
    monkeypatch.setattr(
        module,
        "_docker_object_exists",
        lambda *_args, **_kwargs: pytest.fail(
            "DinD namespace checks must not use the published TCP endpoint"
        ),
    )

    orch._assert_isolated_docker_namespace()

    assert calls == [
        ("ps", "-aq", "--filter", "name=^/dicepp$"),
        ("ps", "-aq", "--filter", "name=^/dicepp-dashboard$"),
        ("ps", "-aq", "--filter", "name=^/dicepp-manager$"),
        ("network", "ls", "-q", "--filter", "name=^dice-net$"),
    ]


def test_post_commit_invalid_journal_cannot_reach_manual_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    class Sandbox:
        def restart(self) -> None:
            events.append("restart")

    orch = _orchestrator(tmp_path, sandbox=Sandbox())
    tx_dir = tmp_path / "tx"
    tx_dir.mkdir()
    request = {
        "transaction_id": "a" * 32,
        "operation_id": "operation-1",
        "manager": {"name": "dicepp-manager"},
    }
    decision = {**request, "value": "commit"}
    monkeypatch.setattr(orch, "_prepare_compose", lambda: None)
    monkeypatch.setattr(orch, "_start_source", lambda: None)
    monkeypatch.setattr(orch, "_verify_source_healthy", lambda: None)
    monkeypatch.setattr(orch, "_trigger_upgrade", lambda _scenario: None)
    monkeypatch.setattr(
        orch, "_wait_control_document", lambda _name: {"mode": "daemon_after_commit"}
    )
    monkeypatch.setattr(orch, "_find_handoff_container", lambda *_args: "updater")
    monkeypatch.setattr(orch, "_docker_cmd", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(orch, "_wait_handoff_document", lambda kind, **_kwargs: ((request if kind == "request" else decision), tx_dir))
    monkeypatch.setattr(
        orch,
        "_read_journal",
        lambda: {
            "transaction_id": request["transaction_id"],
            "operation_id": "wrong-operation",
            "status": "interrupted",
            "phase": "cleanup_pending",
        },
    )
    monkeypatch.setattr(
        orch,
        "_run_manual_handoff_helper",
        lambda *_args, **_kwargs: events.append("manual"),
    )
    orch._harness_control_dir = tmp_path

    with pytest.raises(_ScenarioExpectationFailure, match="not bound"):
        orch._run_daemon_restart_after_commit(
            "manager_handoff_commit_crash_window"
        )
    assert events == ["restart"]


@pytest.mark.parametrize(
    ("premature_value", "manual_expected"),
    [
        ("restore-failed", True),
        ("source-restored", False),
        ("target-committed", False),
    ],
)
def test_post_commit_accepts_only_restore_failed_before_manual_finalize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    premature_value: str,
    manual_expected: bool,
) -> None:
    events: list[str] = []

    class Sandbox:
        def restart(self) -> None:
            events.append("restart")

    orch = _orchestrator(tmp_path, sandbox=Sandbox())
    tx_dir = tmp_path / "tx"
    tx_dir.mkdir()
    request = {
        "transaction_id": "a" * 32,
        "operation_id": "b" * 32,
        "manager": {"name": "dicepp-manager"},
    }
    decision = {**request, "value": "commit"}
    premature = {**request, "value": premature_value}
    manual_result = {**request, "value": "target-committed"}
    journals = iter(
        [
            {
                "transaction_id": request["transaction_id"],
                "operation_id": request["operation_id"],
                "status": "interrupted",
                "phase": "cleanup_pending",
            },
            {
                "transaction_id": request["transaction_id"],
                "operation_id": request["operation_id"],
                "status": "committed",
                "phase": "cleanup_complete",
            },
        ]
    )
    monkeypatch.setattr(orch, "_prepare_compose", lambda: None)
    monkeypatch.setattr(orch, "_start_source", lambda: None)
    monkeypatch.setattr(orch, "_verify_source_healthy", lambda: None)
    monkeypatch.setattr(orch, "_trigger_upgrade", lambda _scenario: None)
    monkeypatch.setattr(
        orch, "_wait_control_document", lambda _name: {"mode": "daemon_after_commit"}
    )
    monkeypatch.setattr(orch, "_find_handoff_container", lambda *_args: "updater")
    monkeypatch.setattr(
        orch,
        "_docker_cmd",
        lambda *args, **_kwargs: events.append("docker:" + args[0]),
    )

    def wait_document(kind: str, **_kwargs: object):
        return {
            "request": (request, tx_dir),
            "decision": (decision, tx_dir),
            "result": (manual_result, tx_dir),
        }[kind]

    monkeypatch.setattr(orch, "_wait_handoff_document", wait_document)
    monkeypatch.setattr(
        orch,
        "_wait_optional_handoff_document",
        lambda *_args, **_kwargs: premature,
    )
    monkeypatch.setattr(orch, "_read_journal", lambda: next(journals))
    monkeypatch.setattr(
        orch, "_verify_handoff_document_binding", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(orch, "_verify_journal_binding", lambda *_args: None)
    monkeypatch.setattr(
        orch,
        "_verify_post_commit_recovery_material",
        lambda *_args: events.append("material"),
    )
    monkeypatch.setattr(
        orch,
        "_run_manual_handoff_helper",
        lambda *_args, **_kwargs: events.append("manual"),
    )
    monkeypatch.setattr(orch, "_wait_upgrade_complete", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(orch, "_verify_handoff_target_objects", lambda *_args: None)
    monkeypatch.setattr(orch, "_verify_sentinels", lambda *_args, **_kwargs: None)
    orch._harness_control_dir = tmp_path
    scenario = "manager_handoff_commit_crash_window"

    if manual_expected:
        assert orch._run_daemon_restart_after_commit(scenario) == "cleanup_pending"
        assert events == [
            "docker:pause",
            "restart",
            "material",
            "manual",
            "docker:start",
        ]
    else:
        with pytest.raises(
            _ScenarioExpectationFailure,
            match="did not preserve the cleanup_pending window",
        ):
            orch._run_daemon_restart_after_commit(scenario)
        assert "manual" not in events


@pytest.mark.parametrize(
    ("method_name", "expected_state"),
    [
        ("_run_daemon_restart_before_commit", "source_restored"),
        ("_run_daemon_restart_after_commit", "cleanup_pending"),
    ],
)
def test_daemon_restart_terminal_paths_verify_legacy_global_sentinel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    expected_state: str,
) -> None:
    class Sandbox:
        def restart(self) -> None:
            pass

    orch = _orchestrator(tmp_path, sandbox=Sandbox())
    scenario = "manager_handoff_commit_crash_window"
    tx_dir = tmp_path / "tx"
    tx_dir.mkdir()
    request = {
        "transaction_id": "a" * 32,
        "operation_id": "operation-1",
        "manager": {"name": "dicepp-manager"},
    }
    decision = {**request, "value": "commit"}
    restored = {**request, "value": "source-restored"}
    committed_result = {**request, "value": "target-committed"}
    verified: list[tuple[str, bool]] = []
    orch._sentinel_digests = {"config/global.json": "legacy-digest"}

    for name in (
        "_prepare_compose",
        "_start_source",
        "_verify_source_healthy",
        "_trigger_upgrade",
        "_verify_journal_binding",
        "_verify_handoff_document_binding",
        "_wait_rollback_complete",
        "_verify_handoff_source_objects",
        "_verify_dashboard_db_source",
        "_verify_handoff_rollback_journal",
        "_verify_post_commit_recovery_material",
        "_run_manual_handoff_helper",
        "_wait_upgrade_complete",
        "_verify_handoff_target_objects",
    ):
        monkeypatch.setattr(orch, name, lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        orch,
        "_verify_sentinels",
        lambda called_scenario, require_mutation=False: verified.append(
            (called_scenario, require_mutation)
        )
        if "config/global.json" in orch._sentinel_digests
        else pytest.fail("legacy global sentinel was not in the unified set"),
    )
    monkeypatch.setattr(
        orch,
        "_wait_control_document",
        lambda _name: {
            "mode": (
                "daemon_before_commit"
                if method_name.endswith("before_commit")
                else "daemon_after_commit"
            )
        },
    )
    monkeypatch.setattr(orch, "_find_handoff_container", lambda *_args: "updater")
    monkeypatch.setattr(
        orch,
        "_docker_cmd",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "", ""),
    )
    monkeypatch.setattr(
        orch,
        "_wait_optional_handoff_document",
        lambda *_args, **_kwargs: None,
    )
    orch._harness_control_dir = tmp_path

    if method_name.endswith("before_commit"):
        monkeypatch.setattr(
            orch,
            "_wait_handoff_document",
            lambda kind, **_kwargs: (
                request if kind == "request" else restored,
                tx_dir,
            ),
        )
        monkeypatch.setattr(
            orch,
            "_read_journal",
            lambda: {**request, "status": "rolled_back"},
        )
    else:
        monkeypatch.setattr(
            orch,
            "_wait_handoff_document",
            lambda kind, **_kwargs: (
                request
                if kind == "request"
                else decision
                if kind == "decision"
                else committed_result,
                tx_dir,
            ),
        )
        journals = iter(
            [
                {**request, "status": "interrupted", "phase": "cleanup_pending"},
                {**request, "status": "committed", "phase": "committed"},
            ]
        )
        monkeypatch.setattr(orch, "_read_journal", lambda: next(journals))

    state = getattr(orch, method_name)(scenario)

    assert state == expected_state
    assert verified == [(scenario, False)]


def test_observed_cleanup_pending_then_manual_recovery_is_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    class Sandbox:
        docker_env: dict[str, str] | None = {"DOCKER_HOST": "tcp://initial"}

        def cleanup(self) -> None:
            events.append("original.cleanup")
            self.docker_env = None

        def start(self) -> None:
            pytest.fail("the original sandbox must not be reused")

    class FreshSandbox:
        docker_env: dict[str, str] | None = None

        def __init__(self, _work_root: Path) -> None:
            events.append("fresh.created")

        def start(self) -> None:
            events.append("fresh.start")
            self.docker_env = {"DOCKER_HOST": "tcp://fresh"}

        def cleanup(self) -> None:
            events.append("fresh.cleanup")
            self.docker_env = None

    orch = _orchestrator(tmp_path, sandbox=Sandbox())

    class Before:
        def _run_daemon_restart_before_commit(self, _scenario: str) -> str:
            return "source_restored"

        def cleanup(self) -> None:
            events.append("before.cleanup")

    class After:
        def _run_daemon_restart_after_commit(self, _scenario: str) -> str:
            return "cleanup_pending"

        def cleanup(self) -> None:
            events.append("after.cleanup")

    children = iter([Before(), After()])
    monkeypatch.setattr(orch, "_fork_daemon_case", lambda *_args: next(children))
    monkeypatch.setattr(module, "_DockerDaemonSandbox", FreshSandbox)

    result = orch.linux_manager_handoff_daemon_restart()

    assert result["status"] == "passed"
    assert result["observations"]["crash_after_commit_final_state"] == "cleanup_pending"
    assert events == [
        "before.cleanup",
        "original.cleanup",
        "fresh.created",
        "fresh.start",
        "after.cleanup",
        "fresh.cleanup",
    ]
    assert isinstance(orch._daemon_sandbox, Sandbox)
    assert orch._docker_env is None
    assert result["assertions"] == {
        "crash_before_commit_allowed_source_restore": True,
        "crash_after_commit_never_rolled_back": True,
        "recovery_material_preserved": True,
        "terminal_state_recorded": True,
    }


def test_commit_entry_emits_only_explicit_assertions_and_protocol_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _orchestrator(tmp_path)
    orch._seeded_bundle_path = tmp_path / "validated-target.zip"
    request = {"transaction_id": "a" * 32, "operation_id": "operation-1"}
    decision = {**request, "value": "commit"}
    for name in (
        "_prepare_compose",
        "_start_source",
        "_verify_source_healthy",
        "_wait_upgrade_complete",
        "_verify_handoff_target_objects",
    ):
        monkeypatch.setattr(orch, name, lambda *_args, **_kwargs: None)
    monkeypatch.setattr(orch, "_trigger_upgrade", lambda _scenario: None)
    monkeypatch.setattr(
        orch,
        "_wait_handoff_document",
        lambda kind, **_kwargs: (
            request if kind == "request" else decision,
            tmp_path,
        ),
    )
    monkeypatch.setattr(
        orch,
        "_read_journal",
        lambda: {
            **request,
            "status": "committed",
            "phase": "committed",
            "detail": {},
        },
    )
    read_paths: list[Path] = []

    def read_manifest(path: Path) -> dict[str, int]:
        read_paths.append(path)
        return {"linux_manager_handoff_protocol": 1}

    monkeypatch.setattr(module, "_read_bundle_manifest", read_manifest)
    monkeypatch.setitem(
        SCENARIO_ASSERTIONS,
        "manager_handoff_commit",
        SCENARIO_ASSERTIONS["manager_handoff_commit"] | {"future_assertion"},
    )
    verified: list[tuple[str, bool]] = []
    orch._sentinel_digests = {"config/global.json": "legacy-digest"}
    monkeypatch.setattr(
        orch,
        "_verify_sentinels",
        lambda scenario, require_mutation=False: verified.append(
            (scenario, require_mutation)
        )
        if "config/global.json" in orch._sentinel_digests
        else pytest.fail("legacy global sentinel was not in the unified set"),
    )

    result = orch.linux_manager_handoff_success()

    assert result["status"] == "passed"
    assert "future_assertion" not in result["assertions"]
    assert result["observations"]["handoff_protocol"] == "1"
    assert type(result["observations"]["handoff_protocol"]) is str
    assert read_paths == [orch._seeded_bundle_path]
    assert verified == [("manager_handoff_commit", False)]


def test_dashboard_snapshot_verification_falls_back_to_source_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _orchestrator(tmp_path)
    orch._instance_dir = tmp_path / "instance"
    orch._container_names = {"manager": "source-manager"}
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(
        module.sqlite3,
        "connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            module.sqlite3.OperationalError("unable to open database file")
        ),
    )

    def docker_cmd(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, '["source"]\n', "")

    monkeypatch.setattr(orch, "_docker_cmd", docker_cmd)

    orch._verify_dashboard_db_source("manager_handoff_rollback")

    assert len(calls) == 1
    assert calls[0][:4] == ("exec", "source-manager", "python", "-c")
    assert "file:/app/dashboard/data/dashboard.db?mode=ro&immutable=1" in calls[0][4]


def test_rollback_entry_emits_only_explicit_assertions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _orchestrator(tmp_path)
    request = {"transaction_id": "a" * 32, "operation_id": "operation-1"}
    result_document = {**request, "value": "source-restored"}
    for name in (
        "_prepare_compose",
        "_start_source",
        "_verify_source_healthy",
        "_wait_rollback_complete",
        "_verify_handoff_source_objects",
        "_verify_sentinels",
        "_verify_dashboard_db_source",
    ):
        monkeypatch.setattr(orch, name, lambda *_args, **_kwargs: None)
    monkeypatch.setattr(orch, "_trigger_upgrade", lambda _scenario: None)
    monkeypatch.setattr(
        orch,
        "_wait_handoff_document",
        lambda kind, **_kwargs: (
            request if kind == "request" else result_document,
            tmp_path,
        ),
    )
    monkeypatch.setattr(
        orch,
        "_wait_control_document",
        lambda _name: {"version": orch.target_version, "mode": "target_crash"},
    )
    monkeypatch.setattr(orch, "_sentinels_differ_from_source", lambda: True)
    monkeypatch.setattr(
        orch,
        "_read_journal",
        lambda: {
            **request,
            "status": "rolled_back",
            "phase": "rolled_back",
            "detail": {"rolled_back": True, "rollback_status": "succeeded"},
        },
    )
    monkeypatch.setitem(
        SCENARIO_ASSERTIONS,
        "manager_handoff_rollback",
        SCENARIO_ASSERTIONS["manager_handoff_rollback"] | {"future_assertion"},
    )

    result = orch.linux_manager_handoff_target_crash()

    assert result["status"] == "passed"
    assert "future_assertion" not in result["assertions"]


def test_transaction_cleanup_rechecks_exact_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _orchestrator(tmp_path)
    transaction_id = "a" * 32
    orch._transaction_ids.add(transaction_id)
    calls: list[tuple[str, ...]] = []

    def docker(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[0] == "ps":
            return subprocess.CompletedProcess(args, 0, "container-id\n", "")
        if args[0] == "inspect":
            return subprocess.CompletedProcess(args, 0, transaction_id + "\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(orch, "_docker_cmd", docker)

    orch._cleanup_handoff_containers()

    assert ("rm", "-f", "container-id") in calls


def test_network_cleanup_uses_verified_id_and_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _orchestrator(tmp_path)
    orch._owns_dice_network = True
    orch._network_id = "b" * 64
    calls: list[tuple[str, ...]] = []

    def docker(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if "inspect" in args:
            return subprocess.CompletedProcess(
                args,
                0,
                f"{orch._network_id}|{orch._network_identity}\n",
                "",
            )
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(orch, "_docker_cmd", docker)

    orch._cleanup_owned_network()

    assert ("network", "rm", "b" * 64) in calls
    assert orch._network_id is None


def test_network_cleanup_refuses_changed_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _orchestrator(tmp_path)
    orch._owns_dice_network = True
    orch._network_id = "b" * 64
    calls: list[tuple[str, ...]] = []

    def docker(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(
            args, 0, f"{orch._network_id}|wrong-owner\n", ""
        )

    monkeypatch.setattr(orch, "_docker_cmd", docker)

    with pytest.raises(_OrchestratorUnavailable, match="ownership changed"):
        orch._cleanup_owned_network()
    assert all("rm" not in call for call in calls)
    assert orch._network_id == "b" * 64


def test_transaction_cleanup_refuses_changed_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _orchestrator(tmp_path)
    orch._transaction_ids.add("a" * 32)
    calls: list[tuple[str, ...]] = []

    def docker(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        stdout = "container-id\n" if args[0] == "ps" else "wrong-owner\n"
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(orch, "_docker_cmd", docker)

    with pytest.raises(_OrchestratorUnavailable, match="ownership changed"):
        orch._cleanup_handoff_containers()
    assert all(call[:2] != ("rm", "-f") for call in calls)
