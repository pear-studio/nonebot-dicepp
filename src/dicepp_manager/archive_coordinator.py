"""Durable Manager orchestration for archive creation and exact restore."""

from __future__ import annotations

import asyncio
import inspect
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4
from datetime import datetime

from dicepp_data import DATA_CATALOG, InstanceLayout

from .archive import (
    ArchiveError,
    apply_archive,
    create_archive,
    delete_archive,
    enforce_system_retention,
    estimate_archive,
    export_archive_path,
    import_archive,
    list_archives,
    plan_archive_restore,
    read_archive_detail,
    verify_archive,
)
from .models import ManagerOperation
from .service import MaintenanceReservation, ManagerService


HealthProbe = Callable[[], bool | dict[str, Any] | Awaitable[bool | dict[str, Any]]]
FaultHook = Callable[[str], None]

CONTROL_GATE_ENFORCED = "enforced"
CONTROL_GATE_SKIPPED_NO_BOUND_BOTS = "skipped_no_bound_bots"
CONTROL_GATE_SKIPPED_NO_ACTIVE_CONTROL_CHANNEL = (
    "skipped_no_active_control_channel"
)

_CONTROL_GATE_SKIP_REASONS = {
    CONTROL_GATE_SKIPPED_NO_BOUND_BOTS: "no_bound_bots",
    CONTROL_GATE_SKIPPED_NO_ACTIVE_CONTROL_CHANNEL: "no_active_control_channel",
}


class ArchiveTransactionError(ArchiveError):
    """Raised after a transaction has failed and compensation was attempted."""

    def __init__(self, message: str, *, detail: dict[str, Any]) -> None:
        self.detail = detail
        super().__init__(message)


class ArchiveCoordinator:
    def __init__(
        self,
        *,
        layout: InstanceLayout,
        service: ManagerService,
        control_probe: HealthProbe | None = None,
        fault_hook: FaultHook | None = None,
        health_timeout: float = 20.0,
        health_interval: float = 0.5,
        health_consecutive: int = 3,
    ) -> None:
        self.layout = layout
        self.service = service
        self.store = service.store
        self.control_probe = control_probe
        self.fault_hook = fault_hook
        self.health_timeout = health_timeout
        self.health_interval = health_interval
        self.health_consecutive = health_consecutive

    def _fault(self, phase: str) -> None:
        if self.fault_hook is not None:
            self.fault_hook(phase)

    def estimate(self, profile: str) -> dict:
        return estimate_archive(self.layout, profile)

    def list(self) -> list[dict]:
        return list_archives(layout=self.layout)

    def detail(self, filename: str) -> tuple[dict, dict]:
        return read_archive_detail(filename, layout=self.layout)

    def verify(self, filename: str) -> dict:
        return verify_archive(filename, layout=self.layout)

    def plan(self, filename: str) -> dict:
        return plan_archive_restore(filename, layout=self.layout)

    def delete(self, filename: str) -> dict:
        if filename in self.store.protected_archive_names():
            raise ArchiveError("Archive is protected by an active or failed transaction")
        return delete_archive(filename, layout=self.layout)

    def export_path(self, filename: str) -> Path:
        return export_archive_path(filename, layout=self.layout)

    def import_stream(self, filename: str, source) -> dict:
        return import_archive(filename, source, layout=self.layout)

    def new_operation(self, action: str) -> ManagerOperation:
        operation = ManagerOperation.create_system(action)
        self.store.save(operation)
        return operation

    @contextmanager
    def _maintenance_context(
        self,
        reservation: MaintenanceReservation | None,
        *,
        timeout: float = 0,
        allow_startup_recovery: bool = False,
    ):
        if reservation is not None:
            # The HTTP critical-task owner releases a transferred reservation
            # only after the operation and journal have reached a durable
            # terminal state.  Coordinators may leave this scope for
            # compensation, so they must not release it here.
            yield reservation.session
            return
        with self.service.maintenance(
            timeout=timeout,
            allow_startup_recovery=allow_startup_recovery,
        ) as maintenance:
            yield maintenance

    async def create(
        self,
        operation: ManagerOperation,
        *,
        description: str | None,
        profile: str,
        archive_kind: str = "manual",
        maintenance_lease: MaintenanceReservation | None = None,
    ) -> ManagerOperation:
        transaction_id = uuid4().hex
        create_detail: dict[str, Any] = {
            "transaction_id": transaction_id,
            "profile": profile,
            "original_running": [],
        }
        operation.transition("running", detail=create_detail)
        self.store.save(operation)
        self.store.write_journal(
            transaction_id,
            kind="archive_create",
            phase="preparing",
            status="running",
            operation_id=operation.operation_id,
            detail=create_detail,
        )
        stopped: list[str] = []
        original_running: list[str] = []
        try:
            with self._maintenance_context(maintenance_lease) as maintenance:
                def record_runtime_state(running: list[str]) -> None:
                    create_detail["original_running"] = running
                    self.store.write_journal(
                        transaction_id,
                        kind="archive_create",
                        phase="quiescing",
                        status="running",
                        operation_id=operation.operation_id,
                        detail=create_detail,
                    )

                original_running, stopped = await self._quiesce(
                    maintenance,
                    state_callback=record_runtime_state,
                )
                self._fault("quiesce")
                estimate = self.estimate(profile)
                if not estimate["enough_space"]:
                    raise ArchiveError("Insufficient free space for archive")
                def create_phase(phase: str) -> None:
                    self.store.write_journal(
                        transaction_id,
                        kind="archive_create",
                        phase=phase,
                        status="running",
                        operation_id=operation.operation_id,
                        detail=create_detail,
                    )
                    self._fault(phase)

                archive, manifest = await asyncio.to_thread(
                    create_archive,
                    description,
                    layout=self.layout,
                    profile=profile,
                    archive_kind=archive_kind,
                    phase_callback=create_phase,
                )
                create_detail["archive"] = archive["filename"]
                self._fault("restart")
                await self._restart(maintenance, original_running)
                stopped = []
            deleted = self._apply_retention_if_safe()
            operation.transition(
                "succeeded",
                message="Archive created",
                detail={
                    "archive": archive,
                    "manifest": manifest,
                    "estimate": estimate,
                    "retention_deleted": deleted,
                },
            )
            self.store.save(operation)
            self.store.write_journal(
                transaction_id,
                kind="archive_create",
                phase="committed",
                status="committed",
                operation_id=operation.operation_id,
                detail={**create_detail, "archive": archive["filename"]},
            )
            return operation
        except Exception as exc:
            restart_error = await self._best_effort_restart(
                stopped or original_running,
                maintenance_lease=maintenance_lease,
            )
            detail = {
                "transaction_id": transaction_id,
                "error": str(exc) or type(exc).__name__,
                "profile": profile,
            }
            if restart_error:
                detail["restart_error"] = restart_error
            operation.transition("failed", message=detail["error"], detail=detail)
            self.store.save(operation)
            journal_status = "rollback_failed" if restart_error else "rolled_back"
            self.store.write_journal(
                transaction_id,
                kind="archive_create",
                phase="failed",
                status=journal_status,
                operation_id=operation.operation_id,
                detail={**create_detail, **detail},
            )
            if restart_error is None:
                self._apply_retention_if_safe()
            raise ArchiveTransactionError(str(exc), detail=detail) from exc

    async def restore(
        self,
        operation: ManagerOperation,
        *,
        filename: str,
        description: str | None = None,
        maintenance_lease: MaintenanceReservation | None = None,
    ) -> ManagerOperation:
        transaction_id = uuid4().hex
        try:
            plan = self.plan(filename)
            profile = plan["profile"]
        except Exception as exc:
            failed_detail = {
                "transaction_id": transaction_id,
                "target_filename": filename,
                "failed_stage": "plan",
                "error": str(exc) or type(exc).__name__,
            }
            operation.transition(
                "failed",
                message=failed_detail["error"],
                detail=failed_detail,
            )
            self.store.save(operation)
            raise ArchiveTransactionError(
                failed_detail["error"],
                detail=failed_detail,
            ) from exc
        operation.transition(
            "running",
            detail={
                "transaction_id": transaction_id,
                "target_filename": filename,
                "profile": profile,
            },
        )
        self.store.save(operation)
        detail: dict[str, Any] = {
            "transaction_id": transaction_id,
            "target_filename": filename,
            "profile": profile,
            "original_running": [],
            "commit_point": "not_started",
        }
        self._journal(transaction_id, operation, "preparing", detail)
        try:
            baseline, control_gate = await self._capture_control_baseline()
            detail["control_heartbeat_baseline"] = baseline
            detail["control_gate"] = control_gate
            self._journal(transaction_id, operation, "preparing", detail)
            with self._maintenance_context(maintenance_lease) as maintenance:
                def record_restore_state(running: list[str]) -> None:
                    detail["original_running"] = running
                    self._journal(transaction_id, operation, "quiescing", detail)

                original_running, _stopped = await self._quiesce(
                    maintenance,
                    state_callback=record_restore_state,
                )
                self._journal(transaction_id, operation, "quiesced", detail)
                self._fault("quiesce")

                pre, _manifest = await asyncio.to_thread(
                    create_archive,
                    description or f"pre-restore {filename}",
                    layout=self.layout,
                    profile=profile,
                    archive_kind="system",
                )
                pre_name = str(pre["filename"])
                detail["pre_restore_filename"] = pre_name
                self._journal(transaction_id, operation, "pre_restore_verified", detail)
                self._fault("pre_restore")

                detail["commit_point"] = "data_switch_started"
                self._journal(transaction_id, operation, "applying", detail)
                result = await asyncio.to_thread(
                    apply_archive,
                    filename,
                    layout=self.layout,
                    phase_callback=lambda phase, _entry: self._fault(phase),
                )
                if result["failed_entries"]:
                    raise ArchiveError(
                        str(result["failed_entries"][0].get("error") or "Archive apply failed")
                    )

                self._journal(transaction_id, operation, "migrating", detail)
                self._fault("migration")
                migrations = await asyncio.to_thread(self._migrate_and_validate_schema)

                self._fault("restart")
                await self._restart(maintenance, original_running)
                self._journal(transaction_id, operation, "health_check", detail)
                self._fault("health")
                health = await self._hard_health(
                    original_running,
                    control_baseline=detail.get("control_heartbeat_baseline"),
                    control_gate=control_gate,
                )

                detail["commit_point"] = "health_passed"
                detail["health"] = health
                detail["migrations"] = migrations
                self._journal(transaction_id, operation, "healthy", detail)
                self._journal(
                    transaction_id,
                    operation,
                    "committed",
                    detail,
                    status="committed",
                )
            self._apply_retention_if_safe()
            operation.transition(
                "succeeded",
                message="Archive restore committed",
                detail={
                    **detail,
                    "restore": result,
                    "plan": plan,
                    "rolled_back": False,
                },
            )
            self.store.save(operation)
            self.store.retire_terminal_rollback_journals()
            return operation
        except Exception as exc:
            rollback = await self._rollback_transaction(
                transaction_id,
                operation,
                detail,
                maintenance_lease=maintenance_lease,
            )
            failed_detail = {
                **detail,
                "error": str(exc) or type(exc).__name__,
                "rollback": rollback,
                "rolled_back": rollback.get("succeeded", False),
            }
            operation.transition(
                "failed",
                message=failed_detail["error"],
                detail=failed_detail,
            )
            self.store.save(operation)
            if rollback.get("succeeded", False):
                self._apply_retention_if_safe()
            raise ArchiveTransactionError(
                failed_detail["error"],
                detail=failed_detail,
            ) from exc

    async def recover(
        self,
        *,
        allow_startup_recovery: bool = False,
    ) -> list[dict[str, Any]]:
        """Deterministically finish or compensate transactions after restart."""
        recovered: list[dict[str, Any]] = []
        for journal in self.store.list_recoverable_journals():
            if journal.get("kind") == "archive_create":
                detail = dict(journal.get("detail") or {})
                transaction_id = str(journal["transaction_id"])
                operation = (
                    self.store.get(str(journal.get("operation_id")))
                    if journal.get("operation_id")
                    else None
                )
                if operation is None:
                    operation = self.new_operation("archive.create.recovery")
                self._cleanup_inprogress()
                restart_error = await self._best_effort_restart(
                    [
                        value
                        for value in detail.get("original_running", [])
                        if isinstance(value, str)
                    ],
                    allow_startup_recovery=allow_startup_recovery,
                )
                status = "rollback_failed" if restart_error else "rolled_back"
                self.store.write_journal(
                    transaction_id,
                    kind="archive_create",
                    phase="recovered",
                    status=status,
                    operation_id=operation.operation_id,
                    detail={**detail, "restart_error": restart_error},
                )
                operation.transition(
                    "failed",
                    message="Archive creation interrupted by Manager restart",
                    detail={
                        **detail,
                        "recovered": True,
                        "restart_error": restart_error,
                    },
                )
                self.store.save(operation)
                recovered.append(
                    {"transaction_id": transaction_id, "action": "create_cleaned"}
                )
                continue
            if journal.get("kind") != "archive_restore":
                continue
            detail = dict(journal.get("detail") or {})
            transaction_id = str(journal["transaction_id"])
            if journal.get("status") == "rollback_failed" and detail.get(
                "commit_point"
            ) not in (None, "not_started"):
                # Terminal rollback adjudication rule (shared with
                # upgrade.UpgradeCoordinator.recover): the rollback already
                # re-applied the pre-restore archive and was adjudicated
                # failed.  Replaying it after a restart would only repeat
                # the damage, so this state is terminal and requires manual
                # recovery.  A rollback that failed before the data switch
                # only owes a best-effort restart and stays retryable.
                recovered.append(
                    {
                        "transaction_id": transaction_id,
                        "action": "rollback_failed",
                        "manual_recovery_required": True,
                    }
                )
                continue
            phase = str(journal.get("phase"))
            operation = (
                self.store.get(str(journal.get("operation_id")))
                if journal.get("operation_id")
                else None
            )
            if operation is None:
                operation = self.new_operation("archive.restore.recovery")
            if phase in {"preparing", "quiescing", "quiesced", "pre_restore_verified"}:
                self._cleanup_inprogress()
                restart_error = await self._best_effort_restart(
                    [
                        value
                        for value in detail.get("original_running", [])
                        if isinstance(value, str)
                    ],
                    allow_startup_recovery=allow_startup_recovery,
                )
                self._journal(
                    transaction_id,
                    operation,
                    "aborted_before_switch" if restart_error is None else "restart_original",
                    {**detail, "restart_error": restart_error},
                    status="rolled_back" if restart_error is None else "rollback_failed",
                )
                recovered.append(
                    {
                        "transaction_id": transaction_id,
                        "action": "cleaned" if restart_error is None else "restart_failed",
                    }
                )
                operation.transition(
                    "failed",
                    message="Restore interrupted before data switch; temporary state cleaned",
                    detail={
                        **detail,
                        "recovered": True,
                        "rolled_back": restart_error is None,
                        "restart_error": restart_error,
                    },
                )
                self.store.save(operation)
                if restart_error is None:
                    self._apply_retention_if_safe()
                continue
            if phase in {"healthy", "committed"} or detail.get("commit_point") == "health_passed":
                self._journal(
                    transaction_id,
                    operation,
                    "committed",
                    detail,
                    status="committed",
                )
                recovered.append({"transaction_id": transaction_id, "action": "finalized"})
                operation.transition(
                    "succeeded",
                    message="Restore commit finalized after Manager restart",
                    detail={**detail, "recovered": True, "rolled_back": False},
                )
                self.store.save(operation)
                continue
            rollback = await self._rollback_transaction(
                transaction_id,
                operation,
                detail,
                allow_startup_recovery=allow_startup_recovery,
            )
            recovered.append(
                {
                    "transaction_id": transaction_id,
                    "action": "rolled_back",
                    "result": rollback,
                }
            )
            operation.transition(
                "failed",
                message="Restore interrupted and automatically rolled back",
                detail={
                    **detail,
                    "recovered": True,
                    "rolled_back": rollback.get("succeeded", False),
                    "rollback": rollback,
                },
            )
            self.store.save(operation)
        return recovered

    async def _rollback_transaction(
        self,
        transaction_id: str,
        operation: ManagerOperation,
        detail: dict[str, Any],
        *,
        maintenance_lease: MaintenanceReservation | None = None,
        allow_startup_recovery: bool = False,
    ) -> dict[str, Any]:
        pre_name = detail.get("pre_restore_filename")
        if detail.get("commit_point") == "not_started" or not isinstance(pre_name, str):
            restart_error = await self._best_effort_restart(
                [
                    value
                    for value in detail.get("original_running", [])
                    if isinstance(value, str)
                ],
                maintenance_lease=maintenance_lease,
                allow_startup_recovery=allow_startup_recovery,
            )
            self._journal(
                transaction_id,
                operation,
                "failed_before_switch" if restart_error is None else "restart_original",
                {**detail, "restart_error": restart_error},
                status="rolled_back" if restart_error is None else "rollback_failed",
            )
            return {
                "succeeded": restart_error is None,
                "data_changed": False,
                "error": restart_error,
            }
        self._journal(transaction_id, operation, "rolling_back", detail)
        try:
            rollback_baseline, rollback_control_gate = (
                await self._capture_control_baseline()
            )
            detail["rollback_control_heartbeat_baseline"] = rollback_baseline
            detail["rollback_control_gate"] = rollback_control_gate
            with self._maintenance_context(
                maintenance_lease,
                timeout=1,
                allow_startup_recovery=allow_startup_recovery,
            ) as maintenance:
                await self._quiesce(maintenance)
                rollback_result = await asyncio.to_thread(
                    apply_archive,
                    pre_name,
                    layout=self.layout,
                )
                if rollback_result["failed_entries"]:
                    raise ArchiveError(
                        str(
                            rollback_result["failed_entries"][0].get("error")
                            or "Rollback apply failed"
                        )
                    )
                migrations = await asyncio.to_thread(self._migrate_and_validate_schema)
                await self._restart(
                    maintenance,
                    [
                        value
                        for value in detail.get("original_running", [])
                        if isinstance(value, str)
                    ],
                )
                health = await self._hard_health(
                    detail.get("original_running", []),
                    control_baseline=rollback_baseline,
                    control_gate=rollback_control_gate,
                )
            detail["rollback_health"] = health
            self._journal(
                transaction_id,
                operation,
                "rolled_back",
                detail,
                status="rolled_back",
            )
            return {
                "succeeded": True,
                "archive": pre_name,
                "migrations": migrations,
                "health": health,
            }
        except Exception as rollback_exc:
            detail["rollback_error"] = str(rollback_exc) or type(rollback_exc).__name__
            self._journal(
                transaction_id,
                operation,
                "rollback_failed",
                detail,
                status="rollback_failed",
            )
            return {"succeeded": False, "error": detail["rollback_error"]}

    async def _quiesce(
        self,
        maintenance,
        *,
        state_callback: Callable[[list[str]], None] | None = None,
    ) -> tuple[list[str], list[str]]:
        units = self.service.units()
        ids = [unit.runtime_unit_id for unit in units]
        statuses = await self.service.runtime_adapter.status(ids)
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

    async def _restart(self, maintenance, runtime_unit_ids: list[str]) -> None:
        for unit_id in runtime_unit_ids:
            await maintenance.operate_runtime_unit(unit_id, "start")

    async def _best_effort_restart(
        self,
        runtime_unit_ids: list[str],
        *,
        maintenance_lease: MaintenanceReservation | None = None,
        allow_startup_recovery: bool = False,
    ) -> str | None:
        if not runtime_unit_ids:
            return None
        try:
            with self._maintenance_context(
                maintenance_lease,
                timeout=1,
                allow_startup_recovery=allow_startup_recovery,
            ) as maintenance:
                await self._restart(maintenance, runtime_unit_ids)
        except Exception as exc:
            return str(exc) or type(exc).__name__
        return None

    def _migrate_and_validate_schema(self) -> list[dict[str, Any]]:
        targets = _schema_targets()
        applied: list[dict[str, Any]] = []
        for match in DATA_CATALOG.collect(self.layout, "full"):
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

    async def _hard_health(
        self,
        expected_running: list[str],
        *,
        control_baseline: str | None = None,
        control_gate: str = CONTROL_GATE_ENFORCED,
    ) -> dict[str, Any]:
        self.store.ensure_schema()
        config = self._validate_config()
        runtime = await self._wait_runtime_healthy(expected_running)
        control_skip_reason = _CONTROL_GATE_SKIP_REASONS.get(control_gate)
        if expected_running and control_skip_reason is None:
            control = await self._run_probe(
                self.control_probe,
                "control",
                predicate=lambda result: _heartbeat_is_newer(
                    result.get("heartbeat"),
                    control_baseline,
                ),
                failure_message="Bot control heartbeat did not advance after restart",
            )
        elif expected_running:
            # No active control channel existed at baseline time, so no
            # control heartbeat can ever advance; requiring it would always
            # fail.
            control = {"status": "not_applicable", "reason": control_skip_reason}
        else:
            control = {"status": "not_applicable"}
        # The runtime must still be healthy after the slower endpoint/control
        # merely at the beginning of the health window.
        runtime = await self._wait_runtime_healthy(expected_running)
        return {
            "manager_store": "ok",
            "config": config,
            "schema": "ok",
            "runtime_units": runtime,
            "control": control,
            "warnings": [
                "External NapCat/QQ/GitHub/LLM services are not hard health checks"
            ],
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

    async def _capture_control_baseline(self) -> tuple[Any, str]:
        """Capture the control heartbeat baseline and decide the health gate.

        The gate decision is anchored at baseline time: without any bound
        bot, or without an active control channel (the baseline probe does
        not report a fresh heartbeat), no control heartbeat can ever
        advance across the restart, so the health gate must not require it
        to advance.
        """
        baseline = await self._probe_once(self.control_probe)
        heartbeat = baseline.get("heartbeat") if isinstance(baseline, dict) else None
        status = await self.service.status()
        bots = status.get("bots") if isinstance(status, dict) else None
        if not bots:
            return heartbeat, CONTROL_GATE_SKIPPED_NO_BOUND_BOTS
        if not isinstance(baseline, dict) or baseline.get("ok") is not True:
            return heartbeat, CONTROL_GATE_SKIPPED_NO_ACTIVE_CONTROL_CHANNEL
        return heartbeat, CONTROL_GATE_ENFORCED

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
        return {"status": "ok" if result is not False else "failed", "ok": result is not False}

    async def _run_probe(
        self,
        probe: HealthProbe | None,
        name: str,
        *,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
        failure_message: str | None = None,
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + self.health_timeout
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

    def _journal(
        self,
        transaction_id: str,
        operation: ManagerOperation,
        phase: str,
        detail: dict[str, Any],
        *,
        status: str = "running",
    ) -> None:
        self.store.write_journal(
            transaction_id,
            kind="archive_restore",
            phase=phase,
            status=status,
            operation_id=operation.operation_id,
            detail=detail,
        )

    def _cleanup_inprogress(self) -> None:
        directory = self.layout.manager_backups_dir
        if not directory.exists():
            return
        for path in directory.iterdir():
            if path.is_file() and path.name.endswith((".inprogress", ".importing")):
                try:
                    path.unlink()
                except OSError:
                    continue

    def _apply_retention_if_safe(self) -> list[str]:
        return enforce_system_retention(
            layout=self.layout,
            protected=self.store.protected_archive_names(),
        )


def _schema_targets() -> dict[str, Any]:
    """Load the existing SchemaTarget definitions without duplicating migrations."""
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
