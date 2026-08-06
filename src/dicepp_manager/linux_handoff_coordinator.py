"""Linux Manager handoff recovery and takeover state machine.

The generic upgrade coordinator supplies durable operation, maintenance, and
platform services. This class owns only the Linux handoff protocol branches.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import socket
from datetime import timedelta
from pathlib import Path
from typing import Any

from dicepp_meta import get_version
from packaging.version import Version

from .archive import ArchiveError, apply_archive
from .dashboard_db import restore_for_transaction
from .docker_runtime import DockerRuntimeError
from .linux_handoff import (
    DECISION_COMMIT,
    DECISION_ROLLBACK,
    _DECISION_FILENAME,
    _REQUEST_FILENAME,
    _RESULT_FILENAME,
    RESULT_SOURCE_RESTORED,
    RESULT_TARGET_COMMITTED,
    HandoffProtocolError,
    read_decision,
    read_request,
    read_result,
    write_decision,
)
from .models import ManagerOperation, utc_now
from ._path_security import UnsafePathError, assert_contained_no_reparse

logger = logging.getLogger("dicepp_manager.linux_handoff_coordinator")

_LINUX_RESULT_POLL_SECONDS = 2
LINUX_COMMIT_CONVERGENCE_WAIT = 45.0
LINUX_CONVERGENCE_WINDOW = 900.0


def _string_list(value: Any) -> list[str]:
    return (
        [item for item in value if isinstance(item, str)]
        if isinstance(value, list)
        else []
    )


class LinuxHandoffCoordinator:
    """Reusable Linux handoff state machine mixed into UpgradeCoordinator."""

    def _init_linux_handoff_coordinator(
        self,
        *,
        upgrade_error: type[Exception],
        upgrade_compatibility_error: type[Exception],
    ) -> None:
        self._upgrade_error = upgrade_error
        self._upgrade_compatibility_error = upgrade_compatibility_error
        self._convergence_tasks: set[asyncio.Task] = set()

    async def _recover_linux_manager_handoff(
        self,
        journal: dict[str, Any],
        operation: ManagerOperation,
        detail: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Resume a Linux Manager handoff: target takeover or source restore.

        The target Manager (running the target release code) takes over the
        transaction; the source Manager (running the source release code)
        restores data after ``result=source-restored``.  Nothing is inferred
        from the journal alone: the authoritative request/decision/result
        files and the exact Docker identity gate every branch.
        """
        transaction_id = str(detail.get("transaction_id") or "")
        staged = detail.get("platform_staged")
        current = detail.get("platform_current")
        if not isinstance(staged, dict) or not isinstance(current, dict):
            return {
                "action": "invalid_linux_handoff_state",
                "manual_recovery_required": True,
            }
        tx_dir_raw = staged.get("transaction_dir")
        if not isinstance(tx_dir_raw, str) or not tx_dir_raw:
            return {
                "action": "invalid_linux_handoff_state",
                "manual_recovery_required": True,
            }
        tx_dir = Path(tx_dir_raw)
        try:
            assert_contained_no_reparse(
                tx_dir,
                root=self.layout.manager_recovery_dir,
                allow_missing=False,
            )
            request = read_request(
                tx_dir / _REQUEST_FILENAME,
                root=self.layout.manager_recovery_dir,
            )
        except (UnsafePathError, HandoffProtocolError, OSError) as exc:
            return {
                "action": "linux_request_unreadable",
                "manual_recovery_required": True,
                "error": str(exc),
            }
        decision = read_decision(
            tx_dir / _DECISION_FILENAME,
            transaction_id=transaction_id,
            operation_id=request["operation_id"],
            root=self.layout.manager_recovery_dir,
        )
        actual_version = get_version()
        try:
            is_target = (
                actual_version != "unknown"
                and Version(actual_version) == Version(request["target_version"])
            )
            is_source = (
                actual_version != "unknown"
                and Version(actual_version) == Version(request["source_version"])
            )
        except Exception:
            is_target = is_source = False
        if is_target:
            return await self._linux_target_takeover(
                operation, detail, request, tx_dir, decision
            )
        if is_source:
            return await self._linux_source_restore(
                operation, detail, request, tx_dir, decision
            )
        blocked = {
            **detail,
            "phase": "linux_handoff_version_blocked",
            "manual_recovery_required": True,
            "error": (
                f"Running Manager version {actual_version!r} matches neither "
                f"source {request['source_version']!r} nor target "
                f"{request['target_version']!r}"
            ),
        }
        self._journal(
            operation,
            blocked,
            phase="linux_handoff_version_blocked",
            status="rollback_failed",
        )
        operation.transition(
            "failed",
            message="Linux handoff belongs to another Manager version",
            detail=blocked,
        )
        self.store.save(operation)
        return {
            "action": "linux_handoff_version_blocked",
            "manual_recovery_required": True,
        }

    def _read_bound_result(
        self,
        tx_dir: Path,
        request: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Read the Updater result and refuse any foreign transaction file.

        The result file is authoritative evidence for this transaction only;
        a stale or foreign result (same transaction dir, different
        transaction/operation identity) must never authorize a commit
        convergence or a source restore.  read_result itself does not bind
        identity, so the coordinator checks the payload fields here and
        raises :class:`HandoffProtocolError` for anything that does not match
        the request.
        """
        result = read_result(
            tx_dir / _RESULT_FILENAME,
            root=self.layout.manager_recovery_dir,
        )
        if result is None:
            return None
        if (
            result.get("transaction_id") != request["transaction_id"]
            or result.get("operation_id") != request["operation_id"]
        ):
            raise HandoffProtocolError(
                "result belongs to another transaction; foreign evidence "
                "is never accepted",
                code="handoff_result_foreign_transaction",
            )
        return result

    async def _linux_handoff_tx_state(
        self,
        request: dict[str, Any],
        tx_dir: Path,
    ) -> tuple[str | None, dict[str, Any] | None]:
        decision = read_decision(
            tx_dir / _DECISION_FILENAME,
            transaction_id=request["transaction_id"],
            operation_id=request["operation_id"],
            root=self.layout.manager_recovery_dir,
        )
        result = self._read_bound_result(tx_dir, request)
        return decision, result

    async def _verify_source_manager_identity(
        self, request: dict[str, Any]
    ) -> bool:
        """Prove this process runs the source Manager container of the request.

        Inside a container the hostname is the container short id; inspect
        that container through the Docker socket and require its image id to
        equal ``request.manager.image_id``.  Any environment that cannot
        prove the identity — no handoff executor, no container hostname,
        inspect failure or mismatch — fails closed: a matching version string
        alone never authorizes a data restore.
        """
        handoff = getattr(self.platform_adapter, "handoff", None)
        inspect = getattr(handoff, "inspect", None)
        if not callable(inspect):
            return False
        hostname = socket.gethostname()
        if not re.fullmatch(r"[0-9a-fA-F]{12,64}", hostname):
            return False
        try:
            identity = await inspect(hostname)
        except Exception:
            return False
        image_id = getattr(identity, "image_id", None)
        if (
            not isinstance(image_id, str)
            or image_id != request["manager"]["image_id"]
        ):
            return False
        container_id = getattr(identity, "container_id", None)
        return (
            isinstance(container_id, str)
            and container_id.lower().startswith(hostname.lower())
        )

    async def _linux_wait_target_committed(
        self,
        tx_dir: Path,
        request: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Wait (bounded) for the Updater result after ``decision=commit``.

        The Updater writes ``result`` at least one poll cycle after the
        decision is visible; converging before it exists would falsely report
        cleanup-pending and leave the startup maintenance gate raised.  The
        wait is bounded by :data:`LINUX_COMMIT_CONVERGENCE_WAIT`, so recovery
        never blocks the caller for the whole transaction deadline; a timeout
        hands convergence to the background loop (``_linux_convergence_loop``).
        Returns the authoritative result once ``target-committed`` arrives, a
        fail-closed conflict dict for any other outcome (including foreign
        transaction evidence), or None when the bounded wait is exhausted.
        """
        deadline = self.now() + timedelta(seconds=LINUX_COMMIT_CONVERGENCE_WAIT)
        # Recovery is not finished until the wait resolves; keep user
        # lifecycle submissions blocked for the whole wait.
        self.service.set_startup_maintenance_gate(True)
        while True:
            try:
                result = self._read_bound_result(tx_dir, request)
            except HandoffProtocolError as exc:
                # Foreign or corrupt evidence after a commit decision: never
                # proceed on it, fail closed.
                return {
                    "action": "linux_result_conflict",
                    "manual_recovery_required": True,
                    "error": str(exc),
                }
            if result is not None:
                if result.get("value") == RESULT_TARGET_COMMITTED:
                    return result
                # A conflicting authoritative outcome after a commit decision
                # means the Updater violated first-write-wins; never proceed.
                return {
                    "action": "linux_result_conflict",
                    "manual_recovery_required": True,
                }
            if self.now() >= deadline:
                return None
            await asyncio.sleep(_LINUX_RESULT_POLL_SECONDS)

    def _spawn_convergence_loop(
        self,
        operation: ManagerOperation,
        detail: dict[str, Any],
        request: dict[str, Any],
        tx_dir: Path,
        *,
        mode: str,
    ) -> None:
        """Schedule the in-process convergence loop for a pending handoff.

        Called when the bounded wait timed out (or the source side is still
        awaiting the Updater result): the background loop re-reads the result
        and converges the terminal cleanup without ever blocking recovery.
        The loop is bounded by :data:`LINUX_CONVERGENCE_WINDOW`; after that
        the transaction stays recoverable for the next Manager start.
        """
        loop = asyncio.get_running_loop()
        task = loop.create_task(
            self._linux_convergence_loop(operation, detail, request, tx_dir, mode=mode)
        )
        self._convergence_tasks.add(task)
        task.add_done_callback(self._convergence_tasks.discard)

    async def _linux_convergence_loop(
        self,
        operation: ManagerOperation,
        detail: dict[str, Any],
        request: dict[str, Any],
        tx_dir: Path,
        *,
        mode: str,
    ) -> None:
        """In-process convergence: finish a pending target/source handoff.

        ``mode="target"`` waits for ``result=target-committed`` and runs the
        committed cleanup; ``mode="source"`` waits for
        ``result=source-restored`` and runs the source restore.  Any other
        authoritative outcome ends the loop (the journal stays recoverable).
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + LINUX_CONVERGENCE_WINDOW
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            await asyncio.sleep(min(_LINUX_RESULT_POLL_SECONDS, remaining))
            try:
                result = self._read_bound_result(tx_dir, request)
            except HandoffProtocolError:
                return
            if result is None:
                continue
            value = result.get("value")
            if value == RESULT_TARGET_COMMITTED and mode == "target":
                try:
                    await self._linux_finish_committed(
                        operation, detail, request, tx_dir
                    )
                except Exception as exc:
                    logger.warning(
                        "handoff committed convergence failed for %s: %s",
                        tx_dir,
                        exc,
                    )
                return
            if value == RESULT_SOURCE_RESTORED and mode == "source":
                try:
                    await self._linux_source_restore(
                        operation, detail, request, tx_dir, DECISION_ROLLBACK
                    )
                except Exception as exc:
                    logger.warning(
                        "handoff source restore convergence failed for %s: %s",
                        tx_dir,
                        exc,
                    )
                return
            if value not in (RESULT_TARGET_COMMITTED, RESULT_SOURCE_RESTORED):
                logger.warning(
                    "handoff result %r ends in-process convergence for %s",
                    value,
                    tx_dir,
                )
                return

    async def _linux_converge_after_commit(
        self,
        operation: ManagerOperation,
        detail: dict[str, Any],
        request: dict[str, Any],
        tx_dir: Path,
    ) -> dict[str, Any]:
        """Wait for the Updater result and converge the committed cleanup.

        Returns ``cleanup_pending`` when the bounded wait is exhausted before
        the result arrives (the background convergence loop keeps retrying in
        this process), or the conflict result when the Updater reported a
        non-commit outcome.
        """
        waited = await self._linux_wait_target_committed(tx_dir, request)
        if waited is None:
            self._spawn_convergence_loop(
                operation, detail, request, tx_dir, mode="target"
            )
            return {
                "action": "cleanup_pending",
                "manual_recovery_required": True,
            }
        if "action" in waited:
            return waited
        return await self._linux_finish_committed(
            operation, detail, request, tx_dir
        )

    async def _linux_target_takeover(
        self,
        operation: ManagerOperation,
        detail: dict[str, Any],
        request: dict[str, Any],
        tx_dir: Path,
        decision: str | None,
    ) -> dict[str, Any]:
        """The target Manager runs migration and completes the transaction."""
        transaction_id = str(detail["transaction_id"])
        staged = detail["platform_staged"]
        current = detail["platform_current"]
        verify_identity = getattr(
            self.platform_adapter,
            "verify_target_manager_identity",
            None,
        )
        allow_restored_restart_policy = False
        if decision == DECISION_COMMIT:
            try:
                committed_result = self._read_bound_result(tx_dir, request)
            except HandoffProtocolError:
                committed_result = None
            allow_restored_restart_policy = (
                committed_result is not None
                and committed_result.get("value") == RESULT_TARGET_COMMITTED
            )
        try:
            if not callable(verify_identity):
                raise self._upgrade_compatibility_error(
                    "Target Manager identity verifier is unavailable",
                    code="target_manager_identity_invalid",
                )
            await verify_identity(
                request,
                allow_restored_restart_policy=allow_restored_restart_policy,
            )
        except (self._upgrade_compatibility_error, DockerRuntimeError) as exc:
            # An unverified target process may not migrate data, move aliases,
            # write a rollback decision or otherwise choose transaction
            # direction.  Keep the startup gate closed and let the source
            # Updater deadline restore the old Manager.
            self.service.set_startup_maintenance_gate(True)
            logger.error("Linux target Manager identity gate failed: %s", exc)
            return {
                "action": "target_manager_identity_invalid",
                "manual_recovery_required": True,
                "error": str(exc),
            }
        if decision == DECISION_COMMIT:
            # The target already committed; only convergence remains (e.g.
            # this Manager wrote commit in a previous recovery run before the
            # Updater confirmed the switch).
            return await self._linux_converge_after_commit(
                operation, detail, request, tx_dir
            )
        if decision == DECISION_ROLLBACK:
            return {
                "action": "linux_target_after_rollback",
                "manual_recovery_required": True,
            }
        self.service.set_startup_maintenance_gate(True)
        takeover = {
            **detail,
            "phase": "linux_manager_takeover",
            "progress": 60,
        }
        self._journal(operation, takeover, phase="linux_manager_takeover")
        try:
            migrations = await asyncio.to_thread(
                self.runtime_support.migrate_and_validate_schema
            )
            takeover["migrations"] = migrations
            await self.platform_adapter.create_target_runtimes(
                current, staged, transaction_id
            )
            with self._maintenance_context(
                None, allow_startup_recovery=True
            ) as maintenance:
                await self.runtime_support.restart(
                    maintenance, _string_list(detail.get("original_running"))
                )
            health = await self.runtime_support.hard_health(
                _string_list(detail.get("original_running")),
                control_baseline=detail.get("control_heartbeat_baseline"),
                control_gate=detail.get("control_gate"),
            )
            takeover["health"] = health
            self._journal(operation, takeover, phase="linux_handoff_healthy")
            await self.platform_adapter.update_current_aliases(
                current,
                {
                    "bot": staged["images"]["bot"]["image_id"],
                    "dashboard": staged["images"]["dashboard"]["image_id"],
                },
            )
            # Health gate before the commit decision is durable: the running
            # bot/dashboard/manager containers must carry exactly the staged
            # target image IDs (adapter-level verification).  Any failure
            # falls into the takeover-failed branch below, which writes
            # decision=rollback so the Updater restores the source (fail
            # closed).  The adapter may still expose the older
            # ``(current, staged)`` signature during the parallel adapter
            # change; a TypeError from the call shape falls back to it.
            verify = getattr(
                self.platform_adapter,
                "verify_target_container_images",
                None,
            )
            if callable(verify):
                try:
                    await verify(request)
                except TypeError:
                    await verify(current, staged)
            decision_payload = {
                "format_version": request["format_version"],
                "transaction_id": request["transaction_id"],
                "operation_id": request["operation_id"],
                "value": DECISION_COMMIT,
                "created_at": utc_now(),
            }
            write_decision(
                tx_dir / _DECISION_FILENAME,
                decision_payload,
                root=self.layout.manager_recovery_dir,
            )
            self._journal(
                operation,
                {**takeover, "phase": "cleanup_pending"},
                phase="cleanup_pending",
                status="interrupted",
            )
            # The Updater writes result only after it switched the
            # containers; poll for it (bounded wait, then the in-process
            # convergence loop) before converging, so a premature
            # cleanup_pending never strands the transaction with the
            # maintenance gate raised.
            return await self._linux_converge_after_commit(
                operation, takeover, request, tx_dir
            )
        except Exception as exc:
            return await self._linux_takeover_failed(
                operation,
                detail,
                request,
                tx_dir,
                error=exc,
            )

    async def _linux_takeover_failed(
        self,
        operation: ManagerOperation,
        detail: dict[str, Any],
        request: dict[str, Any],
        tx_dir: Path,
        *,
        error: Exception,
    ) -> dict[str, Any]:
        """A takeover failure writes rollback; the Updater restores the source."""
        transaction_id = str(detail["transaction_id"])
        decision = read_decision(
            tx_dir / _DECISION_FILENAME,
            transaction_id=transaction_id,
            operation_id=request["operation_id"],
            root=self.layout.manager_recovery_dir,
        )
        if decision == DECISION_COMMIT:
            # The commit decision is authoritative even though this takeover
            # attempt failed; wait for the Updater to confirm the switch
            # before converging.
            return await self._linux_converge_after_commit(
                operation, detail, request, tx_dir
            )
        if decision is None:
            try:
                with self._maintenance_context(
                    None, allow_startup_recovery=True
                ) as maintenance:
                    await self.runtime_support.quiesce(maintenance)
            except Exception:
                pass
            write_decision(
                tx_dir / _DECISION_FILENAME,
                {
                    "format_version": request["format_version"],
                    "transaction_id": request["transaction_id"],
                    "operation_id": request["operation_id"],
                    "value": DECISION_ROLLBACK,
                    "created_at": utc_now(),
                },
                root=self.layout.manager_recovery_dir,
            )
        failed = {
            **detail,
            "phase": "linux_takeover_failed",
            "error": str(error) or type(error).__name__,
            "failure_code": getattr(error, "code", "linux_takeover_failed"),
            "manual_recovery_required": True,
        }
        self._journal(
            operation,
            failed,
            phase="linux_takeover_failed",
            status="interrupted",
        )
        operation.transition(
            "failed",
            message="Linux Manager takeover failed; the Updater restores the source",
            detail=failed,
        )
        self.store.save(operation)
        return {
            "action": "linux_takeover_failed_rollback_requested",
            "rollback_requested": True,
        }

    async def _linux_finish_committed(
        self,
        operation: ManagerOperation,
        detail: dict[str, Any],
        request: dict[str, Any],
        tx_dir: Path,
    ) -> dict[str, Any]:
        """Converge target-side cleanup after ``decision=commit``.

        Only completes the target cleanup; the source can never be restored
        past this point, even if the Updater result never arrives.
        """
        try:
            result = self._read_bound_result(tx_dir, request)
        except HandoffProtocolError as exc:
            # Foreign or corrupt evidence must never authorize the committed
            # cleanup; fail closed and keep the transaction recoverable.
            return {
                "action": "cleanup_pending",
                "manual_recovery_required": True,
                "error": str(exc),
            }
        if result is None or result.get("value") != RESULT_TARGET_COMMITTED:
            return {
                "action": "cleanup_pending",
                "manual_recovery_required": True,
            }
        try:
            await self.platform_adapter.restore_runtime_policies(detail)
        except Exception as exc:
            return {
                "action": "cleanup_pending",
                "manual_recovery_required": True,
                "error": str(exc),
            }
        cleanup_error = await self._cleanup_platform_staging(detail)
        if cleanup_error is not None:
            return {
                "action": "cleanup_pending",
                "manual_recovery_required": True,
                "error": cleanup_error,
            }
        committed = {
            **detail,
            "phase": "committed",
            "progress": 100,
        }
        self._journal(
            operation,
            committed,
            phase="committed",
            status="committed",
        )
        operation.transition(
            "succeeded",
            message=f"Upgrade to {request['target_version']} committed",
            detail=committed,
        )
        self.store.save(operation)
        self.service.set_startup_maintenance_gate(False)
        # Terminal success: the transaction files are no longer recovery
        # material.  Failure to remove them only warns; the terminal state
        # must never depend on cleanup.
        try:
            self._cleanup_transaction_dir(tx_dir)
        except (OSError, self._upgrade_error) as exc:
            logger.warning(
                "handoff transaction dir cleanup failed for %s: %s",
                tx_dir,
                exc,
            )
        return {"action": "committed"}

    async def _linux_source_restore(
        self,
        operation: ManagerOperation,
        detail: dict[str, Any],
        request: dict[str, Any],
        tx_dir: Path,
        decision: str | None,
    ) -> dict[str, Any]:
        """The source Manager restores data only after Updater confirmed it."""
        transaction_id = str(detail["transaction_id"])
        if decision == DECISION_COMMIT:
            return {
                "action": "linux_source_after_commit",
                "manual_recovery_required": True,
            }
        try:
            _decision, result = await self._linux_handoff_tx_state(request, tx_dir)
        except HandoffProtocolError as exc:
            # Foreign or corrupt evidence never authorizes a restore; fail
            # closed, keep waiting.
            return {
                "action": "awaiting_updater_source_restore",
                "manual_recovery_required": True,
                "error": str(exc),
            }
        if result is None or result.get("value") != RESULT_SOURCE_RESTORED:
            # The Updater has not confirmed the source Manager was restored;
            # never infer it from an uncommitted journal.  Keep converging in
            # this process: the Updater may still be mid-rollback (e.g. it
            # crashed between decision=rollback and writing result), and
            # without this loop nothing would retry until the next Manager
            # start.
            self._spawn_convergence_loop(
                operation, detail, request, tx_dir, mode="source"
            )
            return {
                "action": "awaiting_updater_source_restore",
                "manual_recovery_required": True,
            }
        # The version string alone never authorizes a restore: prove through
        # the Docker socket that this process really is the source Manager
        # container recorded in the request, or fail closed.
        if not await self._verify_source_manager_identity(request):
            blocked = {
                **detail,
                "phase": "source_identity_mismatch",
                "manual_recovery_required": True,
                "error": (
                    "Running Manager container image does not match the "
                    "request source identity; manual recovery required"
                ),
            }
            self._journal(
                operation,
                blocked,
                phase="source_identity_mismatch",
                status="rollback_failed",
            )
            operation.transition(
                "failed",
                message="Linux restore blocked: Manager identity mismatch",
                detail=blocked,
            )
            self.store.save(operation)
            return {
                "action": "source_identity_mismatch",
                "manual_recovery_required": True,
            }
        self.service.set_startup_maintenance_gate(True)
        original = _string_list(detail.get("original_running"))
        current = detail["platform_current"]
        restored_detail = {
            **detail,
            "phase": "linux_source_restore",
            "rollback_status": "running",
        }
        self._journal(operation, restored_detail, phase="linux_source_restore")
        try:
            with self._maintenance_context(
                None, timeout=1, allow_startup_recovery=True
            ) as maintenance:
                await self.runtime_support.quiesce(maintenance)
                # 回退契约(规格 §10/329):./dashboard/data 是共享 bind mount,
                # 目标 Dashboard 可能已写入 dashboard.db/-wal/-shm。趁来源
                # 容器尚未启动,按 WAL 安全流程恢复本事务快照并校验;任何
                # 契约破坏都 fail closed 并保留现场。
                await asyncio.to_thread(
                    restore_for_transaction, self.layout, request
                )
                await self.platform_adapter.restore_source_runtimes(
                    current, transaction_id=transaction_id
                )
                await self.platform_adapter.restore_current_aliases(current)
                pre = detail.get("pre_upgrade_filename")
                if not isinstance(pre, str):
                    raise self._upgrade_error("Pre-upgrade archive is unavailable")
                restored = await asyncio.to_thread(
                    apply_archive, pre, layout=self.layout
                )
                if restored["failed_entries"]:
                    raise ArchiveError(
                        str(
                            restored["failed_entries"][0].get("error")
                            or "Pre-upgrade data restore failed"
                        )
                    )
                migrations = await asyncio.to_thread(
                    self.runtime_support.migrate_and_validate_schema
                )
                await self.runtime_support.restart(maintenance, original)
                health = await self.runtime_support.hard_health(
                    original,
                    control_baseline=detail.get("control_heartbeat_baseline"),
                    control_gate=detail.get("control_gate"),
                    control_failure_is_warning=True,
                )
            cleanup_error = await self._cleanup_platform_staging(detail)
            if cleanup_error is not None:
                raise self._upgrade_error(
                    f"Rollback completed but cleanup failed: {cleanup_error}"
                )
            rolled = {
                **restored_detail,
                "phase": "rolled_back",
                "rollback_status": "succeeded",
                "rolled_back": True,
                "health": health,
                "migrations": migrations,
            }
            self._journal(
                operation,
                rolled,
                phase="rolled_back",
                status="rolled_back",
            )
            operation.transition(
                "failed",
                message="Linux upgrade rolled back to the source",
                detail=rolled,
            )
            self.store.save(operation)
            self.service.set_startup_maintenance_gate(False)
            # Terminal success: drop the handoff transaction files (request/
            # decision/result/snapshots).  Failure only warns; rollback_failed
            # below always preserves them as recovery material.
            try:
                self._cleanup_transaction_dir(tx_dir)
            except (OSError, self._upgrade_error) as exc:
                logger.warning(
                    "handoff transaction dir cleanup failed for %s: %s",
                    tx_dir,
                    exc,
                )
            return {"action": "rolled_back"}
        except Exception as exc:
            failed = {
                **detail,
                "phase": "rollback_failed",
                "rollback_status": "failed",
                "rolled_back": False,
                "error": str(exc) or type(exc).__name__,
                "manual_recovery_required": True,
            }
            self._journal(
                operation,
                failed,
                phase="rollback_failed",
                status="rollback_failed",
            )
            operation.transition(
                "failed",
                message="Linux rollback failed; recovery material is preserved",
                detail=failed,
            )
            self.store.save(operation)
            return {"action": "rollback_failed"}

    def _cleanup_transaction_dir(self, tx_dir: Path) -> None:
        """Remove a finished Linux handoff transaction directory.

        The request/decision/result files and snapshots are only recovery
        material until the transaction reaches a terminal success (committed
        or rolled back).  The path must stay inside the trusted recovery root
        with a 32-hex transaction id, mirroring the staging cleanup guard;
        anything else is refused.  Callers treat failure as a warning and
        never change the terminal state; rollback_failed/cleanup_pending must
        preserve the directory.
        """
        root = self.layout.manager_recovery_dir
        if tx_dir.parent != root or not re.fullmatch(
            r"[0-9a-f]{32}", tx_dir.name
        ):
            raise self._upgrade_error(
                "Refusing to clean an untrusted recovery transaction path"
            )
        if tx_dir.is_symlink():
            tx_dir.unlink(missing_ok=True)
        elif tx_dir.is_dir():
            shutil.rmtree(tx_dir)
        elif tx_dir.exists():
            tx_dir.unlink()
