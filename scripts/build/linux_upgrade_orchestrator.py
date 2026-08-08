"""Docker-based Linux cross-version upgrade scenario orchestrator.

Called by ``linux_upgrade_matrix_harness.py`` to execute the four-scenario
contract against real Docker images.  The orchestrator is deliberately
fail-closed: any missing prerequisite (Docker unavailable, images missing,
compose file unreadable) produces an ``unavailable`` result rather than a
false-positive ``passed``.

Scenario execution drives the Manager's local HTTP API
(``/v1/health``, ``/v1/upgrades/preview``, ``/v1/upgrades/confirm``,
``/v1/upgrades/status``) and the Docker CLI; the Manager itself performs the
upgrade transaction against the seeded release and the shared instance
directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import select
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from contextlib import closing
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

SCENARIO_ASSERTIONS: dict[str, frozenset[str]] = {
    "healthy_commit": frozenset(
        {
            "source_started",
            "target_started",
            "local_health_passed",
            "journal_committed",
        }
    ),
    "target_health_failure_rollback": frozenset(
        {
            "target_executed",
            "health_failure_injected",
            "program_restored",
            "data_restored",
            "source_restarted",
            "journal_rolled_back",
        }
    ),
    "retry_after_rollback": frozenset(
        {
            "prior_rollback_observed",
            "retry_started_same_instance",
            "target_started",
            "journal_committed",
        }
    ),
    "apply_failure_before_target_execution": frozenset(
        {
            "apply_failure_injected",
            "target_never_executed",
            "source_remained_or_restored",
            "no_target_migration",
            "terminal_state_recorded",
        }
    ),
    "manager_handoff_commit": frozenset(
        {
            "manager_handoff_completed",
            "target_containers_started",
            "local_health_passed",
            "commit_decision_written",
        }
    ),
    "manager_handoff_rollback": frozenset(
        {
            "target_manager_failed",
            "source_manager_restored",
            "program_restored",
            "data_restored",
            "dashboard_db_restored",
            "source_restarted",
            "journal_rolled_back",
        }
    ),
    "manager_handoff_commit_crash_window": frozenset(
        {
            "crash_before_commit_allowed_source_restore",
            "crash_after_commit_never_rolled_back",
            "recovery_material_preserved",
            "terminal_state_recorded",
        }
    ),
}

SCENARIO_OBSERVATION_FIELDS: dict[str, frozenset[str]] = {
    "healthy_commit": frozenset(
        {
            "source_version_before",
            "target_version_after",
            "journal_status",
            "health_status",
        }
    ),
    "target_health_failure_rollback": frozenset(
        {
            "target_version_observed",
            "restored_version",
            "journal_status",
            "rollback_marker_status",
        }
    ),
    "retry_after_rollback": frozenset(
        {
            "first_transaction_status",
            "retry_transaction_status",
            "final_version",
        }
    ),
    "apply_failure_before_target_execution": frozenset(
        {
            "target_process_start_count",
            "source_version_after",
            "journal_status",
            "apply_exit_code",
        }
    ),
    "manager_handoff_commit": frozenset(
        {
            "source_version_before",
            "target_version_after",
            "handoff_protocol",
            "journal_status",
            "health_status",
        }
    ),
    "manager_handoff_rollback": frozenset(
        {
            "target_version_observed",
            "restored_version",
            "result_status",
            "journal_status",
            "rollback_marker_status",
        }
    ),
    "manager_handoff_commit_crash_window": frozenset(
        {
            "crash_before_commit_final_state",
            "crash_after_commit_final_state",
            "decision_status",
        }
    ),
}

# External evidence names remain stable; the internal method names make the
# fault being exercised explicit.  Keeping this as data also prevents a newly
# declared evidence scenario from falling through to an uncaught ``KeyError``.
SCENARIO_METHODS: dict[str, str] = {
    "healthy_commit": "healthy_commit",
    "target_health_failure_rollback": "target_health_failure_rollback",
    "retry_after_rollback": "retry_after_rollback",
    "apply_failure_before_target_execution": "apply_failure_before_target_execution",
    "manager_handoff_commit": "linux_manager_handoff_success",
    "manager_handoff_rollback": "linux_manager_handoff_target_crash",
    "manager_handoff_commit_crash_window": "linux_manager_handoff_daemon_restart",
}

_DEFAULT_DIND_IMAGE = (
    "docker.io/library/docker@sha256:"
    "7613944c7bc318c7b97541bd0e65b8a18d033e37e204305f1ee2639fc9a03827"
)

_MANAGER_API_EXEC_SCRIPT = r'''import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

method, path, encoded_body = sys.argv[1:4]
token = Path("/app/manager/state/api-token").read_text(encoding="utf-8").strip()
if not token:
    raise RuntimeError("Manager API token is empty")
data = None if encoded_body == "null" else encoded_body.encode("utf-8")
headers = {"Authorization": f"Bearer {token}"}
if data is not None:
    headers["Content-Type"] = "application/json"
request = urllib.request.Request(
    "http://127.0.0.1:4091" + path,
    data=data,
    headers=headers,
    method=method,
)
try:
    with urllib.request.urlopen(request, timeout=5) as response:
        status = response.status
        payload = json.loads(response.read().decode("utf-8"))
except urllib.error.HTTPError as error:
    status = int(error.code)
    payload = json.loads(error.read().decode("utf-8"))
print(json.dumps({"status": status, "payload": payload}))
'''

_MANAGER_HARNESS_WRAPPER = r'''from __future__ import annotations

import json
import runpy
import sqlite3
import sys
import time
from pathlib import Path

from dicepp_meta import get_version


def _publish(control: Path, name: str, payload: dict[str, object]) -> None:
    temporary = control / f".{name}.tmp"
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    temporary.replace(control / name)


control = Path("/app/manager/harness")
control.mkdir(parents=True, exist_ok=True)
mode = __import__("os").environ.get("DICEPP_HANDOFF_HARNESS_MODE", "passthrough")
target = __import__("os").environ.get("DICEPP_HANDOFF_HARNESS_TARGET", "")
version = get_version()
if target and version == target:
    if mode == "target_crash":
        Path("/app/config/user.json").write_text(
            '{"damaged_by_target_manager":true}\n', encoding="utf-8"
        )
        Path("/app/data/local_images/sentinel.bin").write_bytes(
            b"damaged-by-target-manager"
        )
        db = Path("/app/dashboard/data/dashboard.db")
        with sqlite3.connect(db) as connection:
            connection.execute(
                "UPDATE dicepp_harness_sentinel SET value = 'target-damaged' WHERE id = 1"
            )
            connection.commit()
        _publish(
            control,
            "target-manager-observed.json",
            {"version": version, "mode": mode},
        )
        time.sleep(3)
        raise SystemExit(86)
    _publish(control, "target-manager-observed.json", {"version": version, "mode": mode})
    if mode in {"daemon_before_commit", "daemon_after_commit"}:
        gate = control / "allow-target-manager"
        while not gate.exists():
            time.sleep(0.1)

runpy.run_module("dicepp_manager", run_name="__main__")
'''

_HANDOFF_FILENAMES = {
    "request": "linux-manager-switch.request.json",
    "decision": "linux-manager-switch.decision.json",
    "result": "linux-manager-switch.result.json",
}


def run_linux_scenario(context: dict[str, Any], work_dir: Path) -> dict[str, Any]:
    """Execute a single upgrade scenario against real Docker images.

    Returns a result dict with ``status``, ``assertions``, and ``observations``
    that conforms to the harness result contract v1.  Returns ``unavailable``
    when any prerequisite is missing.
    """
    platform = context.get("platform")
    scenario = context.get("scenario")
    if platform != "linux" or scenario not in SCENARIO_ASSERTIONS:
        return _unavailable(context, "unsupported platform or scenario")

    if not _docker_available():
        return _unavailable(context, "Docker is unavailable")

    source_assets = context.get("source_assets")
    target_assets = context.get("target_assets")
    if not isinstance(source_assets, list) or not source_assets:
        return _unavailable(context, "source assets are missing")
    if not isinstance(target_assets, list) or not target_assets:
        return _unavailable(context, "target assets are missing")

    source_bundle = _find_bundle(source_assets, "linux-bundle")
    target_bundle = _find_bundle(target_assets, "linux-bundle")
    if source_bundle is None:
        return _unavailable(context, "source linux-bundle asset not found")
    if target_bundle is None:
        return _unavailable(context, "target linux-bundle asset not found")

    sandbox: _DockerDaemonSandbox | None = None
    orchestrator: _LinuxUpgradeOrchestrator | None = None
    result: dict[str, Any]
    execution_error: Exception | None = None
    try:
        if scenario == "manager_handoff_commit_crash_window":
            sandbox = _DockerDaemonSandbox(work_dir)
            sandbox.start()
        orchestrator = _LinuxUpgradeOrchestrator(
            source_bundle=Path(source_bundle["path"]),
            source_version=context["source_version"],
            target_bundle=Path(target_bundle["path"]),
            target_version=context["target_version"],
            work_dir=work_dir,
            docker_env=sandbox.docker_env if sandbox is not None else None,
            daemon_sandbox=sandbox,
            use_socket_proxy=sandbox is None,
        )
        scenario_func = getattr(orchestrator, SCENARIO_METHODS[scenario])
        result = scenario_func()
    except _OrchestratorUnavailable as exc:
        result = _unavailable(context, str(exc))
    except Exception as exc:  # cleanup still runs before an unexpected error escapes
        execution_error = exc
        result = _unavailable(context, "scenario execution failed unexpectedly")
    cleanup_errors: list[str] = []
    if orchestrator is not None:
        try:
            orchestrator.cleanup()
        except _OrchestratorUnavailable as exc:
            cleanup_errors.append(str(exc))
    if sandbox is not None:
        if orchestrator is not None:
            orchestrator = None
        try:
            sandbox.cleanup()
        except _OrchestratorUnavailable as exc:
            cleanup_errors.append(str(exc))
    if cleanup_errors:
        return _unavailable(
            context,
            "isolated cleanup did not complete: " + "; ".join(cleanup_errors),
        )
    if execution_error is not None:
        raise execution_error
    return result


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------

def _unavailable(context: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "contract_version": 1,
        "platform": context.get("platform", "linux"),
        "arch": context.get("arch", "amd64"),
        "source_version": context.get("source_version", ""),
        "target_version": context.get("target_version", ""),
        "scenario": context.get("scenario", ""),
        "status": "unavailable",
        "assertions": {},
        "observations": {"reason": reason},
    }


def _result(
    context: dict[str, Any],
    scenario: str,
    passed: bool,
    assertions: dict[str, bool],
    observations: dict[str, Any],
) -> dict[str, Any]:
    return {
        "contract_version": 1,
        "platform": context["platform"],
        "arch": context["arch"],
        "source_version": context["source_version"],
        "target_version": context["target_version"],
        "scenario": scenario,
        "status": "passed" if passed else "failed",
        "assertions": assertions,
        "observations": observations,
    }


def _build_scenario_result(
    orchestrator: _LinuxUpgradeOrchestrator,
    scenario: str,
    assertions: dict[str, bool],
    observations: dict[str, Any],
) -> dict[str, Any]:
    """Build a result dict from the orchestrator's context and supplied values."""
    return {
        "contract_version": 1,
        "platform": "linux",
        "arch": "amd64",
        "source_version": orchestrator.source_version,
        "target_version": orchestrator.target_version,
        "scenario": scenario,
        "status": "passed",
        "assertions": assertions,
        "observations": observations,
    }


# ---------------------------------------------------------------------------
# Prerequisite checks
# ---------------------------------------------------------------------------

def _docker_available() -> bool:
    executable = shutil.which("docker")
    if not executable:
        return False
    try:
        subprocess.run(
            [executable, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False
    return True


def _find_bundle(
    assets: list[dict[str, Any]], purpose: str
) -> dict[str, Any] | None:
    for asset in assets:
        if asset.get("purpose") == purpose:
            return asset
    return None


def _load_docker_image_from_bundle(
    bundle_path: Path,
    work_dir: Path,
    label: str,
    *,
    docker_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Extract and ``docker load`` images from a Linux release bundle.

    Returns a mapping ``{"bot": image_id, "dashboard": image_id}``.
    """
    extract_dir = work_dir / f"{label}-images"
    extract_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(bundle_path, "r") as archive:
            package = json.loads(
                archive.read("dicepp-package.json").decode("utf-8")
            )
            image_archive = package["image_archive"]
            member_path = image_archive["path"]
            if (
                not isinstance(member_path, str)
                or member_path.startswith("/")
                or ".." in Path(member_path).parts
            ):
                raise _OrchestratorUnavailable(
                    f"{label} bundle image archive path is unsafe: "
                    f"{member_path!r}"
                )
            archive_path = extract_dir / member_path
            archive.extract(member_path, extract_dir)
    except (KeyError, OSError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise _OrchestratorUnavailable(
            f"cannot extract {label} bundle: {exc}"
        ) from exc

    if not archive_path.resolve().is_relative_to(extract_dir.resolve()):
        raise _OrchestratorUnavailable(
            f"{label} image archive escapes the extract directory"
        )
    if not archive_path.is_file():
        raise _OrchestratorUnavailable(
            f"{label} image archive not found in bundle"
        )

    # Decompress zstd and load into Docker
    if archive_path.name.endswith(".zst"):
        tar_path = archive_path.with_suffix("")
        try:
            subprocess.run(
                ["zstd", "-d", "-f", str(archive_path), "-o", str(tar_path)],
                capture_output=True,
                text=True,
                timeout=300,
                check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            raise _OrchestratorUnavailable(
                f"cannot decompress {label} image archive: {exc}"
            ) from exc
    else:
        tar_path = archive_path

    try:
        subprocess.run(
            ["docker", "load", "-i", str(tar_path)],
            capture_output=True,
            text=True,
            timeout=120,
            env=docker_env,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        raise _OrchestratorUnavailable(
            f"cannot docker load {label} images: {exc}"
        ) from exc

    images: dict[str, str] = {}
    for item in package["images"]:
        role = item["role"]
        image_ref = item["reference"]
        try:
            result = subprocess.run(
                [
                    "docker", "image", "inspect",
                    "--format", "{{.Id}}",
                    image_ref,
                ],
                capture_output=True,
                text=True,
                timeout=30,
                env=docker_env,
                check=True,
            )
            images[role] = result.stdout.strip()
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
        ) as exc:
            raise _OrchestratorUnavailable(
                f"cannot inspect {label} image {image_ref!r} "
                f"after docker load: {exc}"
            ) from exc
    return images


# ---------------------------------------------------------------------------
# Module-level seams (stdlib only, unit-testable)
# ---------------------------------------------------------------------------

class _OrchestratorUnavailable(Exception):
    """A prerequisite is missing; the harness must return ``unavailable``."""


class _ManagerApiError(Exception):
    """The Manager API rejected a request with an HTTP error status."""

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"Manager API error {status}: {detail}")


class _ScenarioExpectationFailure(Exception):
    """A scenario expectation was not met; the scenario must fail.

    Carries the failed assertion/observation payloads so scenario methods can
    convert it into a ``failed`` result without re-deriving them.
    """

    def __init__(
        self,
        message: str,
        *,
        assertions: dict[str, bool],
        observations: dict[str, Any],
    ) -> None:
        super().__init__(message)
        self.assertions = dict(assertions)
        self.observations = dict(observations)


class _DockerSocketProxy:
    """Transparent Docker socket proxy with one narrowly scoped failpoint.

    The Linux apply-failure scenario must fail after the upgrade transaction has
    started, but before target code executes.  The source Manager therefore uses
    this harness-owned socket.  Normal requests are forwarded byte-for-byte; an
    explicitly armed proxy rejects exactly one container-create request with a
    Docker 500 response and immediately disarms itself.
    """

    def __init__(
        self,
        path: Path,
        upstream: Path = Path("/var/run/docker.sock"),
        *,
        on_create_failure: Callable[[], None] | None = None,
    ) -> None:
        self.path = path
        self.upstream = upstream
        self._stop = threading.Event()
        self._armed = threading.Event()
        self._thread: threading.Thread | None = None
        self.failure_count = 0
        self.failure_status: int | None = None
        self.last_error: str | None = None
        self._on_create_failure = on_create_failure

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.unlink(missing_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.path))
        self.path.chmod(0o666)
        listener.listen(16)
        listener.settimeout(0.5)

        def serve() -> None:
            try:
                while not self._stop.is_set():
                    try:
                        client, _address = listener.accept()
                    except TimeoutError:
                        continue
                    threading.Thread(
                        target=self._handle,
                        args=(client,),
                        daemon=True,
                    ).start()
            except OSError as exc:
                if not self._stop.is_set():
                    self.last_error = str(exc)
            finally:
                listener.close()

        self._thread = threading.Thread(target=serve, daemon=True)
        self._thread.start()

    def arm_container_create_failure(self) -> None:
        self._armed.set()

    def stop(self) -> None:
        self._stop.set()
        try:
            wake = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            wake.connect(str(self.path))
            wake.close()
        except OSError:
            pass
        if self._thread is not None:
            self._thread.join(timeout=2)
        self.path.unlink(missing_ok=True)

    def _handle(self, client: socket.socket) -> None:
        with client:
            try:
                initial = _recv_http_headers(client)
                request_line = initial.split(b"\r\n", 1)[0]
                parts = request_line.split(b" ", 2)
                is_create = (
                    len(parts) == 3
                    and parts[0] == b"POST"
                    and parts[1].split(b"?", 1)[0].endswith(b"/containers/create")
                )
                if is_create and self._armed.is_set():
                    self._armed.clear()
                    if self._on_create_failure is not None:
                        try:
                            self._on_create_failure()
                        except Exception as exc:
                            self.last_error = str(exc) or type(exc).__name__
                    self.failure_count += 1
                    self.failure_status = 500
                    payload = b'{"message":"upgrade matrix injected container create failure"}'
                    client.sendall(
                        b"HTTP/1.1 500 Internal Server Error\r\n"
                        b"Content-Type: application/json\r\n"
                        + f"Content-Length: {len(payload)}\r\n".encode("ascii")
                        + b"Connection: close\r\n\r\n"
                        + payload
                    )
                    return
                upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                with upstream:
                    upstream.connect(str(self.upstream))
                    upstream.sendall(initial)
                    _relay_sockets(client, upstream)
            except OSError as exc:
                self.last_error = str(exc)


class _DockerDaemonSandbox:
    """Own an isolated Docker-in-Docker daemon for daemon-restart evidence.

    The outer daemon is used only to host one labelled privileged DinD
    container.  Candidate objects, fixed Compose names, ports and daemon
    restarts all live inside the nested daemon, so this scenario never stops
    or restarts unrelated host containers.
    """

    _LABEL = "io.dicepp.upgrade-harness"

    def __init__(self, work_root: Path) -> None:
        self.work_root = work_root.resolve()
        self.identity = uuid4().hex
        self.name = f"dicepp-upgrade-dind-{self.identity[:12]}"
        self.container_id: str | None = None
        self.docker_env: dict[str, str] | None = None

    @staticmethod
    def _host_env() -> dict[str, str]:
        env = dict(os.environ)
        for key in (
            "DOCKER_HOST",
            "DOCKER_TLS",
            "DOCKER_TLS_VERIFY",
            "DOCKER_CERT_PATH",
        ):
            env.pop(key, None)
        return env

    def _outer(self, *args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
        return _docker(
            "docker",
            *args,
            timeout=timeout,
            env=self._host_env(),
        )

    def _wait_inner(self, timeout: float = 90) -> None:
        if self.docker_env is None:
            raise _OrchestratorUnavailable("isolated Docker endpoint is unavailable")
        deadline = time.monotonic() + timeout
        last_error = "nested daemon did not answer"
        while time.monotonic() < deadline:
            try:
                _docker(
                    "docker",
                    "info",
                    "--format",
                    "{{.ServerVersion}}",
                    timeout=10,
                    env=self.docker_env,
                )
                return
            except _OrchestratorUnavailable as exc:
                last_error = str(exc)
                time.sleep(1)
        raise _OrchestratorUnavailable(
            f"isolated Docker daemon did not become ready: {last_error}"
        )

    def _refresh_endpoint(self) -> None:
        if self.container_id is None:
            raise _OrchestratorUnavailable("isolated Docker daemon is not started")
        port_result = self._outer("port", self.container_id, "2375/tcp")
        endpoints = [
            line.strip()
            for line in port_result.stdout.splitlines()
            if line.strip().startswith("127.0.0.1:")
        ]
        if len(endpoints) != 1 or len(
            [line for line in port_result.stdout.splitlines() if line.strip()]
        ) != 1:
            raise _OrchestratorUnavailable(
                "isolated Docker daemon has no unique loopback endpoint"
            )
        self.docker_env = self._host_env()
        self.docker_env["DOCKER_HOST"] = f"tcp://{endpoints[0]}"

    def manager_api_request(
        self,
        manager_name: str,
        method: str,
        path: str,
        token: str,
        body: dict[str, Any] | None,
    ) -> tuple[int, dict[str, Any]]:
        """Call Manager localhost from its own container via the owned DinD."""
        del token  # The bridge reads Manager's mounted token; never pass it in argv.
        self._verify_owned()
        if self.container_id is None:
            raise _OrchestratorUnavailable("isolated Docker daemon is not started")
        result = self._outer(
            "exec",
            self.container_id,
            "docker",
            "exec",
            manager_name,
            "python",
            "-c",
            _MANAGER_API_EXEC_SCRIPT,
            method,
            path,
            json.dumps(body, separators=(",", ":")) if body is not None else "null",
            timeout=30,
        )
        try:
            envelope = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise _OrchestratorUnavailable(
                f"Manager API bridge returned invalid JSON: {exc}"
            ) from exc
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"status", "payload"}
            or type(envelope["status"]) is not int
            or not isinstance(envelope["payload"], dict)
        ):
            raise _OrchestratorUnavailable(
                "Manager API bridge returned an invalid response envelope"
            )
        status = envelope["status"]
        payload = envelope["payload"]
        if status >= 400:
            raise _ManagerApiError(status, str(payload.get("detail", "")))
        return status, payload

    def start(self) -> None:
        self.work_root.mkdir(parents=True, exist_ok=True)
        image = os.environ.get("DICEPP_DIND_IMAGE", _DEFAULT_DIND_IMAGE).strip()
        if not re.fullmatch(
            r"docker\.io/library/docker@sha256:[0-9a-f]{64}", image
        ):
            raise _OrchestratorUnavailable(
                "DICEPP_DIND_IMAGE must be a digest-pinned "
                "docker.io/library/docker reference"
            )
        try:
            result = self._outer(
                "run",
                "-d",
                "--privileged",
                "--name",
                self.name,
                "--label",
                f"{self._LABEL}={self.identity}",
                "-e",
                "DOCKER_TLS_CERTDIR=",
                "-p",
                "127.0.0.1::2375",
                "--mount",
                f"type=bind,src={self.work_root},dst={self.work_root}",
                image,
                "--host=tcp://0.0.0.0:2375",
                "--host=unix:///var/run/docker.sock",
                timeout=180,
            )
            container_id = result.stdout.strip()
            if not container_id or any(
                character not in "0123456789abcdef" for character in container_id
            ):
                raise _OrchestratorUnavailable(
                    "Docker did not return the isolated daemon container id"
                )
            self.container_id = container_id
            self._refresh_endpoint()
            self._wait_inner()
        except Exception:
            self.cleanup()
            raise

    def _verify_owned(self) -> None:
        if self.container_id is None:
            raise _OrchestratorUnavailable("isolated Docker daemon is not started")
        inspected = self._outer(
            "inspect",
            "--format",
            "{{.Id}}|{{index .Config.Labels \"io.dicepp.upgrade-harness\"}}",
            self.container_id,
        ).stdout.strip()
        actual_id, separator, actual_label = inspected.partition("|")
        if (
            not separator
            or actual_id != self.container_id
            or actual_label != self.identity
        ):
            raise _OrchestratorUnavailable(
                "isolated Docker daemon identity changed; refusing to touch it"
            )

    def restart(self) -> None:
        self._verify_owned()
        assert self.container_id is not None
        self._outer("restart", "-t", "10", self.container_id, timeout=60)
        self._refresh_endpoint()
        self._wait_inner()

    def cleanup(self) -> None:
        if self.container_id is None:
            return
        self._verify_owned()
        self._outer("rm", "-f", self.container_id, timeout=60)
        self.container_id = None
        self.docker_env = None


def _recv_http_headers(connection: socket.socket) -> bytes:
    payload = bytearray()
    while b"\r\n\r\n" not in payload:
        chunk = connection.recv(65536)
        if not chunk:
            raise OSError("Docker proxy client closed before sending headers")
        payload.extend(chunk)
        if len(payload) > 1024 * 1024:
            raise OSError("Docker proxy request headers are too large")
    return bytes(payload)


def _relay_sockets(left: socket.socket, right: socket.socket) -> None:
    active = {left, right}
    while active:
        readable, _writable, _errors = select.select(list(active), [], [], 300)
        if not readable:
            raise OSError("Docker proxy connection timed out")
        for source in readable:
            destination = right if source is left else left
            chunk = source.recv(65536)
            if chunk:
                destination.sendall(chunk)
                continue
            active.remove(source)
            try:
                destination.shutdown(socket.SHUT_WR)
            except OSError:
                pass


def _http_json(
    method: str,
    url: str,
    *,
    token: str,
    body: dict[str, Any] | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict[str, Any]]:
    """Perform an authenticated JSON request against the Manager API.

    Returns ``(status, payload)``; ``HTTPError`` becomes ``_ManagerApiError``
    and network failures become ``_OrchestratorUnavailable``.
    """
    headers = {"Authorization": f"Bearer {token}"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            parsed = json.loads(response.read().decode("utf-8"))
            if not isinstance(parsed, dict):
                raise _OrchestratorUnavailable(
                    "Manager API returned a non-object response"
                )
            return response.status, parsed
    except urllib.error.HTTPError as exc:
        raw_detail = ""
        try:
            raw_detail = exc.read().decode("utf-8", errors="replace").strip()
            parsed = json.loads(raw_detail)
            if isinstance(parsed, dict):
                detail = str(
                    parsed.get("detail")
                    or parsed.get("message")
                    or parsed.get("error")
                    or raw_detail
                )
            else:
                detail = raw_detail
        except (OSError, ValueError):
            detail = raw_detail
        raise _ManagerApiError(int(exc.code), detail) from exc
    except urllib.error.URLError as exc:
        raise _OrchestratorUnavailable(
            f"Manager API is unreachable: {exc.reason}"
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _OrchestratorUnavailable(
            f"Manager API returned invalid JSON: {exc}"
        ) from exc


def _docker(
    *args: str,
    timeout: float = 60,
    cwd: str | os.PathLike[str] | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a Docker CLI command; any failure becomes ``_OrchestratorUnavailable``."""
    command = list(args)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=env,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise _OrchestratorUnavailable(
            f"docker command failed: {exc}"
        ) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise _OrchestratorUnavailable(
            f"docker {' '.join(command)} failed with exit {result.returncode}: "
            f"{detail[-400:]}"
        )
    return result


def _docker_object_exists(
    kind: str, name: str, *, docker_env: dict[str, str] | None = None
) -> bool:
    try:
        result = subprocess.run(
            ["docker", kind, "inspect", name],
            capture_output=True,
            text=True,
            timeout=30,
            env=docker_env,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise _OrchestratorUnavailable(
            f"cannot inspect Docker {kind} {name!r}: {exc}"
        ) from exc
    if result.returncode == 0:
        return True
    detail = (result.stderr or result.stdout or "").lower()
    if "no such" in detail or "not found" in detail:
        return False
    raise _OrchestratorUnavailable(
        f"cannot inspect Docker {kind} {name!r}: {detail[-400:]}"
    )


def _optional_docker_image_id(
    reference: str, *, docker_env: dict[str, str] | None = None
) -> str | None:
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", reference],
            capture_output=True,
            text=True,
            timeout=30,
            env=docker_env,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise _OrchestratorUnavailable(
            f"cannot inspect Docker image {reference!r}: {exc}"
        ) from exc
    if result.returncode == 0:
        return result.stdout.strip()
    detail = (result.stderr or result.stdout or "").lower()
    if "no such" in detail or "not found" in detail:
        return None
    raise _OrchestratorUnavailable(
        f"cannot inspect Docker image {reference!r}: {detail[-400:]}"
    )


def _read_bundle_manifest(bundle_path: Path) -> dict[str, Any]:
    """Read ``dicepp-package.json`` from a Linux release bundle."""
    try:
        with zipfile.ZipFile(bundle_path, "r") as archive:
            raw = archive.read("dicepp-package.json")
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise _OrchestratorUnavailable(
            f"cannot read dicepp-package.json from bundle: {exc}"
        ) from exc
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _OrchestratorUnavailable("bundle manifest is invalid JSON") from exc
    if not isinstance(manifest, dict):
        raise _OrchestratorUnavailable("bundle manifest must be an object")
    return manifest


def _copy_validation_bundle(
    source: Path,
    destination: Path,
    manifest: dict[str, Any],
) -> None:
    """Copy final bytes, enabling only the isolated validation policy gate.

    A manual-migration candidate has ``automatic_upgrade=false`` in its final
    manifest, so the Manager correctly refuses to enter the upgrade adapter.
    The validation-only matrix is deliberately non-promotable: it derives a
    package inside the excluded Manager package directory with that single
    policy bit enabled, while retaining the final compose and image bytes.
    """
    automatic_upgrade = manifest.get("automatic_upgrade")
    if type(automatic_upgrade) is not bool:
        raise _OrchestratorUnavailable(
            "target bundle automatic_upgrade policy is not boolean"
        )
    if automatic_upgrade:
        shutil.copy2(source, destination)
        return
    patched_manifest = {
        **manifest,
        "automatic_upgrade": True,
        "linux_manager_handoff_protocol": 1,
    }
    patched_manifest_bytes = json.dumps(
        patched_manifest,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    try:
        with zipfile.ZipFile(source, "r") as source_archive:
            manifest_members = [
                info
                for info in source_archive.infolist()
                if info.filename == "dicepp-package.json"
            ]
            if len(manifest_members) != 1:
                raise _OrchestratorUnavailable(
                    "target bundle manifest member is not unique"
                )
            checksum_members = [
                info
                for info in source_archive.infolist()
                if info.filename == "checksums.sha256"
            ]
            if len(checksum_members) != 1:
                raise _OrchestratorUnavailable(
                    "target bundle checksum member is not unique"
                )
            checksums_raw = source_archive.read(checksum_members[0])
            checksums_lines = checksums_raw.decode("utf-8").splitlines()
            manifest_digest = hashlib.sha256(patched_manifest_bytes).hexdigest()
            manifest_checksum_replaced = False
            patched_checksum_lines: list[str] = []
            for line in checksums_lines:
                digest, separator, name = line.partition("  ")
                if separator and name == "dicepp-package.json":
                    line = f"{manifest_digest}  {name}"
                    manifest_checksum_replaced = True
                patched_checksum_lines.append(line)
            if not manifest_checksum_replaced:
                raise _OrchestratorUnavailable(
                    "target bundle checksums do not cover dicepp-package.json"
                )
            patched_checksums = (
                "\n".join(patched_checksum_lines) + "\n"
            ).encode("utf-8")
            with zipfile.ZipFile(destination, "x") as target_archive:
                target_archive.comment = source_archive.comment
                for info in source_archive.infolist():
                    if info.filename == "dicepp-package.json":
                        target_archive.writestr(info, patched_manifest_bytes)
                        continue
                    if info.filename == "checksums.sha256":
                        target_archive.writestr(info, patched_checksums)
                        continue
                    with source_archive.open(info, "r") as source_member:
                        with target_archive.open(
                            info,
                            "w",
                            force_zip64=True,
                        ) as target_member:
                            shutil.copyfileobj(
                                source_member,
                                target_member,
                                length=1024 * 1024,
                            )
    except (OSError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        raise _OrchestratorUnavailable(
            f"cannot derive validation-only target bundle: {exc}"
        ) from exc


def _image_ids_by_role(manifest: dict[str, Any]) -> dict[str, str]:
    """Map ``{role: image_id}`` from a bundle manifest's ``images`` records."""
    images = manifest.get("images")
    if not isinstance(images, list) or not images:
        raise _OrchestratorUnavailable("bundle manifest has no image records")
    result: dict[str, str] = {}
    for item in images:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("role"), str)
            or not item["role"]
            or not isinstance(item.get("image_id"), str)
            or not item["image_id"]
        ):
            raise _OrchestratorUnavailable(
                "bundle manifest image record is invalid"
            )
        result[item["role"]] = item["image_id"]
    return result


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Manager API client
# ---------------------------------------------------------------------------

class _ManagerApiClient:
    """Thin authenticated client for the Manager's local upgrade API."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:4091",
        *,
        requester: Callable[
            [str, str, str, dict[str, Any] | None],
            tuple[int, dict[str, Any]],
        ]
        | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token: str | None = None
        self._requester = requester

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.token:
            raise _OrchestratorUnavailable("Manager API token is not set")
        if self._requester is None:
            _status, payload = _http_json(
                method,
                f"{self.base_url}{path}",
                token=self.token,
                body=body,
            )
        else:
            _status, payload = self._requester(
                method, path, self.token, body
            )
        return payload

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/v1/health")

    def preview(self, version: str) -> dict[str, Any]:
        encoded = urllib.parse.quote(version, safe="")
        return self._request(
            "GET", f"/v1/upgrades/preview?version={encoded}"
        )

    def confirm(self, version: str, confirmation_token: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/upgrades/confirm",
            body={"version": version, "confirmation_token": confirmation_token},
        )

    def status(self) -> dict[str, Any]:
        return self._request("GET", "/v1/upgrades/status")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class _LinuxUpgradeOrchestrator:
    """Execute four-scenario cross-version upgrade contract on Linux/Docker."""

    def __init__(
        self,
        *,
        source_bundle: Path,
        source_version: str,
        target_bundle: Path,
        target_version: str,
        work_dir: Path,
        docker_env: dict[str, str] | None = None,
        daemon_sandbox: _DockerDaemonSandbox | None = None,
        use_socket_proxy: bool = True,
    ) -> None:
        self._source_bundle = source_bundle
        self.source_version = source_version
        self._target_bundle = target_bundle
        self.target_version = target_version
        self._work_dir = work_dir
        self._docker_env = dict(docker_env) if docker_env is not None else None
        self._daemon_sandbox = daemon_sandbox
        self._use_socket_proxy = use_socket_proxy
        self._compose_project = f"dicepp-upgrade-{uuid4().hex[:12]}"
        self._compose_file: Path | None = None
        self._compose_override: Path | None = None
        self._compose_started = False
        self._instance_dir: Path | None = None
        # The shipped compose interpolates ``:${DICEPP_IMAGE_TAG}``; the
        # bundle archives are tagged with the release tag (``v`` prefix).
        self._image_tag = "v" + source_version.removeprefix("v")
        self._source_image_ids: dict[str, str] = {}
        self._target_image_ids: dict[str, str] = {}
        self._owned_image_refs: set[str] = set()
        self._container_names: dict[str, str] = {}
        self._owns_dice_network = False
        self._network_id: str | None = None
        self._network_identity = uuid4().hex
        self._seeded_bundle_path: Path | None = None
        self._api: _ManagerApiClient | None = None
        self._docker_proxy: _DockerSocketProxy | None = None
        self._sentinel_digests: dict[str, str] = {}
        self._sentinel_original_bytes: dict[str, bytes] = {}
        self._sentinels_mutated = False
        self._handoff_mode = "passthrough"
        self._harness_control_dir: Path | None = None
        self._source_manager_identity: dict[str, Any] = {}
        self._transaction_ids: set[str] = set()
        self._dashboard_db_expected = "source"

    # -- scenario entry points -----------------------------------------------

    def healthy_commit(self) -> dict[str, Any]:
        scenario = "healthy_commit"
        try:
            self._prepare_compose()
            self._start_source()
            self._verify_source_healthy()
            self._trigger_upgrade(scenario)
            self._wait_upgrade_complete(scenario)
            self._verify_target_healthy(scenario)
            self._verify_sentinels(scenario)
            journal = self._read_journal()
            journal_status = journal.get("status", "committed")
            if journal_status != "committed":
                raise self._expectation_failure(
                    scenario,
                    f"journal status is {journal_status!r}; expected committed",
                )
        except _ScenarioExpectationFailure as exc:
            return self._scenario_failed(scenario, exc)
        return _build_scenario_result(
            self,
            scenario,
            assertions={
                "source_started": True,
                "target_started": True,
                "local_health_passed": True,
                "journal_committed": True,
            },
            observations={
                "source_version_before": self.source_version,
                "target_version_after": self.target_version,
                "journal_status": journal_status,
                "health_status": "healthy",
            },
        )

    def target_health_failure_rollback(self) -> dict[str, Any]:
        scenario = "target_health_failure_rollback"
        try:
            self._prepare_compose()
            self._start_source()
            self._verify_source_healthy()
            self._trigger_upgrade(scenario)
            self._inject_health_failure(scenario)
            self._wait_rollback_complete(scenario)
            journal = self._read_journal()
            journal_status = self._verify_rollback_journal(scenario, journal)
            rollback_program_status = self._rollback_program_status(journal)
            self._verify_source_restored(scenario)
            self._verify_sentinels(scenario, require_mutation=True)
        except _ScenarioExpectationFailure as exc:
            return self._scenario_failed(scenario, exc)
        return _build_scenario_result(
            self,
            scenario,
            assertions={
                "target_executed": True,
                "health_failure_injected": True,
                "program_restored": True,
                "data_restored": True,
                "source_restarted": True,
                "journal_rolled_back": True,
            },
            observations={
                "target_version_observed": self.target_version,
                "restored_version": self.source_version,
                "journal_status": journal_status,
                "rollback_marker_status": rollback_program_status,
            },
        )

    def retry_after_rollback(self) -> dict[str, Any]:
        scenario = "retry_after_rollback"
        try:
            self._prepare_compose()
            self._start_source()
            self._verify_source_healthy()
            # First attempt: inject failure → rollback.
            self._trigger_upgrade(scenario)
            self._inject_health_failure(scenario)
            self._wait_rollback_complete(scenario)
            first_journal = self._read_journal()
            self._verify_rollback_journal(scenario, first_journal)
            self._verify_source_restored(scenario)
            self._verify_sentinels(scenario, require_mutation=True)
            # Retry on the same instance.
            self._trigger_upgrade(scenario)
            self._wait_upgrade_complete(scenario)
            self._verify_target_healthy(scenario)
            self._verify_sentinels(scenario, require_mutation=True)
            journal = self._read_journal()
            journal_status = journal.get("status", "committed")
            if journal_status != "committed":
                raise self._expectation_failure(
                    scenario,
                    f"journal status is {journal_status!r}; expected committed",
                )
        except _ScenarioExpectationFailure as exc:
            return self._scenario_failed(scenario, exc)
        return _build_scenario_result(
            self,
            scenario,
            assertions={
                "prior_rollback_observed": True,
                "retry_started_same_instance": True,
                "target_started": True,
                "journal_committed": True,
            },
            observations={
                "first_transaction_status": "rolled_back",
                "retry_transaction_status": journal_status,
                "final_version": self.target_version,
            },
        )

    def apply_failure_before_target_execution(self) -> dict[str, Any]:
        scenario = "apply_failure_before_target_execution"
        try:
            self._prepare_compose()
            self._start_source()
            self._verify_source_healthy()
            if self._docker_proxy is None:
                raise _OrchestratorUnavailable("Docker socket proxy is not prepared")
            self._docker_proxy.arm_container_create_failure()
            self._trigger_upgrade(scenario)
            self._wait_apply_failure(scenario)
            journal = self._read_journal()
            journal_status = self._verify_rollback_journal(scenario, journal)
            self._verify_source_restored(scenario)
            self._verify_sentinels(scenario, require_mutation=True)
            target_start_count = self._target_process_start_count()
            apply_status = self._docker_proxy.failure_status
            if (
                self._docker_proxy.failure_count != 1
                or apply_status is None
                or target_start_count != 0
            ):
                raise self._expectation_failure(
                    scenario,
                    "the Docker failpoint did not reject exactly one target create",
                    target_process_start_count=target_start_count,
                    journal_status=journal_status,
                    apply_exit_code=apply_status or -1,
                )
        except _ScenarioExpectationFailure as exc:
            return self._scenario_failed(scenario, exc)
        return _build_scenario_result(
            self,
            scenario,
            assertions={
                "apply_failure_injected": True,
                "target_never_executed": True,
                "source_remained_or_restored": True,
                "no_target_migration": True,
                "terminal_state_recorded": True,
            },
            observations={
                "target_process_start_count": target_start_count,
                "source_version_after": self.source_version,
                "journal_status": journal_status,
                "apply_exit_code": apply_status,
            },
        )

    def linux_manager_handoff_success(self) -> dict[str, Any]:
        """Exercise a real source-Updater-target Manager handoff commit."""
        scenario = "manager_handoff_commit"
        self._handoff_mode = "passthrough"
        try:
            self._prepare_compose()
            self._start_source()
            self._verify_source_healthy()
            self._trigger_upgrade(scenario)
            request, _tx_dir = self._wait_handoff_document("request")
            decision, _tx_dir = self._wait_handoff_document(
                "decision", transaction_id=request["transaction_id"]
            )
            self._verify_handoff_document_binding(
                scenario, decision, request, kind="decision"
            )
            if decision.get("value") != "commit":
                raise self._expectation_failure(
                    scenario,
                    "target Manager did not publish the commit decision",
                )
            self._wait_upgrade_complete(scenario)
            self._verify_handoff_target_objects(scenario, request)
            journal = self._read_journal()
            self._verify_journal_binding(scenario, journal, request)
            if journal.get("status") != "committed":
                raise self._expectation_failure(
                    scenario,
                    "handoff journal is not committed",
                    journal_status=journal.get("status", "unavailable"),
                )
            protocol = _read_bundle_manifest(self._target_bundle).get(
                "linux_manager_handoff_protocol"
            )
            if protocol != 1:
                raise self._expectation_failure(
                    scenario,
                    "target bundle does not declare Linux Manager handoff v1",
                )
        except _ScenarioExpectationFailure as exc:
            return self._scenario_failed(scenario, exc)
        return _build_scenario_result(
            self,
            scenario,
            assertions={
                "manager_handoff_completed": True,
                "target_containers_started": True,
                "local_health_passed": True,
                "commit_decision_written": True,
            },
            observations={
                "source_version_before": self.source_version,
                "target_version_after": self.target_version,
                "handoff_protocol": "1",
                "journal_status": "committed",
                "health_status": "healthy",
            },
        )

    def linux_manager_handoff_target_crash(self) -> dict[str, Any]:
        """Crash the real target Manager container and prove source restore."""
        scenario = "manager_handoff_rollback"
        self._handoff_mode = "target_crash"
        try:
            self._prepare_compose()
            self._start_source()
            self._verify_source_healthy()
            self._trigger_upgrade(scenario)
            request, _tx_dir = self._wait_handoff_document("request")
            observed = self._wait_control_document("target-manager-observed.json")
            if (
                observed.get("version") != self.target_version
                or observed.get("mode") != "target_crash"
            ):
                raise self._expectation_failure(
                    scenario, "the target Manager crash marker is invalid"
                )
            self._sentinels_mutated = self._sentinels_differ_from_source()
            result, _tx_dir = self._wait_handoff_document(
                "result", transaction_id=request["transaction_id"]
            )
            self._verify_handoff_document_binding(
                scenario, result, request, kind="result"
            )
            if result.get("value") != "source-restored":
                raise self._expectation_failure(
                    scenario,
                    "Updater did not restore the source Manager",
                    result_status=result.get("value", "unavailable"),
                )
            self._wait_rollback_complete(scenario)
            self._verify_handoff_source_objects(scenario, request)
            self._verify_sentinels(scenario, require_mutation=True)
            self._verify_dashboard_db_source(scenario)
            journal = self._read_journal()
            self._verify_journal_binding(scenario, journal, request)
            self._verify_handoff_rollback_journal(scenario, journal)
        except _ScenarioExpectationFailure as exc:
            return self._scenario_failed(scenario, exc)
        return _build_scenario_result(
            self,
            scenario,
            assertions={
                "target_manager_failed": True,
                "source_manager_restored": True,
                "program_restored": True,
                "data_restored": True,
                "dashboard_db_restored": True,
                "source_restarted": True,
                "journal_rolled_back": True,
            },
            observations={
                "target_version_observed": self.target_version,
                "restored_version": self.source_version,
                "result_status": "source-restored",
                "journal_status": "rolled_back",
                "rollback_marker_status": "restored",
            },
        )

    def linux_manager_handoff_daemon_restart(self) -> dict[str, Any]:
        """Exercise both sides of the commit point across an isolated restart."""
        scenario = "manager_handoff_commit_crash_window"
        if self._daemon_sandbox is None:
            raise _OrchestratorUnavailable(
                "daemon restart scenario requires the isolated Docker sandbox"
            )
        before = self._fork_daemon_case("before-commit", "daemon_before_commit")
        after = self._fork_daemon_case("after-commit", "daemon_after_commit")
        try:
            try:
                before_state = before._run_daemon_restart_before_commit(scenario)
            finally:
                before.cleanup()
            try:
                after_state = after._run_daemon_restart_after_commit(scenario)
            finally:
                after.cleanup()
        except _ScenarioExpectationFailure as exc:
            return self._scenario_failed(scenario, exc)
        manual_after = after_state == "cleanup_pending"
        assertions = {
            "crash_before_commit_allowed_source_restore": (
                before_state == "source_restored"
            ),
            "crash_after_commit_never_rolled_back": manual_after,
            "recovery_material_preserved": manual_after,
            "terminal_state_recorded": manual_after,
        }
        if not all(assertions.values()):
            failure = self._expectation_failure(
                scenario,
                "daemon restart cases did not converge across the commit point",
                assertion_values=assertions,
                crash_before_commit_final_state=before_state,
                crash_after_commit_final_state=after_state,
                decision_status=(
                    "committed"
                    if after_state == "cleanup_pending"
                    else "unknown"
                ),
            )
            return self._scenario_failed(scenario, failure)
        return _build_scenario_result(
            self,
            scenario,
            assertions={
                "crash_before_commit_allowed_source_restore": True,
                "crash_after_commit_never_rolled_back": True,
                "recovery_material_preserved": True,
                "terminal_state_recorded": True,
            },
            observations={
                "crash_before_commit_final_state": "source_restored",
                "crash_after_commit_final_state": "cleanup_pending",
                "decision_status": "committed",
            },
        )

    # -- scenario failure helpers --------------------------------------------

    def _expectation_failure(
        self,
        scenario: str,
        message: str,
        *,
        assertion_values: dict[str, bool] | None = None,
        **observation_values: Any,
    ) -> _ScenarioExpectationFailure:
        observations: dict[str, Any] = {
            key: "unavailable" for key in SCENARIO_OBSERVATION_FIELDS[scenario]
        }
        observations.update(observation_values)
        observations["reason"] = message
        return _ScenarioExpectationFailure(
            message,
            assertions=(
                dict(assertion_values)
                if assertion_values is not None
                else {key: False for key in SCENARIO_ASSERTIONS[scenario]}
            ),
            observations=observations,
        )

    def _scenario_failed(
        self,
        scenario: str,
        failure: _ScenarioExpectationFailure,
    ) -> dict[str, Any]:
        return {
            "contract_version": 1,
            "platform": "linux",
            "arch": "amd64",
            "source_version": self.source_version,
            "target_version": self.target_version,
            "scenario": scenario,
            "status": "failed",
            "assertions": failure.assertions,
            "observations": failure.observations,
        }

    # -- instance bootstrapping ----------------------------------------------

    def _prepare_compose(self) -> None:
        """Bootstrap a full instance and seed the Manager's verified release."""
        self._work_dir.mkdir(parents=True, exist_ok=True)
        self._instance_dir = self._work_dir / "instance"
        self._instance_dir.mkdir(parents=True, exist_ok=True)
        instance = self._instance_dir

        # 1. Instance layout directories (config/, data/, content/, manager/).
        for relative in (
            "config",
            "data",
            "content",
            "dashboard/data",
            "manager/state",
        ):
            (instance / relative).mkdir(parents=True, exist_ok=True)
        packages_dir = instance / "manager" / "packages" / self.target_version
        packages_dir.mkdir(parents=True, exist_ok=True)

        # 2. Minimal global config: prerelease channel, no network discovery.
        (instance / "config" / "global.json").write_text(
            json.dumps(
                {"update": {"channel": "prerelease", "discovery_enabled": False}}
            ),
            encoding="utf-8",
        )
        # Catalog-owned sentinels make rollback assertions observable.  They
        # deliberately use regular-backup assets instead of arbitrary files.
        user_sentinel = instance / "config" / "user.json"
        user_sentinel.write_text(
            "{}\n",
            encoding="utf-8",
        )
        image_sentinel = instance / "data" / "local_images" / "sentinel.bin"
        image_sentinel.parent.mkdir(parents=True, exist_ok=True)
        image_sentinel.write_bytes(os.urandom(64))
        self._sentinel_digests = {
            user_sentinel.relative_to(instance).as_posix(): _sha256_file(user_sentinel),
            image_sentinel.relative_to(instance).as_posix(): _sha256_file(image_sentinel),
        }
        self._sentinel_original_bytes = {
            relative: (instance / relative).read_bytes()
            for relative in self._sentinel_digests
        }
        dashboard_db = instance / "dashboard" / "data" / "dashboard.db"
        with closing(sqlite3.connect(dashboard_db)) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS dicepp_harness_sentinel "
                "(id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT OR REPLACE INTO dicepp_harness_sentinel(id, value) "
                "VALUES (1, ?)",
                (self._dashboard_db_expected,),
            )
            connection.commit()

        self._harness_control_dir = instance / "manager" / "harness"
        self._harness_control_dir.mkdir(parents=True, exist_ok=True)
        wrapper = self._harness_control_dir / "manager_entrypoint.py"
        wrapper.write_text(_MANAGER_HARNESS_WRAPPER, encoding="utf-8")

        # 3. Compose: deploy the supported source release's exact topology.
        # A separate harness-only override replaces only the Manager's Docker
        # socket with our transparent fault-injection proxy.
        try:
            with zipfile.ZipFile(self._source_bundle, "r") as archive:
                shipped_bytes = archive.read("docker-compose.yml")
        except (KeyError, OSError, zipfile.BadZipFile) as exc:
            raise _OrchestratorUnavailable(
                f"cannot read compose from target bundle: {exc}"
            ) from exc
        try:
            shipped_compose = shipped_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _OrchestratorUnavailable(
                "cannot decode compose from target bundle"
            ) from exc
        _minimal_test_compose(shipped_compose, instance, self.source_version)
        self._compose_file = instance / "docker-compose.yml"
        self._compose_file.write_bytes(shipped_bytes)
        self._compose_override = instance / "docker-compose.harness.yml"
        override_lines = [
            "services:",
            "  manager:",
            "    command: [\"python\", \"/app/manager/harness/manager_entrypoint.py\"]",
            "    environment:",
            f"      DICEPP_HANDOFF_HARNESS_MODE: {json.dumps(self._handoff_mode)}",
            f"      DICEPP_HANDOFF_HARNESS_TARGET: {json.dumps(self.target_version)}",
        ]
        if self._use_socket_proxy:
            proxy_path = Path(tempfile.gettempdir()) / (
                f"dicepp-upgrade-{uuid4().hex}.sock"
            )
            self._docker_proxy = _DockerSocketProxy(
                proxy_path,
                on_create_failure=self._mutate_sentinels_for_failure,
            )
            override_lines.extend(
                [
                    "    volumes:",
                    "      - type: bind",
                    f"        source: {json.dumps(proxy_path.resolve().as_posix())}",
                    "        target: /var/run/docker.sock",
                ]
            )
        self._compose_override.write_text(
            "\n".join(override_lines) + "\n",
            encoding="utf-8",
        )

        # 4. Seed the target release for the verified-package path: bundle
        #    copy, verified metadata, and cached release state.
        target_manifest = _read_bundle_manifest(self._target_bundle)
        self._target_image_ids = _image_ids_by_role(target_manifest)
        self._source_image_ids = _image_ids_by_role(
            _read_bundle_manifest(self._source_bundle)
        )
        bundle_filename = self._target_bundle.name
        self._seeded_bundle_path = packages_dir / bundle_filename
        _copy_validation_bundle(
            self._target_bundle,
            self._seeded_bundle_path,
            target_manifest,
        )
        seeded_manifest = _read_bundle_manifest(self._seeded_bundle_path)
        bundle_size = self._seeded_bundle_path.stat().st_size
        bundle_digest = _sha256_file(self._seeded_bundle_path)
        self._write_seeded_release(
            instance,
            packages_dir,
            seeded_manifest,
            bundle_filename,
            bundle_size,
            bundle_digest,
        )

        # 5. Manager API client (the token is read after the Manager starts).
        # A nested daemon owns a different loopback namespace, so API calls
        # are executed from inside the exact Manager container in that case.
        requester = (
            self._sandbox_manager_api_request
            if self._daemon_sandbox is not None
            else None
        )
        self._api = _ManagerApiClient(
            base_url="http://127.0.0.1:4091",
            requester=requester,
        )

    def _write_seeded_release(
        self,
        instance: Path,
        packages_dir: Path,
        manifest: dict[str, Any],
        filename: str,
        size: int,
        digest: str,
    ) -> None:
        """Write ``release-state.json`` + ``verified-release.json``.

        The schema matches ``dicepp_manager.release._validate_cached_latest``
        and the Manager's ``_package_from_release`` verified-package path.
        """
        try:
            compatibility = {
                "deployment_schema_version": manifest[
                    "deployment_schema_version"
                ],
                "minimum_manager_version": manifest["minimum_manager_version"],
                "catalog_version": manifest["catalog_version"],
                "catalog_digest": manifest["catalog_digest"],
                "automatic_upgrade": manifest["automatic_upgrade"],
                "problems": [],
            }
            change_scope = [str(item) for item in manifest["change_scope"]]
            handoff_protocol = manifest.get("linux_manager_handoff_protocol")
        except (KeyError, TypeError, ValueError) as exc:
            raise _OrchestratorUnavailable(
                f"target bundle manifest is incomplete: {exc}"
            ) from exc
        artifact = {
            "platform": "linux",
            "arch": "amd64",
            "filename": filename,
            "purpose": "linux-bundle",
            "size": size,
            "sha256": digest,
        }
        published_at = "2026-01-01T00:00:00+00:00"
        available = {
            "version": self.target_version,
            "channel": "prerelease",
            "change_scope": change_scope,
            "linux_manager_handoff_protocol": handoff_protocol,
            "compatible": True,
            "compatibility": compatibility,
            "release_url": (
                f"https://github.com/pear-studio/nonebot-dicepp/"
                f"releases/tag/v{self.target_version}"
            ),
            "published_at": published_at,
            "artifacts": [
                {
                    **artifact,
                    "download_url": (
                        f"https://github.com/pear-studio/nonebot-dicepp/"
                        f"releases/download/v{self.target_version}/{filename}"
                    ),
                }
            ],
        }
        release_state = {
            "format_version": 1,
            "channel": "prerelease",
            "available": available,
            "discovery": {
                "status": "idle",
                "last_checked": None,
                "channel": "prerelease",
                "error": None,
                "candidate_errors": [],
            },
            "download": {"status": "idle"},
        }
        verified = {
            "contract_version": 2,
            "version": self.target_version,
            "channel": "prerelease",
            "change_scope": change_scope,
            "compatibility": compatibility,
            "artifact": artifact,
            "generation": None,
            "verified_path": filename,
            "bundle_manifest": None,
            "payload_verified_path": None,
            "completed_at": published_at,
        }
        manager_state = instance / "manager" / "state"
        (manager_state / "release-state.json").write_text(
            json.dumps(release_state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (packages_dir / "verified-release.json").write_text(
            json.dumps(verified, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    # -- Docker / Compose helpers --------------------------------------------

    def _effective_docker_env(
        self, extra: dict[str, str] | None = None
    ) -> dict[str, str]:
        env = dict(self._docker_env) if self._docker_env is not None else dict(os.environ)
        if extra:
            env.update(extra)
        return env

    def _docker_cmd(
        self,
        *args: str,
        timeout: float = 60,
        cwd: str | os.PathLike[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return _docker(
            "docker",
            *args,
            timeout=timeout,
            cwd=cwd,
            env=env if env is not None else self._effective_docker_env(),
        )

    def _sandbox_manager_api_request(
        self,
        method: str,
        path: str,
        token: str,
        body: dict[str, Any] | None,
    ) -> tuple[int, dict[str, Any]]:
        if self._daemon_sandbox is None:
            raise _OrchestratorUnavailable("isolated Docker daemon is unavailable")
        manager_name = self._container_names.get("manager")
        if not manager_name:
            raise _OrchestratorUnavailable("Manager container is not identified")
        return self._daemon_sandbox.manager_api_request(
            manager_name, method, path, token, body
        )

    def _compose(
        self,
        *args: str,
        env: dict[str, str] | None = None,
        timeout: float = 180,
    ) -> None:
        if self._compose_file is None:
            raise _OrchestratorUnavailable("compose file not prepared")
        compose_files = ["-f", str(self._compose_file)]
        if self._compose_override is not None:
            compose_files.extend(["-f", str(self._compose_override)])
        self._docker_cmd(
            "compose",
            "-p",
            self._compose_project,
            *compose_files,
            *args,
            timeout=timeout,
            cwd=self._work_dir,
            env=env,
        )

    def _start_source(self) -> None:
        if self._api is None:
            raise _OrchestratorUnavailable("manager api client is not prepared")
        if self._use_socket_proxy:
            if self._docker_proxy is None:
                raise _OrchestratorUnavailable("Docker socket proxy is not prepared")
            self._docker_proxy.start()
        self._assert_isolated_docker_namespace()
        network = self._docker_cmd(
            "network",
            "create",
            "--label",
            f"io.dicepp.upgrade-harness-network={self._network_identity}",
            "dice-net",
        )
        network_id = network.stdout.strip()
        if not network_id or any(
            character not in "0123456789abcdef" for character in network_id
        ):
            raise _OrchestratorUnavailable(
                "Docker did not return the owned dice-net network id"
            )
        self._network_id = network_id
        self._owns_dice_network = True
        self._load_images()
        env = self._effective_docker_env({"DICEPP_IMAGE_TAG": self._image_tag})
        self._compose_started = True
        self._compose("up", "-d", "--wait", env=env)
        if self._instance_dir is None:
            raise _OrchestratorUnavailable("instance directory is not prepared")
        token_path = self._instance_dir / "manager" / "state" / "api-token"
        try:
            token = token_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise _OrchestratorUnavailable(
                f"manager api token is unavailable: {exc}"
            ) from exc
        if not token:
            raise _OrchestratorUnavailable("manager api token is empty")
        self._api.token = token
        self._container_names = {
            role: self._find_container_name(role)
            for role in ("bot", "dashboard", "manager")
        }
        manager = self._container_state("manager")
        self._source_manager_identity = dict(manager)

    def _load_images(self) -> None:
        for label, bundle in (
            ("source", self._source_bundle),
            ("target", self._target_bundle),
        ):
            manifest = _read_bundle_manifest(bundle)
            for item in manifest.get("images", []):
                if not isinstance(item, dict) or not isinstance(
                    item.get("reference"), str
                ):
                    raise _OrchestratorUnavailable(
                        f"{label} bundle image reference is invalid"
                    )
                reference = item["reference"]
                expected_id = item.get("image_id")
                existing = _optional_docker_image_id(
                    reference, docker_env=self._effective_docker_env()
                )
                if existing is not None and existing != expected_id:
                    raise _OrchestratorUnavailable(
                        f"image reference {reference!r} already points to unrelated bytes"
                    )
                if existing is None:
                    self._owned_image_refs.add(reference)
            images = _load_docker_image_from_bundle(
                bundle,
                self._work_dir,
                label,
                docker_env=self._effective_docker_env(),
            )
            if set(images) != {"bot", "dashboard"}:
                raise _OrchestratorUnavailable(
                    f"{label} bundle does not contain bot and dashboard images"
                )

    def _find_container_name(self, service: str) -> str:
        result = self._docker_cmd(
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={self._compose_project}",
            "--filter",
            f"label=com.docker.compose.service={service}",
            "--format",
            "{{.Names}}",
        )
        name = result.stdout.strip()
        if not name:
            raise _OrchestratorUnavailable(
                f"managed {service} container was not found"
            )
        return name

    def _assert_isolated_docker_namespace(self) -> None:
        for name in ("dicepp", "dicepp-dashboard", "dicepp-manager"):
            if _docker_object_exists(
                "container", name, docker_env=self._effective_docker_env()
            ):
                raise _OrchestratorUnavailable(
                    f"Docker container {name!r} already exists; refusing to touch a shared instance"
                )
        if _docker_object_exists(
            "network", "dice-net", docker_env=self._effective_docker_env()
        ):
            raise _OrchestratorUnavailable(
                "Docker network 'dice-net' already exists; refusing to join a shared instance"
            )

    def _container_state(self, role: str) -> dict[str, Any]:
        name = self._container_names.get(role)
        if not name:
            raise _OrchestratorUnavailable(f"{role} container is not started")
        result = self._docker_cmd(
            "inspect", "--format", "{{json .}}", name
        )
        try:
            payload = json.loads(result.stdout)
            state = payload["State"]
            image_id = payload["Image"]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise _OrchestratorUnavailable(
                f"cannot read {role} container state: {exc}"
            ) from exc
        config = payload.get("Config")
        host_config = payload.get("HostConfig")
        labels = config.get("Labels") if isinstance(config, dict) else {}
        restart = (
            host_config.get("RestartPolicy")
            if isinstance(host_config, dict)
            else {}
        )
        return {
            "container_id": payload.get("Id"),
            "name": str(payload.get("Name") or name).removeprefix("/"),
            "image_id": image_id,
            "running": state.get("Running") is True,
            "status": state.get("Status"),
            "started_at": state.get("StartedAt"),
            "labels": labels if isinstance(labels, dict) else {},
            "restart_policy": (
                restart.get("Name") if isinstance(restart, dict) else None
            ),
        }

    def _bot_image_id(self) -> str:
        return str(self._container_state("bot")["image_id"])

    def _wait_health(
        self, expected_version: str, timeout: float = 120
    ) -> None:
        if self._api is None:
            raise _OrchestratorUnavailable("manager api client is not prepared")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                health = self._api.health()
            except (_OrchestratorUnavailable, _ManagerApiError):
                time.sleep(2)
                continue
            if health.get("dicepp_version") == expected_version:
                return
            time.sleep(2)
        raise _OrchestratorUnavailable(
            f"manager health did not report {expected_version!r} "
            f"within {timeout}s"
        )

    def _verify_source_healthy(self) -> None:
        self._wait_health(self.source_version, timeout=120)
        for role in ("bot", "dashboard"):
            state = self._container_state(role)
            if not state["running"]:
                raise _OrchestratorUnavailable(
                    f"source {role} container is not running: {state['status']!r}"
                )
            if state["image_id"] != self._source_image_ids.get(role):
                raise _OrchestratorUnavailable(
                    f"{role} image {state['image_id']!r} differs from the source bundle manifest"
                )

    def _verify_target_healthy(self, scenario: str) -> None:
        # The Manager container itself is never switched, so /v1/health keeps
        # reporting the source version after a successful upgrade.
        self._wait_health(self.source_version, timeout=120)
        for role in ("bot", "dashboard"):
            state = self._container_state(role)
            if not state["running"] or state["image_id"] != self._target_image_ids.get(
                role
            ):
                raise self._expectation_failure(
                    scenario,
                    f"target {role} is not running from the expected image",
                )

    def _verify_source_restored(self, scenario: str) -> None:
        self._wait_health(self.source_version, timeout=60)
        for role in ("bot", "dashboard"):
            state = self._container_state(role)
            if not state["running"] or state["image_id"] != self._source_image_ids.get(
                role
            ):
                raise self._expectation_failure(
                    scenario,
                    f"source {role} was not restored as a running container",
                )

    def _wait_handoff_document(
        self,
        kind: str,
        *,
        transaction_id: str | None = None,
        timeout: float = 300,
    ) -> tuple[dict[str, Any], Path]:
        if self._instance_dir is None or kind not in _HANDOFF_FILENAMES:
            raise _OrchestratorUnavailable("handoff recovery root is unavailable")
        recovery_root = self._instance_dir / "manager" / "recovery"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            candidates = (
                [recovery_root / transaction_id]
                if transaction_id is not None
                else sorted(recovery_root.glob("*"))
            )
            for tx_dir in candidates:
                if not tx_dir.is_dir() or tx_dir.is_symlink():
                    continue
                path = tx_dir / _HANDOFF_FILENAMES[kind]
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (FileNotFoundError, PermissionError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                observed_id = payload.get("transaction_id")
                if (
                    not isinstance(observed_id, str)
                    or observed_id != tx_dir.name
                    or (transaction_id is not None and observed_id != transaction_id)
                ):
                    continue
                self._transaction_ids.add(observed_id)
                return payload, tx_dir
            time.sleep(0.1)
        raise _OrchestratorUnavailable(
            f"Linux handoff {kind} document did not appear within {timeout}s"
        )

    def _wait_control_document(
        self, name: str, *, timeout: float = 180
    ) -> dict[str, Any]:
        if self._harness_control_dir is None:
            raise _OrchestratorUnavailable("handoff control directory is unavailable")
        path = self._harness_control_dir / name
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, PermissionError, json.JSONDecodeError):
                time.sleep(0.1)
                continue
            if isinstance(payload, dict):
                return payload
            time.sleep(0.1)
        raise _OrchestratorUnavailable(
            f"handoff control marker {name!r} did not appear within {timeout}s"
        )

    def _sentinels_differ_from_source(self) -> bool:
        if self._instance_dir is None:
            return False
        return all(
            (self._instance_dir / relative).is_file()
            and _sha256_file(self._instance_dir / relative) != expected
            for relative, expected in self._sentinel_digests.items()
        )

    def _verify_dashboard_db_source(self, scenario: str) -> None:
        if self._instance_dir is None:
            raise _OrchestratorUnavailable("instance directory is unavailable")
        database = self._instance_dir / "dashboard" / "data" / "dashboard.db"
        try:
            with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
                row = connection.execute(
                    "SELECT value FROM dicepp_harness_sentinel WHERE id = 1"
                ).fetchone()
        except sqlite3.Error as exc:
            raise self._expectation_failure(
                scenario, f"Dashboard database cannot be verified: {exc}"
            ) from exc
        if row != (self._dashboard_db_expected,):
            raise self._expectation_failure(
                scenario, "Dashboard database snapshot was not restored"
            )

    def _verify_aliases(
        self,
        scenario: str,
        aliases: dict[str, Any],
        *,
        target: bool,
    ) -> None:
        expected_by_role = (
            {
                "bot": self._target_image_ids["bot"],
                "dashboard_manager": self._target_image_ids["dashboard"],
            }
            if target
            else {
                role: record["image_id"]
                for role, record in aliases.items()
                if isinstance(record, dict) and "image_id" in record
            }
        )
        for role, record in aliases.items():
            if not isinstance(record, dict) or role not in expected_by_role:
                raise self._expectation_failure(scenario, "current alias contract is invalid")
            reference = record.get("name")
            if not isinstance(reference, str):
                raise self._expectation_failure(scenario, "current alias name is invalid")
            actual = _optional_docker_image_id(
                reference, docker_env=self._effective_docker_env()
            )
            if actual != expected_by_role[role]:
                raise self._expectation_failure(
                    scenario, f"current alias {reference!r} points at unexpected bytes"
                )

    def _verify_handoff_target_objects(
        self, scenario: str, request: dict[str, Any]
    ) -> None:
        self._wait_health(self.target_version, timeout=180)
        transaction_id = request["transaction_id"]
        manager = self._container_state("manager")
        if (
            manager["container_id"] == request["manager"]["container_id"]
            or manager["image_id"] != request["target_manager_image_id"]
            or manager["running"] is not True
            or manager["labels"].get("io.dicepp.upgrade-transaction")
            != transaction_id
            or manager["labels"].get("io.dicepp.upgrade-role") != "manager"
        ):
            raise self._expectation_failure(
                scenario, "target Manager Docker identity is not proven"
            )
        for role in ("bot", "dashboard"):
            state = self._container_state(role)
            if (
                state["image_id"] != self._target_image_ids[role]
                or state["running"] is not True
                or state["labels"].get("io.dicepp.upgrade-transaction")
                != transaction_id
            ):
                raise self._expectation_failure(
                    scenario, f"target {role} Docker identity is not proven"
                )
        self._verify_aliases(
            scenario, request["current_aliases"], target=True
        )
        if _docker_object_exists(
            "container",
            request["manager"]["backup_name"],
            docker_env=self._effective_docker_env(),
        ):
            raise self._expectation_failure(
                scenario, "source Manager backup still exists after commit"
            )

    def _verify_handoff_source_objects(
        self, scenario: str, request: dict[str, Any]
    ) -> None:
        self._wait_health(self.source_version, timeout=180)
        manager = self._container_state("manager")
        if (
            manager["container_id"] != request["manager"]["container_id"]
            or manager["image_id"] != request["manager"]["image_id"]
            or manager["running"] is not True
        ):
            raise self._expectation_failure(
                scenario, "the exact source Manager container was not restored"
            )
        for role in ("bot", "dashboard"):
            state = self._container_state(role)
            if (
                state["image_id"] != self._source_image_ids[role]
                or state["running"] is not True
            ):
                raise self._expectation_failure(
                    scenario, f"source {role} Docker object was not restored"
                )
        self._verify_aliases(
            scenario, request["current_aliases"], target=False
        )

    def _verify_handoff_rollback_journal(
        self, scenario: str, journal: dict[str, Any]
    ) -> None:
        detail = journal.get("detail")
        if (
            journal.get("status") != "rolled_back"
            or journal.get("phase") != "rolled_back"
            or not isinstance(detail, dict)
            or detail.get("rolled_back") is not True
            or detail.get("rollback_status") != "succeeded"
        ):
            raise self._expectation_failure(
                scenario,
                "journal does not prove the Linux handoff rollback",
                journal_status=journal.get("status", "unavailable"),
            )

    def _verify_handoff_document_binding(
        self,
        scenario: str,
        payload: dict[str, Any],
        request: dict[str, Any],
        *,
        kind: str,
    ) -> None:
        if (
            payload.get("transaction_id") != request.get("transaction_id")
            or payload.get("operation_id") != request.get("operation_id")
        ):
            raise self._expectation_failure(
                scenario, f"handoff {kind} is not bound to the observed request"
            )

    def _verify_journal_binding(
        self,
        scenario: str,
        journal: dict[str, Any],
        request: dict[str, Any],
    ) -> None:
        if (
            journal.get("transaction_id") != request.get("transaction_id")
            or journal.get("operation_id") != request.get("operation_id")
        ):
            raise self._expectation_failure(
                scenario, "Manager journal is not bound to the handoff request"
            )

    def _wait_optional_handoff_document(
        self,
        kind: str,
        request: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any] | None:
        try:
            payload, _ = self._wait_handoff_document(
                kind,
                transaction_id=str(request["transaction_id"]),
                timeout=timeout,
            )
        except _OrchestratorUnavailable as exc:
            if "did not appear within" in str(exc):
                return None
            raise
        return payload

    def _inspect_container_reference(self, reference: str) -> dict[str, Any]:
        try:
            payload = json.loads(
                self._docker_cmd(
                    "inspect", "--format", "{{json .}}", reference
                ).stdout
            )
        except (json.JSONDecodeError, TypeError) as exc:
            raise _OrchestratorUnavailable(
                f"cannot inspect handoff container {reference}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise _OrchestratorUnavailable(
                f"handoff container {reference} inspect is not an object"
            )
        return payload

    def _verify_post_commit_recovery_material(
        self, scenario: str, request: dict[str, Any]
    ) -> None:
        target_id = self._find_handoff_container(request, "manager")
        target = self._inspect_container_reference(target_id)
        target_config = target.get("Config")
        target_labels = (
            target_config.get("Labels") if isinstance(target_config, dict) else {}
        )
        if (
            target.get("Id") != target_id
            or target.get("Image") != request.get("target_manager_image_id")
            or not isinstance(target_labels, dict)
            or target_labels.get("io.dicepp.upgrade-transaction")
            != request.get("transaction_id")
            or target_labels.get("io.dicepp.upgrade-role") != "manager"
        ):
            raise self._expectation_failure(
                scenario, "post-commit target Manager recovery material is invalid"
            )
        source = self._inspect_container_reference(request["manager"]["backup_name"])
        if (
            source.get("Id") != request["manager"]["container_id"]
            or source.get("Image") != request["manager"]["image_id"]
        ):
            raise self._expectation_failure(
                scenario, "post-commit source Manager backup identity is invalid"
            )

    def _fork_daemon_case(
        self, directory: str, mode: str
    ) -> _LinuxUpgradeOrchestrator:
        child = _LinuxUpgradeOrchestrator(
            source_bundle=self._source_bundle,
            source_version=self.source_version,
            target_bundle=self._target_bundle,
            target_version=self.target_version,
            work_dir=self._work_dir / directory,
            docker_env=self._docker_env,
            daemon_sandbox=self._daemon_sandbox,
            use_socket_proxy=False,
        )
        child._handoff_mode = mode
        return child

    def _find_handoff_container(
        self, request: dict[str, Any], role: str
    ) -> str:
        result = self._docker_cmd(
            "ps",
            "-aq",
            "--no-trunc",
            "--filter",
            f"label=io.dicepp.upgrade-transaction={request['transaction_id']}",
            "--filter",
            f"label=io.dicepp.upgrade-role={role}",
            "--format",
            "{{.ID}}",
        )
        ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if len(ids) != 1:
            raise _OrchestratorUnavailable(
                f"handoff {role} container is not uniquely identifiable"
            )
        return ids[0]

    def _run_manual_handoff_helper(
        self,
        request: dict[str, Any],
        tx_dir: Path,
        *,
        mode: str,
    ) -> None:
        helper_name = (
            f"dicepp-harness-recovery-{mode.replace('_', '-')}-"
            f"{request['transaction_id'][:8]}"
        )
        self._docker_cmd(
            "run",
            "--rm",
            "--name",
            helper_name,
            "--label",
            f"io.dicepp.upgrade-transaction={request['transaction_id']}",
            "--label",
            "io.dicepp.upgrade-role=harness-recovery",
            "--mount",
            f"type=bind,src={tx_dir.resolve()},dst=/transaction",
            "--mount",
            "type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock",
            request["manager"]["image_id"],
            "python",
            "-m",
            "dicepp_manager.linux_update_helper",
            "--transaction-dir",
            "/transaction",
            "--socket",
            "/var/run/docker.sock",
            "--mode",
            mode,
            timeout=180,
        )

    def _run_daemon_restart_before_commit(self, scenario: str) -> str:
        assert self._daemon_sandbox is not None
        self._prepare_compose()
        self._start_source()
        self._verify_source_healthy()
        self._trigger_upgrade(scenario)
        request, tx_dir = self._wait_handoff_document("request")
        observed = self._wait_control_document("target-manager-observed.json")
        if observed.get("mode") != "daemon_before_commit":
            raise self._expectation_failure(
                scenario, "pre-commit target Manager gate was not reached"
            )
        if (tx_dir / _HANDOFF_FILENAMES["decision"]).exists():
            raise self._expectation_failure(
                scenario, "commit decision existed before the injected daemon restart"
            )
        self._daemon_sandbox.restart()
        if (tx_dir / _HANDOFF_FILENAMES["decision"]).exists():
            raise self._expectation_failure(
                scenario, "pre-commit restart unexpectedly acquired a decision"
            )
        self._verify_journal_binding(scenario, self._read_journal(), request)
        self._run_manual_handoff_helper(
            request, tx_dir, mode="restore-source"
        )
        result, _ = self._wait_handoff_document(
            "result", transaction_id=request["transaction_id"]
        )
        if result.get("value") != "source-restored":
            raise self._expectation_failure(
                scenario, "pre-commit recovery did not restore the source"
            )
        self._verify_handoff_document_binding(
            scenario, result, request, kind="result"
        )
        self._wait_rollback_complete(scenario)
        self._verify_handoff_source_objects(scenario, request)
        self._verify_dashboard_db_source(scenario)
        journal = self._read_journal()
        self._verify_journal_binding(scenario, journal, request)
        self._verify_handoff_rollback_journal(scenario, journal)
        return "source_restored"

    def _run_daemon_restart_after_commit(self, scenario: str) -> str:
        assert self._daemon_sandbox is not None
        self._prepare_compose()
        self._start_source()
        self._verify_source_healthy()
        self._trigger_upgrade(scenario)
        request, tx_dir = self._wait_handoff_document("request")
        observed = self._wait_control_document("target-manager-observed.json")
        if observed.get("mode") != "daemon_after_commit":
            raise self._expectation_failure(
                scenario, "post-commit target Manager gate was not reached"
            )
        updater_id = self._find_handoff_container(request, "updater")
        self._docker_cmd("pause", updater_id)
        if self._harness_control_dir is None:
            raise _OrchestratorUnavailable("handoff control directory is unavailable")
        (self._harness_control_dir / "allow-target-manager").write_text(
            "allow\n", encoding="utf-8"
        )
        decision, _ = self._wait_handoff_document(
            "decision", transaction_id=request["transaction_id"]
        )
        if decision.get("value") != "commit":
            raise self._expectation_failure(
                scenario, "target did not durably commit before daemon restart"
            )
        self._verify_handoff_document_binding(
            scenario, decision, request, kind="decision"
        )
        if (tx_dir / _HANDOFF_FILENAMES["result"]).exists():
            raise self._expectation_failure(
                scenario, "Updater completed before the post-commit restart"
            )
        self._daemon_sandbox.restart()
        # Capture the durable state produced by the candidate itself before
        # invoking the documented manual disaster-recovery helper.  The
        # observation remains cleanup_pending; the helper proves that this
        # state can be finalized according to the durable commit decision.
        journal = self._read_journal()
        self._verify_journal_binding(scenario, journal, request)
        premature_result = self._wait_optional_handoff_document(
            "result", request, timeout=10
        )
        if premature_result is not None:
            self._verify_handoff_document_binding(
                scenario, premature_result, request, kind="result"
            )
            raise self._expectation_failure(
                scenario,
                "post-commit restart did not preserve the cleanup_pending window",
            )

        if journal.get("status") != "interrupted" or journal.get("phase") != "cleanup_pending":
            raise self._expectation_failure(
                scenario,
                "post-commit durable journal is not cleanup_pending",
                crash_after_commit_final_state="invalid_durable_state",
                decision_status="committed",
            )
        self._verify_post_commit_recovery_material(scenario, request)
        self._run_manual_handoff_helper(
            request, tx_dir, mode="finalize-committed"
        )
        manual_result, _ = self._wait_handoff_document(
            "result", transaction_id=request["transaction_id"]
        )
        self._verify_handoff_document_binding(
            scenario, manual_result, request, kind="result"
        )
        if manual_result.get("value") != "target-committed":
            raise self._expectation_failure(
                scenario, "manual post-commit recovery did not finalize the target"
            )
        self._docker_cmd("start", request["manager"]["name"])
        self._wait_upgrade_complete(scenario, timeout=300)
        self._verify_handoff_target_objects(scenario, request)
        committed = self._read_journal()
        self._verify_journal_binding(scenario, committed, request)
        if committed.get("status") != "committed":
            raise self._expectation_failure(
                scenario, "post-commit recovery did not converge to committed"
            )
        return "cleanup_pending"

    def _mutate_sentinels_for_failure(self) -> None:
        """Damage both catalog-owned sentinels after the safety archive exists."""
        if self._instance_dir is None or not self._sentinel_digests:
            raise _OrchestratorUnavailable("rollback sentinels are not prepared")
        config = self._instance_dir / "config" / "user.json"
        data = self._instance_dir / "data" / "local_images" / "sentinel.bin"
        config.write_text('{"damaged_after_archive":true}\n', encoding="utf-8")
        data.write_bytes(b"damaged-after-pre-upgrade-archive")
        if any(
            _sha256_file(self._instance_dir / relative) == original
            for relative, original in self._sentinel_digests.items()
        ):
            raise _OrchestratorUnavailable(
                "failed to mutate both rollback sentinels"
            )
        self._sentinels_mutated = True

    def _verify_sentinels(
        self, scenario: str, *, require_mutation: bool = False
    ) -> None:
        if self._instance_dir is None:
            raise _OrchestratorUnavailable("instance directory is not prepared")
        if require_mutation and not self._sentinels_mutated:
            raise self._expectation_failure(
                scenario,
                "rollback sentinels were never mutated after the safety archive",
            )
        for relative, expected in self._sentinel_digests.items():
            path = self._instance_dir / relative
            try:
                actual = _sha256_file(path)
            except OSError as exc:
                raise self._expectation_failure(
                    scenario, f"sentinel {relative!r} is unavailable: {exc}"
                ) from exc
            if actual != expected:
                raise self._expectation_failure(
                    scenario, f"sentinel {relative!r} was not restored"
                )

    # -- upgrade driving ------------------------------------------------------

    def _trigger_upgrade(self, scenario: str) -> None:
        if self._api is None:
            raise _OrchestratorUnavailable("manager api client is not prepared")
        try:
            preview = self._api.preview(self.target_version)
            token = str(preview["preview"]["confirmation_token"])
            self._api.confirm(self.target_version, token)
        except _ManagerApiError as exc:
            raise self._expectation_failure(
                scenario, f"upgrade trigger was rejected: {exc.detail}"
            ) from exc
        except (KeyError, TypeError) as exc:
            raise self._expectation_failure(
                scenario, f"preview response is invalid: {exc}"
            ) from exc

    def _wait_upgrade_complete(
        self, scenario: str, timeout: float = 300
    ) -> None:
        if self._api is None:
            raise _OrchestratorUnavailable("manager api client is not prepared")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                status = self._api.status()
            except (_OrchestratorUnavailable, _ManagerApiError):
                time.sleep(2)
                continue
            if status.get("active_operation") is None:
                last = status.get("last_operation") or {}
                if last.get("status") == "succeeded":
                    return
                if last.get("status") == "failed":
                    raise self._expectation_failure(
                        scenario,
                        f"upgrade failed: {last.get('message', '')}",
                    )
            time.sleep(2)
        raise self._expectation_failure(
            scenario, f"upgrade did not complete within {timeout}s"
        )

    def _wait_rollback_complete(
        self, scenario: str, timeout: float = 300
    ) -> None:
        if self._api is None:
            raise _OrchestratorUnavailable("manager api client is not prepared")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                status = self._api.status()
            except (_OrchestratorUnavailable, _ManagerApiError):
                time.sleep(2)
                continue
            if status.get("active_operation") is None:
                last = status.get("last_operation") or {}
                detail = last.get("detail") or {}
                if last.get("status") == "failed" and detail.get(
                    "rolled_back"
                ) is True:
                    return
                if last.get("status") == "succeeded":
                    raise self._expectation_failure(
                        scenario, "upgrade committed; expected a rollback"
                    )
            time.sleep(2)
        raise self._expectation_failure(
            scenario, f"rollback did not complete within {timeout}s"
        )

    def _wait_apply_failure(self, scenario: str, timeout: float = 300) -> None:
        if self._api is None:
            raise _OrchestratorUnavailable("manager api client is not prepared")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                status = self._api.status()
            except (_OrchestratorUnavailable, _ManagerApiError):
                time.sleep(2)
                continue
            if status.get("active_operation") is None:
                last = status.get("last_operation") or {}
                if last.get("status") == "failed":
                    return
                if last.get("status") == "succeeded":
                    raise self._expectation_failure(
                        scenario, "upgrade committed; expected Docker apply failure"
                    )
            time.sleep(2)
        raise self._expectation_failure(
            scenario, f"apply failure did not reach a terminal state within {timeout}s"
        )

    def _inject_health_failure(
        self, scenario: str, timeout: float = 180
    ) -> None:
        """Wait for the bot to switch to the target image, then stop it."""
        bot_name = self._container_names.get("bot")
        if not bot_name:
            raise _OrchestratorUnavailable("bot container is not started")
        target_bot_id = self._target_image_ids.get("bot")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self._bot_image_id() == target_bot_id:
                    self._mutate_sentinels_for_failure()
                    self._docker_cmd("stop", "-t", "10", bot_name)
                    return
            except _OrchestratorUnavailable:
                # The container may be recreated mid-switch; keep polling.
                pass
            time.sleep(0.2)
        raise self._expectation_failure(
            scenario,
            "bot did not switch to the target image before the failure window",
        )

    def _target_process_start_count(self) -> int:
        target_ids = {
            value for value in self._target_image_ids.values() if isinstance(value, str)
        }
        if not target_ids:
            raise _OrchestratorUnavailable("target image identities are unavailable")
        result = self._docker_cmd(
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={self._compose_project}",
            "--format",
            "{{.Image}}|{{.ID}}",
        )
        count = 0
        for line in result.stdout.splitlines():
            _image_ref, separator, container_id = line.partition("|")
            if not separator or not container_id:
                continue
            inspected = self._docker_cmd(
                "inspect", "--format", "{{.Image}}", container_id
            ).stdout.strip()
            if inspected in target_ids:
                count += 1
        return count

    # -- journal / cleanup ----------------------------------------------------

    def _read_journal(self) -> dict[str, Any]:
        if self._instance_dir is None:
            return {}
        db = self._instance_dir / "manager" / "state" / "manager.db"
        if not db.is_file():
            return {"status": "unavailable"}
        try:
            with closing(
                sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            ) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT transaction_id, operation_id, status, phase, detail "
                    "FROM manager_journal "
                    "ORDER BY updated_at DESC LIMIT 1"
                ).fetchall()
            if rows:
                detail = json.loads(rows[0]["detail"] or "{}")
                if not isinstance(detail, dict):
                    detail = {}
                return {
                    "transaction_id": rows[0]["transaction_id"],
                    "operation_id": rows[0]["operation_id"],
                    "status": rows[0]["status"] or "unknown",
                    "phase": rows[0]["phase"] or "unknown",
                    "detail": detail,
                }
        except sqlite3.OperationalError as exc:
            return {
                "status": "unavailable",
                "error": f"cannot read manager journal: {exc}",
            }
        except Exception as exc:
            return {
                "status": "unavailable",
                "error": f"cannot read manager journal: {exc}",
            }
        return {"status": "unavailable"}

    def _verify_rollback_journal(
        self, scenario: str, journal: dict[str, Any]
    ) -> str:
        status = journal.get("status")
        detail = journal.get("detail")
        rollback_result = detail.get("rollback_result") if isinstance(detail, dict) else None
        if (
            status != "rolled_back"
            or not isinstance(detail, dict)
            or not isinstance(rollback_result, dict)
            or rollback_result.get("succeeded") is not True
            or rollback_result.get("program_restored") is not True
            or rollback_result.get("data_restored") is not True
        ):
            raise self._expectation_failure(
                scenario,
                "journal does not prove successful program and data restoration",
                journal_status=status or "unavailable",
            )
        return status

    def _rollback_program_status(self, journal: dict[str, Any]) -> str:
        detail = journal.get("detail")
        rollback_result = detail.get("rollback_result") if isinstance(detail, dict) else None
        program = rollback_result.get("program") if isinstance(rollback_result, dict) else None
        status = program.get("status") if isinstance(program, dict) else None
        if not isinstance(status, str) or not status:
            raise _OrchestratorUnavailable(
                "rollback journal does not contain an observed platform restore status"
            )
        return status

    def cleanup(self) -> None:
        errors: list[str] = []
        if self._compose_file is not None and self._compose_started:
            try:
                self._compose(
                    "down", "--volumes", "--remove-orphans", "-t", "10",
                    timeout=60,
                )
            except _OrchestratorUnavailable as exc:
                errors.append(str(exc))
        if self._docker_proxy is not None:
            self._docker_proxy.stop()
        try:
            self._discover_cleanup_transaction_ids()
        except _OrchestratorUnavailable as exc:
            errors.append(str(exc))
        try:
            self._cleanup_handoff_containers()
        except _OrchestratorUnavailable as exc:
            errors.append(str(exc))
        try:
            self._cleanup_compose_project_containers()
        except _OrchestratorUnavailable as exc:
            errors.append(str(exc))
        for image_ref in self._owned_image_refs:
            try:
                self._cleanup_owned_image_containers(image_ref)
                self._docker_cmd("rmi", image_ref)
            except _OrchestratorUnavailable as exc:
                errors.append(str(exc))
        try:
            self._cleanup_owned_network()
        except _OrchestratorUnavailable as exc:
            errors.append(str(exc))
        if errors:
            raise _OrchestratorUnavailable("cleanup failed: " + "; ".join(errors))

    def _discover_cleanup_transaction_ids(self) -> None:
        """Bind cleanup to handoff requests persisted in the isolated instance."""
        if self._instance_dir is None:
            return
        recovery_root = self._instance_dir / "manager" / "recovery"
        if not recovery_root.is_dir():
            return
        for tx_dir in recovery_root.iterdir():
            if tx_dir.is_symlink() or not tx_dir.is_dir():
                continue
            request_path = tx_dir / _HANDOFF_FILENAMES["request"]
            if not request_path.is_file() or request_path.is_symlink():
                continue
            try:
                request = json.loads(request_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise _OrchestratorUnavailable(
                    "cannot verify a persisted handoff request for cleanup"
                ) from exc
            transaction_id = request.get("transaction_id")
            operation_id = request.get("operation_id")
            manager = request.get("manager")
            bot = request.get("bot")
            dashboard = request.get("dashboard")
            targets = request.get("target_images")
            manager_image = manager.get("image_id") if isinstance(manager, dict) else None
            bot_image = bot.get("image_id") if isinstance(bot, dict) else None
            dashboard_image = (
                dashboard.get("image_id") if isinstance(dashboard, dict) else None
            )
            target_bot_image = targets.get("bot") if isinstance(targets, dict) else None
            target_dashboard_image = (
                targets.get("dashboard") if isinstance(targets, dict) else None
            )
            if not all(
                (
                    isinstance(transaction_id, str),
                    transaction_id == tx_dir.name,
                    re.fullmatch(r"[0-9a-f]{32}", transaction_id or "") is not None,
                    isinstance(operation_id, str),
                    re.fullmatch(r"[0-9a-f]{32}", operation_id or "") is not None,
                    request.get("compose_project") == self._compose_project,
                    request.get("source_version") == self.source_version,
                    request.get("target_version") == self.target_version,
                    isinstance(manager, dict),
                    manager_image == self._source_image_ids.get("dashboard"),
                    request.get("target_manager_image_id")
                    == self._target_image_ids.get("dashboard"),
                    isinstance(bot, dict),
                    bot_image == self._source_image_ids.get("bot"),
                    isinstance(dashboard, dict),
                    dashboard_image == self._source_image_ids.get("dashboard"),
                    isinstance(targets, dict),
                    target_bot_image == self._target_image_ids.get("bot"),
                    target_dashboard_image == self._target_image_ids.get("dashboard"),
                )
            ):
                raise _OrchestratorUnavailable(
                    "persisted handoff request is not bound to the isolated matrix"
                )
            self._transaction_ids.add(transaction_id)

    def _cleanup_compose_project_containers(self) -> None:
        """Remove residual containers owned by this unique Compose project."""
        result = self._docker_cmd(
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={self._compose_project}",
            "--format",
            "{{.ID}}",
        )
        for container_id in {
            line.strip() for line in result.stdout.splitlines() if line.strip()
        }:
            project = self._docker_cmd(
                "inspect",
                "--format",
                '{{index .Config.Labels "com.docker.compose.project"}}',
                container_id,
            ).stdout.strip()
            if project != self._compose_project:
                raise _OrchestratorUnavailable(
                    "Compose container ownership changed; refusing cleanup"
                )
            self._docker_cmd("rm", "-f", container_id)

    def _cleanup_owned_image_containers(self, image_ref: str) -> None:
        """Close the post-handoff cleanup race before removing a loaded image.

        A recovering Manager can recreate a source Runtime after the earlier
        Compose/transaction sweeps.  Re-enumerate users of an image that this
        harness loaded into an initially image-free daemon, but still require
        the exact project or an observed transaction label before removal.
        """
        result = self._docker_cmd(
            "ps",
            "-aq",
            "--filter",
            f"ancestor={image_ref}",
            "--format",
            "{{.ID}}",
        )
        for container_id in {
            line.strip() for line in result.stdout.splitlines() if line.strip()
        }:
            inspected = self._docker_cmd(
                "inspect", "--format", "{{json .Config.Labels}}", container_id
            ).stdout.strip()
            try:
                labels = json.loads(inspected)
            except json.JSONDecodeError as exc:
                raise _OrchestratorUnavailable(
                    "cannot verify container labels before image cleanup"
                ) from exc
            if not isinstance(labels, dict):
                labels = {}
            project = labels.get("com.docker.compose.project")
            transaction_id = labels.get("io.dicepp.upgrade-transaction")
            if project != self._compose_project and transaction_id not in self._transaction_ids:
                raise _OrchestratorUnavailable(
                    "image container is outside the isolated project; refusing cleanup: "
                    f"image={image_ref!r}, container={container_id!r}, "
                    f"project={project!r}, transaction={transaction_id!r}, "
                    f"label_keys={sorted(labels)!r}"
                )
            self._docker_cmd("rm", "-f", container_id)

    def _cleanup_handoff_containers(self) -> None:
        """Remove only containers proven to belong to observed transactions."""
        for transaction_id in sorted(self._transaction_ids):
            try:
                result = self._docker_cmd(
                    "ps",
                    "-aq",
                    "--filter",
                    f"label=io.dicepp.upgrade-transaction={transaction_id}",
                    "--format",
                    "{{.ID}}",
                )
            except _OrchestratorUnavailable as exc:
                raise _OrchestratorUnavailable(
                    f"cannot enumerate transaction {transaction_id} containers: {exc}"
                ) from exc
            for container_id in {
                line.strip() for line in result.stdout.splitlines() if line.strip()
            }:
                try:
                    inspected = self._docker_cmd(
                        "inspect",
                        "--format",
                        '{{index .Config.Labels "io.dicepp.upgrade-transaction"}}',
                        container_id,
                    ).stdout.strip()
                    if inspected != transaction_id:
                        raise _OrchestratorUnavailable(
                            "transaction container ownership changed; refusing cleanup"
                        )
                    self._docker_cmd("rm", "-f", container_id)
                except _OrchestratorUnavailable as exc:
                    raise _OrchestratorUnavailable(
                        f"cannot remove owned transaction container {container_id}: {exc}"
                    ) from exc

    def _cleanup_owned_network(self) -> None:
        if not self._owns_dice_network:
            return
        if self._network_id is None:
            raise _OrchestratorUnavailable(
                "owned dice-net network id was not recorded"
            )
        inspected = self._docker_cmd(
            "network",
            "inspect",
            "--format",
            '{{.Id}}|{{index .Labels "io.dicepp.upgrade-harness-network"}}',
            self._network_id,
        ).stdout.strip()
        actual_id, separator, actual_label = inspected.partition("|")
        if (
            not separator
            or actual_id != self._network_id
            or actual_label != self._network_identity
        ):
            raise _OrchestratorUnavailable(
                "dice-net ownership changed; refusing cleanup"
            )
        self._docker_cmd("network", "rm", self._network_id)
        self._network_id = None
        self._owns_dice_network = False


# ---------------------------------------------------------------------------
# Compose passthrough
# ---------------------------------------------------------------------------

def _minimal_test_compose(
    shipped_compose: str,
    instance_dir: Path,
    source_version: str,
) -> str:
    """Validate the shipped compose and return it unchanged.

    The shipped compose pins every service image through the
    ``DICEPP_IMAGE_TAG`` environment variable, so the orchestrator starts the
    source version with ``DICEPP_IMAGE_TAG=v{source_version}`` and lets the
    Manager switch the managed containers to the target bundle images.  The
    text returned here is the shipped compose verbatim; ``_prepare_compose``
    writes the original bytes without any modification.
    """
    del instance_dir, source_version
    if "services:" not in shipped_compose:
        raise _OrchestratorUnavailable(
            "shipped compose does not define any services"
        )
    if "manager:" not in shipped_compose:
        raise _OrchestratorUnavailable(
            "shipped compose does not define a manager service"
        )
    if "${DICEPP_IMAGE_TAG" not in shipped_compose:
        raise _OrchestratorUnavailable(
            "shipped compose does not use DICEPP_IMAGE_TAG interpolation; "
            "the orchestrator cannot pin source images without it"
        )
    return shipped_compose
