"""Exercise the simplified Windows upgrade contract with final release bytes.

The orchestrator deliberately drives a packaged source Manager instead of
importing the source revision as Python.  A validation-only release snapshot is
staged in the isolated instance, then the real Manager API, Velopack updater,
target Manager startup recovery and root ``DicePP-Recover.cmd`` are exercised.
The temporary instance never lives below the uploaded diagnostics directory, so
API/control credentials and recovery archives cannot leak into CI artifacts.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from dicepp_data import DATA_CATALOG, InstanceLayout
from dicepp_manager.client import ManagerClient, ManagerClientError
from dicepp_manager.config import ManagerClientSettings
from dicepp_manager.release import ReleaseManager, UpdateSettings
from dicepp_manager.velopack_bundle import validate_velopack_bundle


WINDOWS_SCENARIOS = frozenset(
    {"healthy_commit", "manual_restore_after_target_failure"}
)
_SOURCE_PURPOSES = frozenset({"portable", "velopack-bundle"})
_TARGET_PURPOSES = frozenset({"portable", "setup", "velopack-bundle"})
_MAX_PORTABLE_MEMBERS = 50_000
_MAX_PORTABLE_MEMBER_BYTES = 2 * 1024**3
_MAX_PORTABLE_TOTAL_BYTES = 8 * 1024**3
_VERSION_PREFIX = "DicePP Dashboard v"


class WindowsMatrixError(RuntimeError):
    """The real Windows matrix could not prove its behavioral contract."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _records_by_purpose(
    records: Any,
    *,
    expected: frozenset[str],
    label: str,
) -> dict[str, dict[str, Any]]:
    if not isinstance(records, list):
        raise WindowsMatrixError(f"{label} assets are missing")
    result: dict[str, dict[str, Any]] = {}
    for raw in records:
        if not isinstance(raw, dict):
            raise WindowsMatrixError(f"{label} asset is malformed")
        purpose = raw.get("purpose")
        path_value = raw.get("path")
        if not isinstance(purpose, str) or not isinstance(path_value, str):
            raise WindowsMatrixError(f"{label} asset identity is malformed")
        path = Path(path_value).resolve(strict=True)
        if path.is_symlink() or not path.is_file():
            raise WindowsMatrixError(f"{label} asset is not a regular file: {path}")
        if raw.get("size") != path.stat().st_size or raw.get("sha256") != _sha256(path):
            raise WindowsMatrixError(f"{label} asset bytes changed: {path.name}")
        if purpose in result:
            raise WindowsMatrixError(f"{label} asset purpose is duplicated: {purpose}")
        result[purpose] = {**raw, "resolved_path": path}
    if frozenset(result) != expected:
        raise WindowsMatrixError(
            f"{label} asset purposes differ: expected={sorted(expected)!r}, "
            f"actual={sorted(result)!r}"
        )
    return result


def _safe_extract_portable(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    total = 0
    with zipfile.ZipFile(archive_path, "r") as archive:
        infos = archive.infolist()
        if not infos or len(infos) > _MAX_PORTABLE_MEMBERS:
            raise WindowsMatrixError("Portable member count is invalid")
        for info in infos:
            member = PurePosixPath(info.filename)
            mode = info.external_attr >> 16
            if (
                member.is_absolute()
                or not member.parts
                or any(part in {"", ".", ".."} for part in member.parts)
                or stat.S_ISLNK(mode)
                or info.file_size < 0
                or info.file_size > _MAX_PORTABLE_MEMBER_BYTES
            ):
                raise WindowsMatrixError(
                    f"Portable contains an unsafe member: {info.filename!r}"
                )
            total += info.file_size
            if total > _MAX_PORTABLE_TOTAL_BYTES:
                raise WindowsMatrixError("Portable expanded size is unbounded")
            target = destination.joinpath(*member.parts)
            resolved_parent = target.parent.resolve(strict=False)
            if destination.resolve() not in (resolved_parent, *resolved_parent.parents):
                raise WindowsMatrixError("Portable member escapes the instance root")
        archive.extractall(destination)


def _program_version(instance_root: Path) -> str:
    executable = instance_root / "current" / "DicePP.exe"
    if executable.is_symlink() or not executable.is_file():
        raise WindowsMatrixError("Packaged current/DicePP.exe is missing")
    completed = subprocess.run(
        [str(executable), "--version"],
        cwd=instance_root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        timeout=20,
        check=False,
    )
    output = completed.stdout.strip()
    if completed.returncode != 0 or not output.startswith(_VERSION_PREFIX):
        raise WindowsMatrixError(
            f"Packaged Dashboard version probe failed: rc={completed.returncode}, "
            f"output={output!r}"
        )
    return output.removeprefix(_VERSION_PREFIX)


def _tree_digest(root: Path) -> str:
    if root.is_symlink() or not root.is_dir():
        raise WindowsMatrixError(f"Program tree is unavailable: {root}")
    digest = hashlib.sha256()
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise WindowsMatrixError(f"Program tree contains a link: {path}")
        if path.is_file():
            files.append(path)
        elif not path.is_dir():
            raise WindowsMatrixError(f"Program tree contains a special entry: {path}")
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(path.stat().st_size.to_bytes(8, "big"))
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _stage_validation_release(
    layout: InstanceLayout,
    *,
    source_version: str,
    target_version: str,
    target_assets: dict[str, dict[str, Any]],
) -> None:
    for directory in (
        layout.config_dir,
        layout.manager_state_dir,
        layout.manager_packages_dir,
        layout.manager_recovery_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    bundle_record = target_assets["velopack-bundle"]
    artifact = {
        key: bundle_record[key]
        for key in ("platform", "arch", "filename", "purpose", "size", "sha256")
    }
    if artifact != {
        "platform": "windows",
        "arch": "amd64",
        "filename": "velopack.win-x64.zip",
        "purpose": "velopack-bundle",
        "size": bundle_record["size"],
        "sha256": bundle_record["sha256"],
    }:
        raise WindowsMatrixError("Target Velopack identity is invalid")
    compatibility = {
        "deployment_schema_version": 2,
        "minimum_manager_version": "1.0",
        "catalog_version": int(DATA_CATALOG.to_dict()["format_version"]),
        "catalog_digest": DATA_CATALOG.digest,
        "automatic_upgrade": True,
        "problems": [],
    }
    available = {
        "version": target_version,
        "channel": "prerelease",
        "change_scope": ["runtime", "dashboard", "manager", "deployment"],
        "linux_manager_handoff_protocol": None,
        "compatible": True,
        "compatibility": compatibility,
        "release_url": "https://validation.invalid/dicepp/final-candidate",
        "published_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": [
            {
                key: record[key]
                for key in ("platform", "arch", "filename", "purpose", "size", "sha256")
            }
            | {"download_url": f"https://validation.invalid/{record['filename']}"}
            for record in target_assets.values()
        ],
    }
    generation = str(bundle_record["sha256"])[:32]
    version_dir = layout.manager_packages_dir / target_version
    version_dir.mkdir(parents=True, exist_ok=False)
    managed_bundle = version_dir / f"velopack-{generation}.win-x64.zip"
    shutil.copyfile(bundle_record["resolved_path"], managed_bundle)
    manager = ReleaseManager(
        layout=layout,
        settings_loader=lambda: UpdateSettings(
            discovery_enabled=False,
            channel="prerelease",
        ),
        current_version_loader=lambda: source_version,
        target=("windows", "amd64"),
    )
    payload, bundle_manifest = manager._materialize_velopack_bundle(
        available,
        artifact,
        managed_bundle,
    )
    manager._write_verified_metadata(
        available,
        artifact,
        managed_bundle,
        payload_path=payload,
        bundle_manifest=bundle_manifest,
        completed_at=datetime.now(timezone.utc).isoformat(),
    )
    manager._latest = available
    manager._latest_channel = "prerelease"
    manager._persist_state_locked()


def _reserve_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _manager_environment(root: Path, port: int) -> dict[str, str]:
    token_path = root / "manager" / "state" / "api-token"
    runtime_command = subprocess.list2cmdline(
        [sys.executable, "-c", "import time; time.sleep(600)"]
    )
    env = os.environ.copy()
    env.update(
        {
            "DICEPP_PROJECT_ROOT": str(root),
            "DICEPP_MANAGER_HOST": "127.0.0.1",
            "DICEPP_MANAGER_PORT": str(port),
            "DICEPP_MANAGER_RUNTIME": "process",
            "DICEPP_MANAGER_RUNTIME_UNIT_ID": "dicepp-runtime",
            "DICEPP_MANAGER_PROCESS_COMMAND": runtime_command,
            "DICEPP_MANAGER_PROCESS_CWD": str(root),
            "DICEPP_MANAGER_PROCESS_STOP_TIMEOUT": "5",
            "DICEPP_MANAGER_TOKEN_FILE": str(token_path),
            "DICEPP_MANAGER_RELEASE_SCHEDULER": "false",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return env


def _launch_manager(root: Path, env: dict[str, str]) -> subprocess.Popen[bytes]:
    launcher = root / "DicePP.exe"
    if launcher.is_symlink() or not launcher.is_file():
        raise WindowsMatrixError("Stable DicePP.exe launcher is missing")
    return subprocess.Popen(
        [str(launcher), "--background"],
        cwd=root,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=(
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        ),
    )


def _client(root: Path, port: int) -> ManagerClient:
    return ManagerClient(
        ManagerClientSettings(
            base_url=f"http://127.0.0.1:{port}",
            token_path=root / "manager" / "state" / "api-token",
            timeout=3,
        )
    )


def _wait_manager(
    root: Path,
    port: int,
    *,
    version: str,
    timeout: float = 90,
) -> tuple[ManagerClient, dict[str, Any]]:
    deadline = time.monotonic() + timeout
    last_error = "Manager did not answer"
    while time.monotonic() < deadline:
        client = _client(root, port)
        try:
            status = asyncio.run(client.status())
            health = status.get("health")
            if isinstance(health, dict) and health.get("dicepp_version") == version:
                return client, status
            last_error = f"unexpected health: {health!r}"
        except (ManagerClientError, OSError, ValueError) as exc:
            last_error = str(exc) or type(exc).__name__
        time.sleep(0.2)
    raise WindowsMatrixError(
        f"Manager {version} did not become ready on port {port}: {last_error}"
    )


def _wait_operation(
    client: ManagerClient,
    operation: dict[str, Any],
    *,
    expected_status: str = "succeeded",
    timeout: float = 30,
) -> dict[str, Any]:
    operation_id = operation.get("operation_id")
    if not isinstance(operation_id, str) or not operation_id:
        raise WindowsMatrixError("Manager operation identity is missing")
    deadline = time.monotonic() + timeout
    current = operation
    while time.monotonic() < deadline:
        current = asyncio.run(client.get_operation(operation_id))
        if current.get("status") not in {"queued", "running"}:
            break
        time.sleep(0.1)
    if current.get("status") != expected_status:
        raise WindowsMatrixError(
            f"Manager operation {operation_id} ended as {current.get('status')!r}: "
            f"{current.get('message')!r}"
        )
    return current


def _start_runtime(client: ManagerClient) -> None:
    operation = asyncio.run(client.operate("dicepp-runtime", "start"))
    _wait_operation(client, operation)
    status = asyncio.run(client.status())
    units = status.get("runtime_units")
    if not _runtime_is_healthy(units):
        raise WindowsMatrixError(f"RuntimeUnit did not start: {units!r}")


def _runtime_is_healthy(units: Any) -> bool:
    if not isinstance(units, list) or len(units) != 1:
        return False
    runtime = units[0].get("runtime") if isinstance(units[0], dict) else None
    return (
        isinstance(runtime, dict)
        and runtime.get("runtime_state") == "running"
        and runtime.get("health") == "healthy"
    )


def _wait_runtime_healthy(
    client: ManagerClient,
    *,
    timeout: float = 60,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last_status = asyncio.run(client.status())
        if _runtime_is_healthy(last_status.get("runtime_units")):
            return last_status
        time.sleep(0.2)
    raise WindowsMatrixError(
        "RuntimeUnit did not recover to healthy state: "
        f"{last_status.get('runtime_units')!r}"
    )


def _stop_runtime_best_effort(root: Path, port: int) -> None:
    try:
        client, status = _wait_manager(root, port, version=_program_version(root), timeout=3)
        units = status.get("runtime_units")
        if _runtime_is_healthy(units):
            operation = asyncio.run(client.operate("dicepp-runtime", "stop"))
            _wait_operation(client, operation, timeout=10)
    except Exception:
        pass
    identity = root / "manager" / "state" / "runtime-process.json"
    try:
        payload = json.loads(identity.read_text(encoding="utf-8"))
        pid = payload.get("pid")
        if type(pid) is int and pid > 0:
            subprocess.run(
                ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
    except (OSError, ValueError, json.JSONDecodeError):
        pass


def _stop_root_processes(root: Path) -> None:
    env = os.environ.copy()
    env["DICEPP_MATRIX_ROOT"] = str(root.resolve())
    script = r"""
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($env:DICEPP_MATRIX_ROOT).TrimEnd('\') + '\'
$processes = @(Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -and
    [IO.Path]::GetFullPath($_.ExecutablePath).StartsWith(
        $root,
        [StringComparison]::OrdinalIgnoreCase
    )
})
foreach ($process in $processes) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}
"""
    subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", script],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        timeout=30,
        check=False,
    )


class _PortBlocker:
    def __init__(self, port: int) -> None:
        self.port = port
        self.acquired = threading.Event()
        self.stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._listener: socket.socket | None = None

    def start(self) -> None:
        self._thread.start()

    def wait(self, timeout: float = 30) -> None:
        if not self.acquired.wait(timeout):
            raise WindowsMatrixError("Could not acquire the Manager port failure gate")

    def close(self) -> None:
        self.stop.set()
        listener = self._listener
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self.stop.is_set():
            listener = socket.socket()
            try:
                listener.bind(("127.0.0.1", self.port))
                listener.listen(1)
                self._listener = listener
                self.acquired.set()
                self.stop.wait()
                return
            except OSError:
                listener.close()
                time.sleep(0.02)


def _confirm_upgrade(client: ManagerClient, target_version: str) -> dict[str, Any]:
    preview_payload = asyncio.run(client.upgrade_preview())
    preview = preview_payload.get("preview")
    if (
        not isinstance(preview, dict)
        or preview.get("version") != target_version
        or preview.get("recovery_scope") != "program_data_runtime"
        or preview.get("manual_recovery_entry") != "DicePP-Recover.cmd"
    ):
        raise WindowsMatrixError(f"Upgrade preview is inconsistent: {preview!r}")
    token = preview.get("confirmation_token")
    if not isinstance(token, str) or len(token) < 32:
        raise WindowsMatrixError("Upgrade confirmation token is missing")
    return asyncio.run(
        client.confirm_upgrade(
            version=target_version,
            confirmation_token=token,
        )
    )


def _wait_current_version(root: Path, version: str, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    last_error = "current program was not readable"
    while time.monotonic() < deadline:
        try:
            if _program_version(root) == version:
                return
            last_error = f"current version is {_program_version(root)!r}"
        except (OSError, subprocess.SubprocessError, WindowsMatrixError) as exc:
            last_error = str(exc) or type(exc).__name__
        time.sleep(0.2)
    raise WindowsMatrixError(f"Current program did not become {version}: {last_error}")


def _write_user_config(path: Path, marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "matrix_marker": marker,
                "update": {
                    "discovery_enabled": False,
                    "auto_download": False,
                    "channel": "prerelease",
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _read_marker(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WindowsMatrixError("User configuration marker is unreadable") from exc
    marker = payload.get("matrix_marker") if isinstance(payload, dict) else None
    if not isinstance(marker, str):
        raise WindowsMatrixError("User configuration marker is missing")
    return marker


def _scenario_root(base: Path, name: str) -> Path:
    root = base / name
    if root.exists():
        raise WindowsMatrixError(f"Scenario root already exists: {root}")
    return root


def _prepare_instance(
    base: Path,
    *,
    source_portable: Path,
    target_portable: Path,
    source_version: str,
    target_version: str,
    target_assets: dict[str, dict[str, Any]],
) -> tuple[Path, str, str]:
    root = _scenario_root(base, "instance")
    target_reference = _scenario_root(base, "target-reference")
    _safe_extract_portable(source_portable, root)
    _safe_extract_portable(target_portable, target_reference)
    if _program_version(root) != source_version:
        raise WindowsMatrixError("Source Portable version differs from matrix")
    if _program_version(target_reference) != target_version:
        raise WindowsMatrixError("Target Portable version differs from matrix")
    source_tree = _tree_digest(root / "current")
    target_tree = _tree_digest(target_reference / "current")
    if source_tree == target_tree:
        raise WindowsMatrixError("Source and target program trees are indistinguishable")
    layout = InstanceLayout.from_root(root)
    _write_user_config(layout.config_user, "source")
    _stage_validation_release(
        layout,
        source_version=source_version,
        target_version=target_version,
        target_assets=target_assets,
    )
    return root, source_tree, target_tree


def _healthy_commit(
    root: Path,
    *,
    port: int,
    source_version: str,
    target_version: str,
    source_tree: str,
    target_tree: str,
) -> tuple[dict[str, bool], dict[str, Any]]:
    del source_tree
    env = _manager_environment(root, port)
    _launch_manager(root, env)
    client, status = _wait_manager(root, port, version=source_version)
    source_started = status.get("health", {}).get("dicepp_version") == source_version
    _start_runtime(client)
    operation = _confirm_upgrade(client, target_version)
    target_client, target_status = _wait_manager(
        root,
        port,
        version=target_version,
        timeout=120,
    )
    target_started = target_status.get("health", {}).get("dicepp_version") == target_version
    deadline = time.monotonic() + 60
    upgrade_status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        upgrade_status = asyncio.run(target_client.upgrade_status())
        last = upgrade_status.get("last_operation")
        if isinstance(last, dict) and last.get("status") not in {"queued", "running"}:
            break
        time.sleep(0.2)
    last = upgrade_status.get("last_operation")
    detail = last.get("detail") if isinstance(last, dict) else None
    journal_committed = (
        isinstance(last, dict)
        and last.get("status") == "succeeded"
        and isinstance(detail, dict)
        and detail.get("phase") == "committed"
    )
    try:
        target_status = _wait_runtime_healthy(target_client)
    except WindowsMatrixError as exc:
        raise WindowsMatrixError(f"{exc}; last_operation={last!r}") from exc
    current_tree = _tree_digest(root / "current")
    layout = InstanceLayout.from_root(root)
    recovery_clean = (
        not (root / "DicePP-Recover.cmd").exists()
        and layout.manager_recovery_dir.is_dir()
        and not any(layout.manager_recovery_dir.iterdir())
    )
    runtime_units = target_status.get("runtime_units")
    local_health_passed = (
        target_started
        and current_tree == target_tree
        and _read_marker(layout.config_user) == "source"
        and recovery_clean
        and _runtime_is_healthy(runtime_units)
    )
    if not all((source_started, target_started, local_health_passed, journal_committed)):
        raise WindowsMatrixError(
            "Healthy Windows upgrade did not satisfy source/target/health/journal "
            f"gates: source_started={source_started!r}, "
            f"target_started={target_started!r}, "
            f"local_health_passed={local_health_passed!r}, "
            f"journal_committed={journal_committed!r}, "
            f"current_tree_matches={current_tree == target_tree!r}, "
            f"config_preserved={_read_marker(layout.config_user) == 'source'!r}, "
            f"recovery_clean={recovery_clean!r}, "
            f"runtime_units={runtime_units!r}, last_operation={last!r}"
        )
    return (
        {
            "source_started": source_started,
            "target_started": target_started,
            "local_health_passed": local_health_passed,
            "journal_committed": journal_committed,
        },
        {
            "source_version_before": source_version,
            "target_version_after": target_version,
            "journal_status": "committed",
            "health_status": "healthy",
        },
    )


def _manual_restore(
    root: Path,
    *,
    port: int,
    source_version: str,
    target_version: str,
    source_tree: str,
    target_tree: str,
) -> tuple[dict[str, bool], dict[str, Any]]:
    env = _manager_environment(root, port)
    _launch_manager(root, env)
    client, status = _wait_manager(root, port, version=source_version)
    if status.get("health", {}).get("dicepp_version") != source_version:
        raise WindowsMatrixError("Source Manager did not start")
    _start_runtime(client)
    blocker = _PortBlocker(port)
    blocker.start()
    try:
        _confirm_upgrade(client, target_version)
        blocker.wait(timeout=45)
        _wait_current_version(root, target_version, timeout=120)
        target_failure_observed = True
        layout = InstanceLayout.from_root(root)
        recovery_dirs = [
            path
            for path in layout.manager_recovery_dir.iterdir()
            if path.is_dir() and len(path.name) == 32
        ]
        recovery_material_preserved = (
            len(recovery_dirs) == 1
            and (root / "DicePP-Recover.cmd").is_file()
            and (recovery_dirs[0] / "current").is_dir()
            and _tree_digest(recovery_dirs[0] / "current") == source_tree
        )
        if not recovery_material_preserved:
            raise WindowsMatrixError("Source recovery material was not preserved")
        _stop_root_processes(root)
        _write_user_config(layout.config_user, "target-mutated")
        recovery = subprocess.run(
            [os.environ["COMSPEC"], "/d", "/c", str(root / "DicePP-Recover.cmd")],
            cwd=root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=30,
            check=False,
        )
        if recovery.returncode != 0:
            raise WindowsMatrixError(
                "Root recovery script failed: "
                + recovery.stdout.decode("utf-8", errors="replace")[-2000:]
            )
        manual_restore_invoked = (
            _program_version(root) == source_version
            and _tree_digest(root / "current") == source_tree
            and (recovery_dirs[0] / "failed-current").is_dir()
            and _tree_digest(recovery_dirs[0] / "failed-current") == target_tree
            and (recovery_dirs[0] / "manual-restore.requested").is_file()
        )
        if not manual_restore_invoked:
            raise WindowsMatrixError("Root recovery did not swap whole program trees")
    finally:
        blocker.close()
    _stop_root_processes(root)
    _launch_manager(root, env)
    source_client, source_status = _wait_manager(
        root,
        port,
        version=source_version,
        timeout=90,
    )
    deadline = time.monotonic() + 60
    upgrade_status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        upgrade_status = asyncio.run(source_client.upgrade_status())
        last = upgrade_status.get("last_operation")
        detail = last.get("detail") if isinstance(last, dict) else None
        if isinstance(detail, dict) and detail.get("phase") == "manual_restored":
            break
        time.sleep(0.2)
    last = upgrade_status.get("last_operation")
    detail = last.get("detail") if isinstance(last, dict) else None
    try:
        source_status = _wait_runtime_healthy(source_client)
    except WindowsMatrixError as exc:
        raise WindowsMatrixError(f"{exc}; last_operation={last!r}") from exc
    runtime_units = source_status.get("runtime_units")
    data_restored = _read_marker(InstanceLayout.from_root(root).config_user) == "source"
    source_restarted = source_status.get("health", {}).get("dicepp_version") == source_version
    journal_manually_restored = (
        isinstance(last, dict)
        and last.get("status") == "failed"
        and isinstance(detail, dict)
        and detail.get("phase") == "manual_restored"
        and detail.get("rollback_status") == "succeeded"
        and detail.get("rolled_back") is True
        and isinstance(detail.get("manual_restore"), dict)
        and detail["manual_restore"].get("data_runtime_restored") is True
        and _runtime_is_healthy(runtime_units)
    )
    whole_program_tree_restored = _tree_digest(root / "current") == source_tree
    if not all(
        (
            target_failure_observed,
            recovery_material_preserved,
            manual_restore_invoked,
            whole_program_tree_restored,
            data_restored,
            source_restarted,
            journal_manually_restored,
        )
    ):
        raise WindowsMatrixError(
            "Manual Windows recovery did not satisfy program/data/runtime/journal "
            f"gates: target_failure_observed={target_failure_observed!r}, "
            f"recovery_material_preserved={recovery_material_preserved!r}, "
            f"manual_restore_invoked={manual_restore_invoked!r}, "
            f"whole_program_tree_restored={whole_program_tree_restored!r}, "
            f"data_restored={data_restored!r}, "
            f"source_restarted={source_restarted!r}, "
            f"journal_manually_restored={journal_manually_restored!r}, "
            f"runtime_units={runtime_units!r}, last_operation={last!r}"
        )
    return (
        {
            "target_failure_observed": target_failure_observed,
            "recovery_material_preserved": recovery_material_preserved,
            "manual_restore_invoked": manual_restore_invoked,
            "whole_program_tree_restored": whole_program_tree_restored,
            "data_restored": data_restored,
            "source_restarted": source_restarted,
            "journal_manually_restored": journal_manually_restored,
        },
        {
            "target_version_observed": target_version,
            "restored_version": source_version,
            "journal_status": "manually_restored",
            "recovery_trigger": "manual",
            "program_restore_mode": "whole_current_directory",
        },
    )


def _remove_temp_tree(path: Path) -> None:
    for _attempt in range(5):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            time.sleep(0.5)


def run_windows_scenario(
    context: dict[str, Any],
    diagnostics_dir: Path,
) -> dict[str, Any]:
    if os.name != "nt":
        raise WindowsMatrixError("Windows cross-version matrix requires Windows")
    scenario = context.get("scenario")
    if scenario not in WINDOWS_SCENARIOS:
        raise WindowsMatrixError(f"Unsupported Windows scenario: {scenario!r}")
    source_version = str(context.get("source_version") or "")
    target_version = str(context.get("target_version") or "")
    source_assets = _records_by_purpose(
        context.get("source_assets"),
        expected=_SOURCE_PURPOSES,
        label="source",
    )
    target_assets = _records_by_purpose(
        context.get("target_assets"),
        expected=_TARGET_PURPOSES,
        label="target",
    )
    validate_velopack_bundle(
        source_assets["velopack-bundle"]["resolved_path"],
        expected_dicepp_version=source_version,
        expected_channel="prerelease",
        expected_size=source_assets["velopack-bundle"]["size"],
        expected_sha256=source_assets["velopack-bundle"]["sha256"],
    )
    validate_velopack_bundle(
        target_assets["velopack-bundle"]["resolved_path"],
        expected_dicepp_version=target_version,
        expected_channel="prerelease",
        expected_size=target_assets["velopack-bundle"]["size"],
        expected_sha256=target_assets["velopack-bundle"]["sha256"],
    )
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="dicepp-windows-upgrade-matrix-"))
    root: Path | None = None
    port: int | None = None
    try:
        root, source_tree, target_tree = _prepare_instance(
            temporary,
            source_portable=source_assets["portable"]["resolved_path"],
            target_portable=target_assets["portable"]["resolved_path"],
            source_version=source_version,
            target_version=target_version,
            target_assets=target_assets,
        )
        port = _reserve_port()
        if scenario == "healthy_commit":
            assertions, observations = _healthy_commit(
                root,
                port=port,
                source_version=source_version,
                target_version=target_version,
                source_tree=source_tree,
                target_tree=target_tree,
            )
        else:
            assertions, observations = _manual_restore(
                root,
                port=port,
                source_version=source_version,
                target_version=target_version,
                source_tree=source_tree,
                target_tree=target_tree,
            )
        diagnostics = {
            "contract_version": 1,
            "scenario": scenario,
            "source_version": source_version,
            "target_version": target_version,
            "target_commit_sha": context.get("target_commit_sha"),
            "source_assets": {
                purpose: record["sha256"] for purpose, record in source_assets.items()
            },
            "target_assets": {
                purpose: record["sha256"] for purpose, record in target_assets.items()
            },
            "assertions": assertions,
            "observations": observations,
        }
        (diagnostics_dir / "redacted-diagnostics.json").write_text(
            json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {
            "contract_version": 1,
            "platform": "windows",
            "arch": "amd64",
            "source_version": source_version,
            "target_version": target_version,
            "scenario": scenario,
            "status": "passed",
            "assertions": assertions,
            "observations": observations,
        }
    finally:
        if root is not None:
            try:
                if port is not None:
                    _stop_runtime_best_effort(root, port)
            except (OSError, ValueError):
                pass
            _stop_root_processes(root)
        _remove_temp_tree(temporary)


__all__ = ["WindowsMatrixError", "run_windows_scenario"]
