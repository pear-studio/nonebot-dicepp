"""Durable Manager orchestration for archive creation and exact restore."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from dicepp_data import InstanceLayout

from .archive import (
    ArchiveError,
    apply_archive,
    create_archive,
    delete_archive,
    estimate_archive,
    export_archive_path,
    import_archive,
    list_archives,
    plan_archive_restore,
    read_archive_detail,
    verify_archive,
)
from .archive_housekeeping import ArchiveHousekeeping
from .maintenance_policy import is_terminal_rollback_failure
from .maintenance_runtime import (
    CONTROL_GATE_ENFORCED,
    CONTROL_GATE_SKIPPED_NO_ACTIVE_CONTROL_CHANNEL,
    CONTROL_GATE_SKIPPED_NO_BOUND_BOTS,
    DEFAULT_CONTROL_HEALTH_TIMEOUT,
    HealthProbe,
    MaintenanceRuntimeSupport,
)
from .models import ManagerOperation
from .service import MaintenanceReservation, ManagerService


FaultHook = Callable[[str], None]


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
        control_health_timeout: float = DEFAULT_CONTROL_HEALTH_TIMEOUT,
        health_interval: float = 0.5,
        health_consecutive: int = 3,
    ) -> None:
        self.layout = layout
        self.service = service
        self.store = service.store
        self.fault_hook = fault_hook
        self.runtime_support = MaintenanceRuntimeSupport(
            layout=layout,
            service=service,
            control_probe=control_probe,
            health_timeout=health_timeout,
            control_health_timeout=control_health_timeout,
            health_interval=health_interval,
            health_consecutive=health_consecutive,
        )
        self.housekeeping = ArchiveHousekeeping(layout=layout, store=self.store)
        service.maintenance_runtime_support = self.runtime_support
        service.archive_housekeeping = self.housekeeping

    @property
    def control_probe(self) -> HealthProbe | None:
        return self.runtime_support.control_probe

    @control_probe.setter
    def control_probe(self, value: HealthProbe | None) -> None:
        self.runtime_support.control_probe = value

    @property
    def health_timeout(self) -> float:
        return self.runtime_support.health_timeout

    @health_timeout.setter
    def health_timeout(self, value: float) -> None:
        self.runtime_support.health_timeout = value

    @property
    def control_health_timeout(self) -> float:
        return self.runtime_support.control_health_timeout

    @control_health_timeout.setter
    def control_health_timeout(self, value: float) -> None:
        self.runtime_support.control_health_timeout = value

    @property
    def health_interval(self) -> float:
        return self.runtime_support.health_interval

    @health_interval.setter
    def health_interval(self, value: float) -> None:
        self.runtime_support.health_interval = value

    @property
    def health_consecutive(self) -> int:
        return self.runtime_support.health_consecutive

    @health_consecutive.setter
    def health_consecutive(self, value: int) -> None:
        self.runtime_support.health_consecutive = value

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

                original_running, stopped = await self.runtime_support.quiesce(
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
                await self.runtime_support.restart(maintenance, original_running)
                stopped = []
            deleted = self.housekeeping.apply_retention()
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
            restart_error = await self.runtime_support.best_effort_restart(
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
                self.housekeeping.apply_retention()
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
            "opaque_sqlite": plan.get(
                "opaque_sqlite",
                {"count": 0, "files": []},
            ),
            "original_running": [],
            "commit_point": "not_started",
        }
        self._journal(transaction_id, operation, "preparing", detail)
        try:
            baseline, control_gate = await self.runtime_support.capture_control_baseline()
            detail["control_heartbeat_baseline"] = baseline
            detail["control_gate"] = control_gate
            self._journal(transaction_id, operation, "preparing", detail)
            with self._maintenance_context(maintenance_lease) as maintenance:
                def record_restore_state(running: list[str]) -> None:
                    detail["original_running"] = running
                    self._journal(transaction_id, operation, "quiescing", detail)

                original_running, _stopped = await self.runtime_support.quiesce(
                    maintenance,
                    state_callback=record_restore_state,
                )
                self._journal(transaction_id, operation, "quiesced", detail)
                self._fault("quiesce")

                pre, _pre_manifest = await asyncio.to_thread(
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
                plan = result["plan"]
                detail["opaque_sqlite"] = plan.get(
                    "opaque_sqlite",
                    {"count": 0, "files": []},
                )

                self._journal(transaction_id, operation, "migrating", detail)
                self._fault("migration")
                migrations = await asyncio.to_thread(
                    self.runtime_support.migrate_and_validate_schema,
                    set(detail["opaque_sqlite"]["files"]),
                )

                self._fault("restart")
                await self.runtime_support.restart(maintenance, original_running)
                self._journal(transaction_id, operation, "health_check", detail)
                self._fault("health")
                health = await self.runtime_support.hard_health(
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
            self.housekeeping.apply_retention()
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
                self.housekeeping.apply_retention()
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
                self.housekeeping.cleanup_inprogress()
                restart_error = await self.runtime_support.best_effort_restart(
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
            if is_terminal_rollback_failure(journal):
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
                self.housekeeping.cleanup_inprogress()
                restart_error = await self.runtime_support.best_effort_restart(
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
                    self.housekeeping.apply_retention()
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
            restart_error = await self.runtime_support.best_effort_restart(
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
                await self.runtime_support.capture_control_baseline()
            )
            detail["rollback_control_heartbeat_baseline"] = rollback_baseline
            detail["rollback_control_gate"] = rollback_control_gate
            with self._maintenance_context(
                maintenance_lease,
                timeout=1,
                allow_startup_recovery=allow_startup_recovery,
            ) as maintenance:
                await self.runtime_support.quiesce(maintenance)
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
                rollback_opaque_sqlite = rollback_result["plan"].get(
                    "opaque_sqlite",
                    {"count": 0, "files": []},
                )
                detail["pre_restore_opaque_sqlite"] = rollback_opaque_sqlite
                migrations = await asyncio.to_thread(
                    self.runtime_support.migrate_and_validate_schema,
                    set(rollback_opaque_sqlite["files"]),
                )
                await self.runtime_support.restart(
                    maintenance,
                    [
                        value
                        for value in detail.get("original_running", [])
                        if isinstance(value, str)
                    ],
                )
                health = await self.runtime_support.hard_health(
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
