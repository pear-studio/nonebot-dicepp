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
import select
import shutil
import socket
import sqlite3
import subprocess
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

    orchestrator = _LinuxUpgradeOrchestrator(
        source_bundle=Path(source_bundle["path"]),
        source_version=context["source_version"],
        target_bundle=Path(target_bundle["path"]),
        target_version=context["target_version"],
        work_dir=work_dir,
    )

    try:
        scenario_func = {
            "healthy_commit": orchestrator.healthy_commit,
            "target_health_failure_rollback": orchestrator.target_health_failure_rollback,
            "retry_after_rollback": orchestrator.retry_after_rollback,
            "apply_failure_before_target_execution": orchestrator.apply_failure_before_target_execution,
        }[scenario]
        return scenario_func()
    except _OrchestratorUnavailable as exc:
        return _unavailable(context, str(exc))
    finally:
        orchestrator.cleanup()


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
    bundle_path: Path, work_dir: Path, label: str
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
        detail = ""
        try:
            parsed = json.loads(exc.read().decode("utf-8"))
            if isinstance(parsed, dict):
                detail = str(parsed.get("detail", ""))
        except (OSError, ValueError):
            pass
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


def _docker_object_exists(kind: str, name: str) -> bool:
    try:
        result = subprocess.run(
            ["docker", kind, "inspect", name],
            capture_output=True,
            text=True,
            timeout=30,
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


def _optional_docker_image_id(reference: str) -> str | None:
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", reference],
            capture_output=True,
            text=True,
            timeout=30,
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

    def __init__(self, base_url: str = "http://127.0.0.1:4091") -> None:
        self.base_url = base_url.rstrip("/")
        self.token: str | None = None

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.token:
            raise _OrchestratorUnavailable("Manager API token is not set")
        _status, payload = _http_json(
            method,
            f"{self.base_url}{path}",
            token=self.token,
            body=body,
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
    ) -> None:
        self._source_bundle = source_bundle
        self.source_version = source_version
        self._target_bundle = target_bundle
        self.target_version = target_version
        self._work_dir = work_dir
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
        self._seeded_bundle_path: Path | None = None
        self._api: _ManagerApiClient | None = None
        self._docker_proxy: _DockerSocketProxy | None = None
        self._sentinel_digests: dict[str, str] = {}
        self._sentinel_original_bytes: dict[str, bytes] = {}
        self._sentinels_mutated = False

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

    # -- scenario failure helpers --------------------------------------------

    def _expectation_failure(
        self,
        scenario: str,
        message: str,
        **observation_values: Any,
    ) -> _ScenarioExpectationFailure:
        observations: dict[str, Any] = {
            key: "unavailable" for key in SCENARIO_OBSERVATION_FIELDS[scenario]
        }
        observations.update(observation_values)
        observations["reason"] = message
        return _ScenarioExpectationFailure(
            message,
            assertions={key: False for key in SCENARIO_ASSERTIONS[scenario]},
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
        proxy_path = instance / "manager" / "docker-proxy.sock"
        self._docker_proxy = _DockerSocketProxy(
            proxy_path,
            on_create_failure=self._mutate_sentinels_for_failure,
        )
        self._compose_override = instance / "docker-compose.harness.yml"
        quoted_proxy = json.dumps(proxy_path.resolve().as_posix())
        self._compose_override.write_text(
            "services:\n"
            "  manager:\n"
            "    volumes:\n"
            f"      - {quoted_proxy}:/var/run/docker.sock\n",
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
        bundle_size = self._target_bundle.stat().st_size
        bundle_digest = _sha256_file(self._target_bundle)
        self._seeded_bundle_path = packages_dir / bundle_filename
        shutil.copy2(self._target_bundle, self._seeded_bundle_path)
        self._write_seeded_release(
            instance,
            packages_dir,
            target_manifest,
            bundle_filename,
            bundle_size,
            bundle_digest,
        )

        # 5. Manager API client (the token is read after the Manager starts).
        self._api = _ManagerApiClient(base_url="http://127.0.0.1:4091")

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
        _docker(
            "docker",
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
        if self._docker_proxy is None:
            raise _OrchestratorUnavailable("Docker socket proxy is not prepared")
        self._docker_proxy.start()
        self._assert_isolated_docker_namespace()
        _docker("network", "create", "dice-net")
        self._owns_dice_network = True
        self._load_images()
        env = {**os.environ, "DICEPP_IMAGE_TAG": self._image_tag}
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
            role: self._find_container_name(role) for role in ("bot", "dashboard")
        }

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
                existing = _optional_docker_image_id(reference)
                if existing is not None and existing != expected_id:
                    raise _OrchestratorUnavailable(
                        f"image reference {reference!r} already points to unrelated bytes"
                    )
                if existing is None:
                    self._owned_image_refs.add(reference)
            images = _load_docker_image_from_bundle(bundle, self._work_dir, label)
            if set(images) != {"bot", "dashboard"}:
                raise _OrchestratorUnavailable(
                    f"{label} bundle does not contain bot and dashboard images"
                )

    def _find_container_name(self, service: str) -> str:
        result = _docker(
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
            if _docker_object_exists("container", name):
                raise _OrchestratorUnavailable(
                    f"Docker container {name!r} already exists; refusing to touch a shared instance"
                )
        if _docker_object_exists("network", "dice-net"):
            raise _OrchestratorUnavailable(
                "Docker network 'dice-net' already exists; refusing to join a shared instance"
            )

    def _container_state(self, role: str) -> dict[str, Any]:
        name = self._container_names.get(role)
        if not name:
            raise _OrchestratorUnavailable(f"{role} container is not started")
        result = _docker(
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
        return {
            "image_id": image_id,
            "running": state.get("Running") is True,
            "status": state.get("Status"),
            "started_at": state.get("StartedAt"),
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
                    _docker("stop", "-t", "10", bot_name)
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
        result = _docker(
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
            inspected = _docker(
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
                    "SELECT transaction_id, status, phase, detail FROM manager_journal "
                    "ORDER BY updated_at DESC LIMIT 1"
                ).fetchall()
            if rows:
                detail = json.loads(rows[0]["detail"] or "{}")
                if not isinstance(detail, dict):
                    detail = {}
                return {
                    "transaction_id": rows[0]["transaction_id"],
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
        if self._compose_file is not None and self._compose_started:
            try:
                self._compose(
                    "down", "--volumes", "--remove-orphans", "-t", "10",
                    timeout=60,
                )
            except _OrchestratorUnavailable:
                pass
        if self._docker_proxy is not None:
            self._docker_proxy.stop()
        for image_ref in self._owned_image_refs:
            try:
                _docker("rmi", image_ref)
            except _OrchestratorUnavailable:
                pass
        if self._owns_dice_network:
            try:
                _docker("network", "rm", "dice-net")
            except _OrchestratorUnavailable:
                pass


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
