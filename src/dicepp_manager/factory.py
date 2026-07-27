"""Composition root for the standalone Manager process."""

from __future__ import annotations

import asyncio
import os
import json
import hashlib
import shlex
import shutil
from pathlib import Path
import urllib.error
import urllib.request
from datetime import datetime, timezone

from packaging.version import InvalidVersion, Version

from .archive_coordinator import ArchiveCoordinator
from .config import ManagerSettings
from .deployment import DASHBOARD_DEFAULT_PORT
from .discovery import RuntimeUnitDiscovery
from .docker_runtime import DockerRuntimeAdapter, DockerSocketRuntimeAdapter
from .docker_upgrade import DockerSocketUpgradeExecutor
from ._file_utils import _atomic_copy, _atomic_json, _read_json_object
from .process_runtime import ProcessRuntimeAdapter
from .release import ReleaseManager
from .upgrade import (
    LinuxBundleUpgradeAdapter,
    UnsupportedUpgradeAdapter,
    UpgradeCompatibilityError,
    UpgradeCoordinator,
    WindowsVelopackUpgradeAdapter,
    _validate_process_identity,
)
from .update_guard import (
    UpdateGuardError,
    _validate_request as validate_update_guard_request,
    current_process_identity,
    inspect_process_identity,
)
from .owner import ManagerOwnerLock
from .runtime import RuntimeAdapter, UnavailableRuntimeAdapter
from .service import ManagerService
from .store import ManagerOperationStore


def create_runtime_adapter(settings: ManagerSettings) -> RuntimeAdapter:
    if settings.runtime in {"", "unavailable"}:
        return UnavailableRuntimeAdapter()
    if settings.runtime == "process":
        return ProcessRuntimeAdapter(
            runtime_unit_id=settings.runtime_unit_id,
            command=settings.process_command,
            cwd=settings.process_cwd,
            stop_timeout=settings.process_stop_timeout,
            log_path=settings.layout.runtime_log,
            identity_path=settings.layout.manager_state_dir / "runtime-process.json",
        )
    if settings.runtime == "docker":
        if settings.docker_command.startswith("unix://"):
            return DockerSocketRuntimeAdapter(
                socket_path=settings.docker_command.removeprefix("unix://"),
                allowed_runtime_units={settings.runtime_unit_id},
                timeout=settings.docker_timeout,
            )
        return DockerRuntimeAdapter(
            docker_command=settings.docker_command,
            allowed_runtime_units={settings.runtime_unit_id},
            timeout=settings.docker_timeout,
        )
    raise ValueError(f"Unsupported Manager runtime adapter: {settings.runtime!r}")


def create_manager_service(settings: ManagerSettings) -> ManagerService:
    for directory in (
        settings.layout.manager_state_dir,
        settings.layout.manager_packages_dir,
        settings.layout.manager_backups_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    owner = ManagerOwnerLock(settings.layout.manager_state_dir)
    owner.acquire()
    try:
        adapter = create_runtime_adapter(settings)
        discovery = RuntimeUnitDiscovery(
            settings.layout,
            runtime_unit_id=settings.runtime_unit_id,
            adapter=settings.runtime,
        )
        store = ManagerOperationStore(settings.layout.manager_db)
        store.recover_incomplete_operations()
        service = ManagerService(
            unit_provider=discovery.list_units,
            runtime_adapter=adapter,
            store=store,
            state_dir=settings.layout.manager_state_dir,
            owner_lock=owner,
        )
        service.archive_coordinator = ArchiveCoordinator(
            layout=settings.layout,
            service=service,
            dashboard_probe=lambda: _dashboard_probe(),
            control_probe=lambda: _control_channel_probe(),
        )
        service.release_manager = ReleaseManager(
            layout=settings.layout,
            github_api=settings.github_api,
            protected_versions_loader=store.protected_upgrade_versions,
        )
        if os.name == "nt":
            _prepare_stable_update_guard(settings.layout.root)
            apply_command = os.environ.get("DICEPP_VELOPACK_APPLY_COMMAND", "")
            guard = settings.layout.root / "DicePP-UpdateGuard.exe"
            update_exe = settings.layout.root / "Update.exe"
            launcher = settings.layout.root / "DicePP.exe"
            if not apply_command and update_exe.is_file():
                install_command = [
                    str(update_exe),
                    "--rootDir",
                    str(settings.layout.root),
                    "--packageDir",
                    "{package_dir}",
                    "apply",
                    "--norestart",
                    "-p",
                    "{package}",
                ]
            else:
                install_command = shlex.split(apply_command, posix=False)
            if guard.is_file() and install_command and launcher.is_file():
                platform_adapter = WindowsVelopackUpgradeAdapter(
                    layout=settings.layout,
                    guard_command=[str(guard)],
                    install_command=install_command,
                    restart_command=[str(launcher)],
                    process_identity_loader=current_process_identity,
                    bundled_guard_path=(
                        Path(
                            os.environ.get(
                                "DICEPP_APP_DIR",
                                str(settings.layout.root),
                            )
                        ).resolve()
                        / "DicePP-UpdateGuard.exe"
                    ),
                    health_url=(
                        f"http://127.0.0.1:{settings.port}/v1/health"
                    ),
                    auth_token_path=(
                        settings.token_path or settings.layout.manager_token
                    ),
                    rollback_package_fetcher=(
                        service.release_manager.fetch_rollback_package
                    ),
                )
            else:
                platform_adapter = UnsupportedUpgradeAdapter(
                    "windows",
                    "UpdateGuard/Velopack command boundary is not configured; "
                    "use a manual Windows update",
                )
        else:
            compose = settings.layout.root / "docker-compose.yml"
            if isinstance(adapter, DockerSocketRuntimeAdapter) and compose.is_file():
                platform_adapter = LinuxBundleUpgradeAdapter(
                    layout=settings.layout,
                    executor=DockerSocketUpgradeExecutor(adapter),
                    current_compose=compose,
                )
            else:
                platform_adapter = UnsupportedUpgradeAdapter(
                    "linux",
                    "Automatic Linux installation requires the Docker socket "
                    "adapter and a read-only current docker-compose.yml mount",
                )
        service.upgrade_coordinator = UpgradeCoordinator(
            layout=settings.layout,
            service=service,
            archive_coordinator=service.archive_coordinator,
            release_manager=service.release_manager,
            platform_adapter=platform_adapter,
        )
        if os.name == "nt" and isinstance(
            platform_adapter, WindowsVelopackUpgradeAdapter
        ):
            cleanup_terminal_update_guard_transactions(
                settings.layout.root,
                journal_loader=store.get_journal,
            )
            service.pending_update_guard_resume = (
                _find_resumable_update_guard_request(settings.layout.root)
            )
        return service
    except BaseException:
        owner.release()
        raise


def _prepare_stable_update_guard(instance_root: Path) -> None:
    """Seed the version-external guard without touching instance data."""
    program_dir = Path(os.environ.get("DICEPP_APP_DIR", str(instance_root))).resolve()
    source = program_dir / "DicePP-UpdateGuard.exe"
    target = instance_root.resolve() / "DicePP-UpdateGuard.exe"
    if source == target or not source.exists():
        return
    if source.is_symlink() or not source.is_file():
        raise ValueError("Versioned UpdateGuard is not a regular file")
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ValueError("Stable UpdateGuard target is not a regular file")
    if target.is_file() and _sha256_path(source) == _sha256_path(target):
        return
    if _has_active_update_guard_transaction(instance_root):
        # The stable executable may be running and owns this protocol version.
        # Refresh only after its transaction reaches a terminal marker.
        return
    _atomic_copy(source, target)


def _has_active_update_guard_transaction(instance_root: Path) -> bool:
    guard_dir = instance_root / "manager" / "state" / "update-guard"
    if not guard_dir.is_dir() or guard_dir.is_symlink():
        return False
    for request in guard_dir.glob("*/request.json"):
        transaction_dir = request.parent
        if request.is_symlink() or not request.is_file():
            continue
        try:
            request_value = validate_update_guard_request(
                _read_json_object(request)
            )
            _validate_guard_resume_paths(
                request_value,
                request_path=request,
                transaction_dir=transaction_dir,
                instance_root=instance_root,
            )
        except (OSError, ValueError, UpdateGuardError):
            return True
        try:
            terminal = _load_bound_guard_terminal(request_value)
        except (OSError, ValueError):
            return True
        if terminal is None:
            return True
        guard_marker = transaction_dir / "guard.json"
        if not guard_marker.is_file() or guard_marker.is_symlink():
            return True
        try:
            guard = _read_json_object(guard_marker)
            identity = guard.get("guard_identity")
        except (OSError, ValueError):
            return True
        marker_matches = (
            guard.get("format_version") == 2
            and guard.get("transaction_id")
            == request_value["transaction_id"]
            and guard.get("target_version")
            == request_value["target_version"]
        )
        if marker_matches and guard.get("status") == "exited":
            continue
        if (
            marker_matches
            and guard.get("status") == "running"
            and isinstance(identity, dict)
            and inspect_process_identity(identity.get("pid", 0)) != identity
        ):
            continue
        return True
    return False


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _find_resumable_update_guard_request(
    instance_root: Path,
    *,
    identity_loader=inspect_process_identity,
) -> dict | None:
    guard_root = (
        instance_root.resolve() / "manager" / "state" / "update-guard"
    )
    if not guard_root.is_dir() or guard_root.is_symlink():
        return None
    candidates: list[dict] = []
    for request_path in guard_root.glob("*/request.json"):
        transaction_dir = request_path.parent
        if (
            request_path.is_symlink()
            or not request_path.is_file()
            or transaction_dir.parent != guard_root
            or transaction_dir.is_symlink()
        ):
            raise RuntimeError(
                "Invalid active UpdateGuard request requires manual recovery"
            )
        try:
            request = _load_update_guard_resume_request(
                instance_root,
                request_path,
            )
        except (OSError, ValueError, UpdateGuardError) as exc:
            raise RuntimeError(
                "Invalid active UpdateGuard request requires manual recovery"
            ) from exc
        try:
            if _guard_request_is_terminal(request):
                continue
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                "Conflicting UpdateGuard terminal markers require manual recovery"
            ) from exc
        guard_state, _guard_identity = _classify_bound_guard_process(
            request,
            identity_loader=identity_loader,
        )
        candidates.append(
            {
                "request_path": request_path.resolve(),
                "guard_state": guard_state,
                "guard_running": guard_state == "live",
            }
        )
    if len(candidates) > 1:
        raise RuntimeError(
            "Multiple active UpdateGuard rollback requests require manual recovery"
        )
    return candidates[0] if candidates else None


def _load_update_guard_resume_request(
    instance_root: Path,
    request_path: Path,
) -> dict:
    root = instance_root.resolve()
    guard_root = root / "manager" / "state" / "update-guard"
    resolved_request = request_path.resolve()
    transaction_dir = resolved_request.parent
    if (
        request_path.is_symlink()
        or not request_path.is_file()
        or guard_root.is_symlink()
        or transaction_dir.parent != guard_root
        or transaction_dir.is_symlink()
        or resolved_request.name != "request.json"
    ):
        raise ValueError("UpdateGuard request path is outside the stable root")
    request = validate_update_guard_request(
        _read_json_object(resolved_request)
    )
    _validate_guard_resume_paths(
        request,
        request_path=resolved_request,
        transaction_dir=transaction_dir,
        instance_root=root,
    )
    return request


def _classify_bound_guard_process(
    request: dict,
    *,
    identity_loader=inspect_process_identity,
) -> tuple[str, dict | None]:
    marker_path = Path(request["guard_marker"])
    if not marker_path.exists():
        return "missing", None
    try:
        identity = _load_bound_guard_identity(request)
    except (OSError, ValueError):
        return "invalid", None
    if identity_loader(identity["pid"]) == identity:
        return "live", identity
    return "dead", identity


def _load_bound_guard_identity(request: dict) -> dict:
    marker_path = Path(request["guard_marker"])
    if marker_path.is_symlink() or not marker_path.is_file():
        raise ValueError("UpdateGuard marker is unavailable")
    marker = _read_json_object(marker_path)
    if (
        marker.get("format_version") != 2
        or marker.get("status") != "running"
        or marker.get("transaction_id") != request["transaction_id"]
        or marker.get("target_version") != request["target_version"]
    ):
        raise ValueError("UpdateGuard marker binding mismatch")
    try:
        return _validate_process_identity(marker.get("guard_identity"))
    except UpgradeCompatibilityError as exc:
        raise ValueError("UpdateGuard process identity is invalid") from exc


def _guard_request_is_terminal(request: dict) -> bool:
    return _load_bound_guard_terminal(request) is not None


def _validate_guard_resume_paths(
    request: dict,
    *,
    request_path: Path,
    transaction_dir: Path,
    instance_root: Path,
) -> None:
    root = instance_root.resolve()
    expected_request = (transaction_dir / "request.json").resolve()
    if request_path.resolve() != expected_request:
        raise ValueError("UpdateGuard request path mismatch")
    for field, filename in (
        ("guard_marker", "guard.json"),
        ("started_marker", "started.json"),
        ("health_marker", "health.json"),
        ("rollback_marker", "rollback.json"),
    ):
        path = Path(request[field])
        if path.resolve(strict=False) != (transaction_dir / filename).resolve():
            raise ValueError(f"UpdateGuard {field} escapes transaction")
    rollback_package = Path(request["rollback_package"])
    if rollback_package.parent.resolve() != transaction_dir.resolve():
        raise ValueError("UpdateGuard rollback package escapes transaction")
    target_package = Path(request["package"]).resolve(strict=False)
    if not target_package.is_relative_to(
        (root / "manager" / "packages").resolve()
    ):
        raise ValueError("UpdateGuard target package escapes Manager cache")
    for field in ("auth_token_path",):
        if not Path(request[field]).resolve(strict=False).is_relative_to(root):
            raise ValueError(f"UpdateGuard {field} escapes instance")


async def resume_interrupted_update_guard(service) -> dict | None:
    pending = getattr(service, "pending_update_guard_resume", None)
    if not isinstance(pending, dict):
        return None
    coordinator = service.upgrade_coordinator
    adapter = getattr(coordinator, "platform_adapter", None)
    if not isinstance(adapter, WindowsVelopackUpgradeAdapter):
        raise RuntimeError("UpdateGuard resume has no Windows adapter")
    request_path = Path(pending["request_path"])
    request = _load_update_guard_resume_request(
        adapter.layout.root,
        request_path,
    )
    try:
        if _guard_request_is_terminal(request):
            service.pending_update_guard_resume = None
            return None
    except (OSError, ValueError) as exc:
        service.set_startup_maintenance_gate(True)
        raise RuntimeError(
            "UpdateGuard terminal state requires manual recovery"
        ) from exc

    try:
        program_state = _classify_guard_resume_program(adapter, request)
    except (InvalidVersion, TypeError) as exc:
        service.set_startup_maintenance_gate(True)
        raise RuntimeError(
            "UpdateGuard resume cannot determine the current program version"
        ) from exc

    guard_state, _guard_identity = _classify_bound_guard_process(
        request,
        identity_loader=inspect_process_identity,
    )
    guard_running = guard_state == "live"
    if program_state == "source" and guard_running:
        # The rollback package is already active.  Stopping this Manager would
        # recreate the old apply->start->terminal race.  Keep normal work gated
        # while the exact Guard publishes its terminal marker; the API lifespan
        # then performs data-only transaction recovery.
        service.set_startup_maintenance_gate(True)
        service.pending_update_guard_resume = None
        return {
            "request": str(request_path.resolve()),
            "guard_pid": None,
            "reused_running_guard": guard_running,
            "awaiting_terminal": True,
        }
    if program_state not in {"source", "target"}:
        service.set_startup_maintenance_gate(True)
        raise RuntimeError(
            "UpdateGuard resume found an unexpected current program version"
        )

    # The scan happened before ASGI startup.  Re-read every authority immediately
    # before mutating the request or process state so a completed Guard cannot be
    # relaunched from a stale cached candidate.
    request = _load_update_guard_resume_request(
        adapter.layout.root,
        request_path,
    )
    try:
        if _guard_request_is_terminal(request):
            service.pending_update_guard_resume = None
            return None
    except (OSError, ValueError) as exc:
        service.set_startup_maintenance_gate(True)
        raise RuntimeError(
            "UpdateGuard terminal state requires manual recovery"
        ) from exc
    try:
        program_state = _classify_guard_resume_program(adapter, request)
    except (InvalidVersion, TypeError) as exc:
        service.set_startup_maintenance_gate(True)
        raise RuntimeError(
            "UpdateGuard resume cannot determine the current program version"
        ) from exc
    if program_state not in {"source", "target"}:
        service.set_startup_maintenance_gate(True)
        raise RuntimeError(
            "UpdateGuard resume found an unexpected current program version"
        )
    guard_state, _guard_identity = _classify_bound_guard_process(
        request,
        identity_loader=inspect_process_identity,
    )
    guard_running = guard_state == "live"
    if program_state == "source" and guard_running:
        service.set_startup_maintenance_gate(True)
        service.pending_update_guard_resume = None
        return {
            "request": str(request_path.resolve()),
            "guard_pid": None,
            "reused_running_guard": True,
            "awaiting_terminal": True,
        }
    if program_state == "target" and guard_running:
        try:
            handoff_phase = _load_guard_nonterminal_phase(request)
        except (OSError, ValueError) as exc:
            service.set_startup_maintenance_gate(True)
            raise RuntimeError(
                "UpdateGuard hand-off phase requires manual recovery"
            ) from exc
        if handoff_phase in {"pristine", "started_no_health"}:
            # The live Guard is waiting for this target Manager to publish its
            # exact started marker and authenticated health.  Continue normal
            # coordinator recovery instead of shutting down the hand-off peer.
            service.set_startup_maintenance_gate(True)
            service.pending_update_guard_resume = None
            return None
    guard_never_started = False
    if program_state == "source":
        if guard_state == "dead":
            try:
                _complete_source_active_guard_recovery(
                    adapter,
                    request_path,
                )
            except (OSError, ValueError, UpdateGuardError) as exc:
                service.set_startup_maintenance_gate(True)
                raise RuntimeError(
                    "UpdateGuard source recovery requires manual recovery"
                ) from exc
            service.pending_update_guard_resume = None
            return None
        guard_never_started = (
            guard_state == "missing"
            and _request_proves_guard_never_started(request)
        )
        if not guard_never_started:
            service.set_startup_maintenance_gate(True)
            raise RuntimeError(
                "UpdateGuard identity is unavailable; manual recovery is required"
            )
    elif guard_state not in {"live", "dead"}:
        service.set_startup_maintenance_gate(True)
        raise RuntimeError(
            "UpdateGuard identity is unavailable; manual recovery is required"
        )
    process = None
    if guard_state != "live":
        request["manager_identity"] = current_process_identity()
        _atomic_json(request_path, request)
        request = _load_update_guard_resume_request(
            adapter.layout.root,
            request_path,
        )
        try:
            if _guard_request_is_terminal(request):
                service.pending_update_guard_resume = None
                return None
        except (OSError, ValueError) as exc:
            service.set_startup_maintenance_gate(True)
            raise RuntimeError(
                "UpdateGuard terminal state requires manual recovery"
            ) from exc
        guard_state, _guard_identity = _classify_bound_guard_process(
            request,
            identity_loader=inspect_process_identity,
        )
        if guard_state == "live":
            guard_running = True
        elif guard_state == "dead" or (
            guard_never_started
            and guard_state == "missing"
            and _request_proves_guard_never_started(request)
        ):
            process, _guard_executable = adapter.start_guard(request_path)
        else:
            service.set_startup_maintenance_gate(True)
            raise RuntimeError(
                "UpdateGuard identity changed before resume; "
                "manual recovery is required"
            )
    service.set_startup_maintenance_gate(True)
    service.pending_update_guard_resume = None
    service.request_shutdown("windows_update_guard_resume")
    return {
        "request": str(request_path),
        "guard_pid": process.pid if process is not None else None,
        "reused_running_guard": guard_running,
        "awaiting_terminal": False,
    }


def _classify_guard_resume_program(
    adapter: WindowsVelopackUpgradeAdapter,
    request: dict,
) -> str:
    current_version = Version(adapter.version_loader())
    if current_version == Version(request["source_version"]):
        return "source"
    if current_version == Version(request["target_version"]):
        return "target"
    return "unexpected"


def _request_proves_guard_never_started(request: dict) -> bool:
    try:
        if current_process_identity() != request["manager_identity"]:
            return False
    except (OSError, ValueError, UpdateGuardError):
        return False
    return all(
        not path.exists() and not path.is_symlink()
        for path in (
            Path(request["guard_marker"]),
            Path(request["started_marker"]),
            Path(request["health_marker"]),
            Path(request["rollback_marker"]),
        )
    )


def _load_bound_guard_terminal(request: dict) -> dict | None:
    terminal: list[dict] = []
    healthy_marker = False
    rollback_marker_present = False
    started = None
    started_path = Path(request["started_marker"])
    if started_path.is_symlink() or (
        started_path.exists() and not started_path.is_file()
    ):
        raise ValueError("UpdateGuard started marker is invalid")
    if started_path.is_file():
        started = _read_json_object(started_path)
        if (
            started.get("format_version") != 2
            or started.get("transaction_id") != request["transaction_id"]
            or started.get("target_version") != request["target_version"]
            or started.get("status") not in {"started", "failed"}
        ):
            raise ValueError("UpdateGuard started marker binding mismatch")
        try:
            _validate_process_identity(started.get("manager_identity"))
        except UpgradeCompatibilityError as exc:
            raise ValueError(
                "UpdateGuard started Manager identity is invalid"
            ) from exc

    health_path = Path(request["health_marker"])
    if health_path.is_symlink() or (
        health_path.exists() and not health_path.is_file()
    ):
        raise ValueError("UpdateGuard health marker is invalid")
    if health_path.is_file():
        health = _read_json_object(health_path)
        if (
            health.get("format_version") != 2
            or health.get("transaction_id") != request["transaction_id"]
            or health.get("target_version") != request["target_version"]
            or health.get("status") not in {"healthy", "failed"}
        ):
            raise ValueError("UpdateGuard health marker binding mismatch")
        try:
            health_identity = _validate_process_identity(
                health.get("manager_identity")
            )
        except UpgradeCompatibilityError as exc:
            raise ValueError(
                "UpdateGuard health Manager identity is invalid"
            ) from exc
        if health["status"] == "healthy":
            if (
                not isinstance(started, dict)
                or started.get("status") != "started"
                or _validate_process_identity(
                    started.get("manager_identity")
                )
                != health_identity
            ):
                raise ValueError(
                    "Healthy UpdateGuard marker has no matching started marker"
                )
            healthy_marker = True
            terminal.append(health)

    rollback_path = Path(request["rollback_marker"])
    if rollback_path.is_symlink() or (
        rollback_path.exists() and not rollback_path.is_file()
    ):
        raise ValueError("UpdateGuard rollback marker is invalid")
    if rollback_path.is_file():
        rollback_marker_present = True
        rollback = _read_json_object(rollback_path)
        if (
            rollback.get("format_version") != 2
            or rollback.get("transaction_id") != request["transaction_id"]
            or rollback.get("target_version") != request["target_version"]
            or rollback.get("source_version") != request["source_version"]
            or rollback.get("status")
            not in {
                "program_rollback_started",
                "program_rolled_back",
                "program_rollback_failed",
            }
        ):
            raise ValueError("UpdateGuard rollback marker binding mismatch")
        if rollback["status"] in {
            "program_rolled_back",
            "program_rollback_failed",
        }:
            terminal.append(rollback)
    if healthy_marker and rollback_marker_present:
        raise ValueError("Conflicting UpdateGuard health and rollback markers")
    if len(terminal) > 1:
        raise ValueError("Conflicting UpdateGuard terminal markers")
    return terminal[0] if terminal else None


def _source_recovery_terminal(request: dict) -> dict | None:
    terminal = _load_bound_guard_terminal(request)
    if terminal is not None and terminal.get("status") == "healthy":
        raise ValueError(
            "Healthy target marker conflicts with active source Manager"
        )
    return terminal


def _load_guard_nonterminal_phase(request: dict) -> str:
    if _load_bound_guard_terminal(request) is not None:
        raise ValueError("UpdateGuard request is already terminal")
    started_path = Path(request["started_marker"])
    if started_path.is_symlink() or (
        started_path.exists() and not started_path.is_file()
    ):
        raise ValueError("UpdateGuard started marker is invalid")
    started = None
    if started_path.is_file():
        started = _read_json_object(started_path)
        if (
            started.get("format_version") != 2
            or started.get("transaction_id") != request["transaction_id"]
            or started.get("target_version") != request["target_version"]
            or started.get("status") not in {"started", "failed"}
        ):
            raise ValueError("UpdateGuard started marker binding mismatch")

    health = None
    health_path = Path(request["health_marker"])
    if health_path.is_file():
        health = _read_json_object(health_path)
    rollback = None
    rollback_path = Path(request["rollback_marker"])
    if rollback_path.is_file():
        rollback = _read_json_object(rollback_path)
    if (
        isinstance(rollback, dict)
        and rollback.get("status") == "program_rollback_started"
    ) or (
        isinstance(health, dict) and health.get("status") == "failed"
    ) or (
        isinstance(started, dict) and started.get("status") == "failed"
    ):
        return "rollback_required"
    if started is None and health is None and rollback is None:
        return "pristine"
    if (
        isinstance(started, dict)
        and started.get("status") == "started"
        and health is None
        and rollback is None
    ):
        return "started_no_health"
    raise ValueError("Unsupported UpdateGuard nonterminal marker combination")


def _complete_source_active_guard_recovery(
    adapter: WindowsVelopackUpgradeAdapter,
    request_path: Path,
) -> dict:
    instance_root = adapter.layout.root
    request = _load_update_guard_resume_request(
        instance_root,
        request_path,
    )
    if _classify_guard_resume_program(adapter, request) != "source":
        raise ValueError("Current Manager is not the requested source version")
    terminal = _source_recovery_terminal(request)
    if terminal is not None:
        return terminal
    guard_state, _guard_identity = _classify_bound_guard_process(
        request,
        identity_loader=inspect_process_identity,
    )
    if guard_state == "live":
        raise ValueError("Exact UpdateGuard is still running")
    if guard_state != "dead":
        raise ValueError("Exact UpdateGuard death cannot be proven")
    identity = current_process_identity()
    executable = Path(identity["executable"]).resolve()
    if not executable.is_relative_to(instance_root.resolve()):
        raise ValueError("Current Manager executable is outside the instance")
    latest = _load_update_guard_resume_request(
        instance_root,
        request_path,
    )
    if latest != request:
        raise ValueError("UpdateGuard request changed during source recovery")
    if _classify_guard_resume_program(adapter, latest) != "source":
        raise ValueError("Current Manager is not the requested source version")
    terminal = _source_recovery_terminal(latest)
    if terminal is not None:
        return terminal
    guard_state, _guard_identity = _classify_bound_guard_process(
        latest,
        identity_loader=inspect_process_identity,
    )
    if guard_state != "dead":
        raise ValueError("Exact UpdateGuard death cannot be proven")
    rollback_path = Path(request["rollback_marker"])
    if rollback_path.is_symlink():
        raise ValueError("UpdateGuard rollback marker is a symlink")
    if rollback_path.is_file():
        rollback = _read_json_object(rollback_path)
        if (
            rollback.get("format_version") != 2
            or rollback.get("transaction_id") != request["transaction_id"]
            or rollback.get("target_version") != request["target_version"]
            or rollback.get("source_version") != request["source_version"]
            or rollback.get("status") != "program_rollback_started"
        ):
            raise ValueError("UpdateGuard rollback marker binding mismatch")
    else:
        rollback = {
            "format_version": 2,
            "transaction_id": request["transaction_id"],
            "target_version": request["target_version"],
            "source_version": request["source_version"],
        }
    # This Manager's running code already proves the stable program directory
    # is the requested source version.  Completing the marker is safer than
    # stopping it and reapplying the same rollback package after a dead Guard.
    rollback.update(
        {
            "manager_identity": identity,
            "status": "program_rolled_back",
            "recovered_from_source_manager": True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    _atomic_json(rollback_path, rollback)
    return rollback


async def wait_for_update_guard_terminal(
    adapter: WindowsVelopackUpgradeAdapter,
    request_path: Path,
    *,
    poll_interval: float = 0.1,
) -> dict:
    instance_root = adapter.layout.root
    initial = _load_update_guard_resume_request(instance_root, request_path)
    identity = (
        initial["transaction_id"],
        initial["source_version"],
        initial["target_version"],
    )
    while True:
        request = _load_update_guard_resume_request(
            instance_root,
            request_path,
        )
        if (
            request["transaction_id"],
            request["source_version"],
            request["target_version"],
        ) != identity:
            raise RuntimeError(
                "UpdateGuard request changed while awaiting terminal recovery"
            )
        terminal = _source_recovery_terminal(request)
        if terminal is not None:
            return request
        if _classify_guard_resume_program(adapter, request) != "source":
            raise RuntimeError(
                "Current program changed while awaiting UpdateGuard terminal recovery"
            )
        guard_state, _guard_identity = _classify_bound_guard_process(
            request,
            identity_loader=inspect_process_identity,
        )
        if guard_state == "dead":
            _complete_source_active_guard_recovery(adapter, request_path)
            return _load_update_guard_resume_request(
                instance_root,
                request_path,
            )
        if guard_state != "live":
            raise RuntimeError(
                "UpdateGuard identity changed while awaiting terminal recovery"
            )
        await asyncio.sleep(poll_interval)


async def refresh_stable_update_guard_when_safe(
    instance_root: Path,
    *,
    timeout: float = 30.0,
    journal_loader=None,
) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        try:
            _prepare_stable_update_guard(instance_root)
            program_dir = Path(
                os.environ.get("DICEPP_APP_DIR", str(instance_root))
            ).resolve()
            source = program_dir / "DicePP-UpdateGuard.exe"
            target = instance_root.resolve() / "DicePP-UpdateGuard.exe"
            if (
                source.is_file()
                and target.is_file()
                and _sha256_path(source) == _sha256_path(target)
            ):
                cleanup_terminal_update_guard_transactions(
                    instance_root,
                    journal_loader=journal_loader,
                )
                return True
        except PermissionError:
            # Windows keeps the running executable locked until Guard exits.
            pass
        if asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(0.1)


def cleanup_terminal_update_guard_transactions(
    instance_root: Path,
    *,
    identity_loader=inspect_process_identity,
    journal_loader=None,
) -> list[str]:
    guard_root = (
        instance_root.resolve() / "manager" / "state" / "update-guard"
    )
    if not guard_root.is_dir() or guard_root.is_symlink():
        return []
    removed: list[str] = []
    for request_path in guard_root.glob("*/request.json"):
        transaction_dir = request_path.parent
        if (
            request_path.is_symlink()
            or not request_path.is_file()
            or transaction_dir.parent != guard_root
            or transaction_dir.is_symlink()
        ):
            continue
        try:
            request = validate_update_guard_request(
                _read_json_object(request_path)
            )
            _validate_guard_resume_paths(
                request,
                request_path=request_path,
                transaction_dir=transaction_dir,
                instance_root=instance_root,
            )
            guard = _read_json_object(Path(request["guard_marker"]))
            terminal = _load_bound_guard_terminal(request)
        except (OSError, ValueError, UpdateGuardError):
            continue
        identity = guard.get("guard_identity")
        guard_exited = (
            guard.get("format_version") == 2
            and guard.get("transaction_id") == request["transaction_id"]
            and guard.get("target_version") == request["target_version"]
            and (
                guard.get("status") == "exited"
                or (
                    guard.get("status") == "running"
                    and isinstance(identity, dict)
                    and identity_loader(identity.get("pid", 0)) != identity
                )
            )
        )
        if terminal is None or not guard_exited:
            continue
        if journal_loader is None:
            continue
        journal = journal_loader(request["transaction_id"])
        if (
            not isinstance(journal, dict)
            or journal.get("status")
            in {"running", "interrupted", "rollback_failed"}
        ):
            continue
        resolved = transaction_dir.resolve()
        if resolved.parent != guard_root:
            raise RuntimeError("UpdateGuard cleanup target escaped state root")
        shutil.rmtree(resolved)
        removed.append(transaction_dir.name)
    return removed


def _dashboard_health_payload() -> tuple[str, int | None, dict]:
    url = os.environ.get(
        "DICEPP_DASHBOARD_HEALTH_URL",
        f"http://127.0.0.1:{DASHBOARD_DEFAULT_PORT}/api/health",
    )
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            status = response.status
            raw = response.read(64 * 1024)
    except urllib.error.HTTPError:
        return url, None, {}
    except (OSError, urllib.error.URLError, TimeoutError):
        return url, None, {}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    return url, status, payload if isinstance(payload, dict) else {}


def _dashboard_probe() -> dict:
    url, status, payload = _dashboard_health_payload()
    valid = (
        status == 200
        and payload.get("status") == "ok"
        and payload.get("component") == "dashboard"
    )
    return {
        "ok": valid,
        "status": "ok" if valid else "failed",
        "http_status": status,
        "url": url,
    }


def _parse_control_heartbeat(raw: str) -> datetime | None:
    """Parse a Dashboard control heartbeat.

    Current Dashboards persist ISO-8601 UTC strings; older versions wrote
    bare epoch-second numbers, which are still accepted here so a stale
    Dashboard does not block Manager health gates.
    """
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        pass
    else:
        if parsed.tzinfo is None:
            # Naive ISO heartbeats are UTC, matching the Dashboard-side
            # _heartbeat_to_epoch contract.
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    try:
        return datetime.fromtimestamp(float(raw), timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _control_channel_probe() -> dict:
    url, status, payload = _dashboard_health_payload()
    if (
        status != 200
        or payload.get("status") != "ok"
        or payload.get("component") != "dashboard"
    ):
        return {
            "ok": False,
            "status": "failed",
            "url": url,
            "message": "Dashboard health is unavailable",
        }
    control = payload.get("control")
    raw_heartbeat = (
        control.get("latest_heartbeat") if isinstance(control, dict) else None
    )
    if not isinstance(raw_heartbeat, str) or not raw_heartbeat:
        return {
            "ok": False,
            "status": "failed",
            "url": url,
            "message": "No Bot control heartbeat",
        }
    heartbeat = _parse_control_heartbeat(raw_heartbeat)
    if heartbeat is None:
        return {
            "ok": False,
            "status": "failed",
            "url": url,
            "message": "Invalid Bot control heartbeat",
        }
    age = (
        datetime.now(timezone.utc) - heartbeat.astimezone(timezone.utc)
    ).total_seconds()
    return {
        "ok": age <= 120,
        "status": "ok" if age <= 120 else "failed",
        "url": url,
        "heartbeat_age_seconds": age,
        "heartbeat": heartbeat.astimezone(timezone.utc).isoformat(),
    }
