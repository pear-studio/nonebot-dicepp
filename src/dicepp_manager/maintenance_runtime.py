"""Shared runtime primitives for destructive Manager maintenance workflows."""

from __future__ import annotations

import asyncio
import inspect
import json
from datetime import datetime
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from dicepp_data import DATA_CATALOG, InstanceLayout

from .archive import ArchiveError

if TYPE_CHECKING:
    from .service import MaintenanceReservation, MaintenanceSession, ManagerService


HealthProbe = Callable[[], bool | dict[str, Any] | Awaitable[bool | dict[str, Any]]]

CONTROL_GATE_ENFORCED = "enforced"
CONTROL_GATE_SKIPPED_NO_BOUND_BOTS = "skipped_no_bound_bots"
CONTROL_GATE_SKIPPED_NO_ACTIVE_CONTROL_CHANNEL = (
    "skipped_no_active_control_channel"
)
DEFAULT_CONTROL_HEALTH_TIMEOUT = 120.0

_CONTROL_GATE_SKIP_REASONS = {
    CONTROL_GATE_SKIPPED_NO_BOUND_BOTS: "no_bound_bots",
    CONTROL_GATE_SKIPPED_NO_ACTIVE_CONTROL_CHANNEL: "no_active_control_channel",
}


class MaintenanceRuntimeSupport:
    """Runtime stop/start, schema, and local health primitives shared by coordinators.

    This class deliberately does not own an archive or upgrade state machine.  Its
    methods operate only inside the maintenance session supplied by a coordinator,
    except for ``best_effort_restart`` which can safely acquire or reuse a lease.
    """

    def __init__(
        self,
        *,
        layout: InstanceLayout,
        service: ManagerService,
        control_probe: HealthProbe | None = None,
        health_timeout: float = 20.0,
        control_health_timeout: float = DEFAULT_CONTROL_HEALTH_TIMEOUT,
        health_interval: float = 0.5,
        health_consecutive: int = 3,
    ) -> None:
        self.layout = layout
        self.service = service
        self.control_probe = control_probe
        self.health_timeout = health_timeout
        self.control_health_timeout = control_health_timeout
        self.health_interval = health_interval
        self.health_consecutive = health_consecutive

    async def capture_control_baseline(self) -> tuple[Any, str]:
        """Capture a heartbeat and anchor the active-session control gate."""
        baseline = await self._probe_once(self.control_probe)
        heartbeat = baseline.get("heartbeat") if isinstance(baseline, dict) else None
        status = await self.service.status()
        bots = status.get("bots") if isinstance(status, dict) else None
        if not bots:
            return heartbeat, CONTROL_GATE_SKIPPED_NO_BOUND_BOTS
        active_sessions = (
            baseline.get("active_authenticated_sessions")
            if isinstance(baseline, dict)
            else None
        )
        if (
            not isinstance(active_sessions, int)
            or isinstance(active_sessions, bool)
            or active_sessions <= 0
            or baseline.get("ok") is not True
        ):
            return heartbeat, CONTROL_GATE_SKIPPED_NO_ACTIVE_CONTROL_CHANNEL
        return heartbeat, CONTROL_GATE_ENFORCED

    async def quiesce(
        self,
        maintenance: MaintenanceSession,
        *,
        state_callback: Callable[[list[str]], None] | None = None,
        require_known: bool = False,
    ) -> tuple[list[str], list[str]]:
        units = self.service.units()
        ids = [unit.runtime_unit_id for unit in units]
        statuses = await self.service.runtime_adapter.status(ids)
        if require_known:
            unknown = [
                unit_id
                for unit_id in ids
                if statuses.get(unit_id) is None
                or statuses[unit_id].runtime_state == "unknown"
            ]
            if unknown:
                raise ArchiveError(
                    "无法确认并安全停止 RuntimeUnit：" + "、".join(unknown)
                )
        original_running = [
            unit_id
            for unit_id, status in statuses.items()
            if status.runtime_state == "running"
        ]
        if state_callback is not None:
            state_callback(list(original_running))
        stopped: list[str] = []
        for unit_id in original_running:
            await maintenance.operate_runtime_unit(unit_id, "stop")
            stopped.append(unit_id)
        return original_running, stopped

    async def restart(
        self,
        maintenance: MaintenanceSession,
        runtime_unit_ids: list[str],
    ) -> None:
        for unit_id in runtime_unit_ids:
            await maintenance.operate_runtime_unit(unit_id, "start")

    async def best_effort_restart(
        self,
        runtime_unit_ids: list[str],
        *,
        maintenance_lease: MaintenanceReservation | None = None,
        allow_startup_recovery: bool = False,
    ) -> str | None:
        if not runtime_unit_ids:
            return None
        try:
            if maintenance_lease is not None:
                await self.restart(maintenance_lease.session, runtime_unit_ids)
            else:
                with self.service.maintenance(
                    timeout=1,
                    allow_startup_recovery=allow_startup_recovery,
                ) as maintenance:
                    await self.restart(maintenance, runtime_unit_ids)
        except Exception as exc:
            return str(exc) or type(exc).__name__
        return None

    async def best_effort_restore_state(
        self,
        runtime_unit_ids: list[str],
        *,
        allow_startup_recovery: bool = False,
    ) -> str | None:
        """Idempotently restore the exact captured Runtime running set."""

        try:
            with self.service.maintenance(
                timeout=1,
                allow_startup_recovery=allow_startup_recovery,
            ) as maintenance:
                units = self.service.units()
                known = {unit.runtime_unit_id for unit in units}
                desired = set(runtime_unit_ids)
                if not desired <= known:
                    missing = sorted(desired - known)
                    raise ArchiveError(
                        f"Captured RuntimeUnit is unavailable: {', '.join(missing)}"
                    )
                statuses = await self.service.runtime_adapter.status(sorted(known))
                for unit_id in sorted(known):
                    status = statuses.get(unit_id)
                    running = status is not None and status.runtime_state == "running"
                    if unit_id in desired and not running:
                        await maintenance.operate_runtime_unit(unit_id, "start")
                    elif unit_id not in desired and running:
                        await maintenance.operate_runtime_unit(unit_id, "stop")
        except Exception as exc:
            return str(exc) or type(exc).__name__
        return None

    def migrate_and_validate_schema(
        self,
        skip_paths: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        targets = _schema_targets()
        applied: list[dict[str, Any]] = []
        skipped = skip_paths or set()
        for match in DATA_CATALOG.collect(self.layout, "full"):
            if match.logical_path in skipped:
                continue
            asset = DATA_CATALOG.find_for_logical_path(match.logical_path)
            if asset is None or asset.schema is None:
                continue
            target = targets.get(asset.schema.name)
            if target is None:
                raise ArchiveError(f"SchemaTarget is unavailable: {asset.schema.name}")
            asset.schema.validate_target(target)
            from plugins.DicePP.core.data.schema.lifecycle import apply_schema_target

            result = apply_schema_target(match.path, target)
            applied.append(
                {
                    "path": match.logical_path,
                    "schema": asset.schema.name,
                    "from": result.current_version,
                    "to": result.target_version,
                    "applied_versions": result.applied_versions,
                }
            )
        return applied

    async def hard_health(
        self,
        expected_running: list[str],
        *,
        control_baseline: str | None = None,
        control_gate: str = CONTROL_GATE_ENFORCED,
        control_failure_is_warning: bool = False,
    ) -> dict[str, Any]:
        self.service.store.ensure_schema()
        config = self._validate_config()
        runtime = await self._wait_runtime_healthy(expected_running)
        warnings = [
            "External NapCat/QQ/GitHub/LLM services are not hard health checks"
        ]
        control_skip_reason = _CONTROL_GATE_SKIP_REASONS.get(control_gate)
        observe_optional_control = (
            control_failure_is_warning
            and control_gate == CONTROL_GATE_SKIPPED_NO_ACTIVE_CONTROL_CHANNEL
        )
        if expected_running and (
            control_skip_reason is None or observe_optional_control
        ):
            effective_baseline = None if observe_optional_control else control_baseline
            try:
                # The enforced target gate must allow real Bot authentication
                # and first-heartbeat startup time.  Post-rollback control is
                # warning-only and keeps the shorter local health budget so a
                # successful restoration is reported promptly.
                control = await self._run_probe(
                    self.control_probe,
                    "control",
                    timeout=(
                        self.health_timeout
                        if control_failure_is_warning
                        else self.control_health_timeout
                    ),
                    predicate=lambda result: _heartbeat_is_newer(
                        result.get("heartbeat"), effective_baseline
                    ),
                    failure_message=(
                        "Bot control channel did not reconnect after restart"
                        if observe_optional_control
                        else "Bot control heartbeat did not advance after restart"
                    ),
                )
            except Exception as exc:
                if not control_failure_is_warning:
                    raise
                warning = str(exc)
                control = {
                    "ok": False,
                    "status": "degraded",
                    "warning": warning,
                }
                warnings.append(warning)
        elif expected_running:
            control = {
                "status": "not_applicable",
                "reason": control_skip_reason,
            }
        else:
            control = {"status": "not_applicable"}
        # Recheck runtime after the slower control probe; an early healthy
        # observation is not enough to commit a maintenance transaction.
        runtime = await self._wait_runtime_healthy(expected_running)
        return {
            "manager_store": "ok",
            "config": config,
            "schema": "ok",
            "runtime_units": runtime,
            "control": control,
            "warnings": warnings,
        }

    def _validate_config(self) -> dict[str, Any]:
        files: list[str] = []
        candidates = [self.layout.config_user]
        if self.layout.config_bots_dir.exists():
            candidates.extend(sorted(self.layout.config_bots_dir.glob("*.json")))
        for path in candidates:
            if not path.exists():
                continue
            if path.is_symlink() or not path.is_file():
                raise ArchiveError(f"Configuration is not a regular file: {path.name}")
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ArchiveError(f"Configuration cannot be loaded: {path.name}") from exc
            if not isinstance(value, dict):
                raise ArchiveError(f"Configuration root must be an object: {path.name}")
            files.append(path.relative_to(self.layout.root).as_posix())
        return {"status": "ok", "files": files}

    async def _wait_runtime_healthy(
        self,
        expected_running: list[str],
    ) -> list[str]:
        if not expected_running:
            return []
        deadline = asyncio.get_running_loop().time() + self.health_timeout
        consecutive = 0
        last_error = "Runtime status unavailable"
        while asyncio.get_running_loop().time() < deadline:
            manager = await self.service.status()
            runtime_rows = {
                row["runtime_unit_id"]: row
                for row in manager.get("runtime_units", [])
                if isinstance(row, dict)
            }
            healthy = True
            for unit_id in expected_running:
                row = runtime_rows.get(unit_id)
                runtime = row.get("runtime") if isinstance(row, dict) else None
                if not isinstance(runtime, dict) or runtime.get("runtime_state") != "running":
                    healthy = False
                    last_error = f"RuntimeUnit did not remain running: {unit_id}"
                    break
                if runtime.get("health") in {"failed", "unhealthy", "unavailable"}:
                    healthy = False
                    last_error = f"RuntimeUnit health failed: {unit_id}"
                    break
            consecutive = consecutive + 1 if healthy else 0
            if consecutive >= self.health_consecutive:
                return expected_running
            await asyncio.sleep(self.health_interval)
        raise ArchiveError(last_error)

    async def _probe_once(self, probe: HealthProbe | None) -> dict[str, Any]:
        if probe is None:
            return {
                "status": "warning",
                "message": "health probe is not configured",
            }
        if inspect.iscoroutinefunction(probe):
            result = await probe()
        else:
            result = await asyncio.to_thread(probe)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, dict):
            return result
        return {
            "status": "ok" if result is not False else "failed",
            "ok": result is not False,
        }

    async def _run_probe(
        self,
        probe: HealthProbe | None,
        name: str,
        *,
        timeout: float,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
        failure_message: str | None = None,
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout
        consecutive = 0
        last: dict[str, Any] = {}
        while asyncio.get_running_loop().time() < deadline:
            last = await self._probe_once(probe)
            healthy = not (
                last.get("ok") is False
                or last.get("status") in {"failed", "unhealthy"}
            )
            if healthy and predicate is not None:
                healthy = predicate(last)
            consecutive = consecutive + 1 if healthy else 0
            if consecutive >= self.health_consecutive:
                return last
            await asyncio.sleep(self.health_interval)
        raise ArchiveError(failure_message or f"{name} health probe failed")


def _schema_targets() -> dict[str, Any]:
    """Load existing SchemaTarget definitions without duplicating migrations."""
    from plugins.DicePP.core.data.schema.bot_core import BOT_CORE_TARGET
    from plugins.DicePP.core.data.schema.bot_log import BOT_LOG_TARGET
    from plugins.DicePP.core.data.schema.instance import INSTANCE_TARGET
    from plugins.DicePP.module.persona.data.schema import PERSONA_TARGET

    return {
        target.name: target
        for target in (
            INSTANCE_TARGET,
            BOT_CORE_TARGET,
            BOT_LOG_TARGET,
            PERSONA_TARGET,
        )
    }


def _heartbeat_is_newer(value: Any, baseline: str | None) -> bool:
    if not isinstance(value, str):
        return False
    try:
        current = datetime.fromisoformat(value.replace("Z", "+00:00"))
        previous = (
            datetime.fromisoformat(baseline.replace("Z", "+00:00"))
            if baseline
            else None
        )
    except ValueError:
        return False
    return previous is None or current > previous
