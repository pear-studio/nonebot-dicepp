"""Private standalone Manager HTTP API."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import tempfile
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from dicepp_data import (
    QueryDatabaseStateError,
    is_query_database_name,
    load_query_database_state,
    set_query_database_enabled,
)
from dicepp_meta import get_version

from .archive import (
    MAX_ARCHIVE_BYTES,
    ArchiveError,
    ArchiveInvalidError,
    ArchiveNameError,
    ArchiveNotFoundError,
    ArchiveRestorePlanVerificationError,
)
from .archive_coordinator import ArchiveCoordinator, ArchiveTransactionError
from .auth import ensure_api_token, token_matches
from .config import ManagerSettings
from .control import ControlChannelService
from .config_validation import (
    ConfigurationValidationError,
    read_config_object,
    validate_bot_candidate,
    validate_user_candidate,
)
from .factory import create_manager_service
from .models import ManagerAction, VALID_ACTIONS
from .query_database import (
    QueryDatabaseCoordinator,
    QueryDatabaseNormalizationError,
    prepare_query_database_candidate,
    query_normalization_report_detail,
)
from .release import ReleaseError, ReleaseManager
from .upgrade import (
    UnsupportedUpgradeAdapter,
    UpgradeCompatibilityError,
    UpgradeConfirmationError,
    UpgradeCoordinator,
    UpgradeError,
    UpgradeTransactionError,
)
from .runtime import RuntimeOperationUnsupported
from .maintenance import MaintenanceConflict
from .maintenance_runtime import MaintenanceRuntimeSupport
from .service import (
    MaintenanceReservation,
    ManagerService,
    OperationConflict,
    OperationFailed,
    UnknownRuntimeUnit,
)


logger = logging.getLogger(__name__)


def _maintenance_conflict_response(exc: MaintenanceConflict) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "ok": False,
            "message": str(exc),
            "code": "maintenance_conflict",
        },
    )


def _invalid_configuration_response(exc: ConfigurationValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "ok": False,
            "code": "invalid_configuration",
            "message": "Configuration validation failed",
            "errors": exc.errors,
        },
    )


def _stored_configuration_response() -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "code": "stored_configuration_invalid",
            "message": "Stored configuration is unreadable",
        },
    )


def _write_managed_config(path: Path, payload: dict) -> None:
    """Atomically persist a Manager-authorized config document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _query_database_path(settings: ManagerSettings, database: str) -> Path:
    if not is_query_database_name(database):
        raise HTTPException(status_code=400, detail="Invalid query database name")
    directory = (settings.layout.content_dir / "queries").resolve()
    candidate = directory / f"{database}.db"
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(directory)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Query database not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Query database escapes content directory") from exc
    if not resolved.is_file():
        raise HTTPException(status_code=400, detail="Query database is not a file")
    return resolved


def create_manager_app(
    settings: ManagerSettings,
    *,
    service: ManagerService | None = None,
    api_token: str | None = None,
) -> FastAPI:
    manager_service = service or create_manager_service(settings)
    if manager_service.control_service is None:
        manager_service.control_service = ControlChannelService(
            project_root=settings.layout.root,
            known_bot_ids=lambda: {
                path.stem
                for path in settings.layout.config_bots_dir.glob("*.json")
                if path.is_file() and path.stem != "_template"
            },
            heartbeat_timeout=settings.control_heartbeat_timeout,
            reload_timeout=settings.control_reload_timeout,
        )
    control_service = manager_service.control_service
    if manager_service.archive_coordinator is None:
        manager_service.archive_coordinator = ArchiveCoordinator(
            layout=settings.layout,
            service=manager_service,
            control_probe=control_service.probe,
        )
    else:
        # A test or embedding may provide its own coordinator; its control
        # health gate must still inspect the Manager-owned session service.
        manager_service.archive_coordinator.control_probe = control_service.probe
    archive_coordinator = manager_service.archive_coordinator
    runtime_support = manager_service.maintenance_runtime_support
    if runtime_support is None:
        runtime_support = getattr(archive_coordinator, "runtime_support", None)
    if runtime_support is None:
        runtime_support = MaintenanceRuntimeSupport(
            layout=settings.layout,
            service=manager_service,
            control_probe=control_service.probe,
        )
        manager_service.maintenance_runtime_support = runtime_support
    query_database_coordinator = QueryDatabaseCoordinator(
        layout=settings.layout,
        service=manager_service,
        runtime_support=runtime_support,
    )
    if manager_service.release_manager is None:
        manager_service.release_manager = ReleaseManager(
            layout=settings.layout,
            github_api=settings.github_api,
            protected_versions_loader=manager_service.store.protected_upgrade_versions,
        )
    release_manager = manager_service.release_manager
    if manager_service.upgrade_coordinator is None:
        manager_service.upgrade_coordinator = UpgradeCoordinator(
            layout=settings.layout,
            service=manager_service,
            archive_coordinator=archive_coordinator,
            release_manager=release_manager,
            platform_adapter=UnsupportedUpgradeAdapter(
                getattr(release_manager, "target", ("unknown", "unknown"))[0],
                "Automatic program installation is not configured",
            ),
        )
    upgrade_coordinator = manager_service.upgrade_coordinator
    expected_token = api_token or ensure_api_token(settings.token_path or settings.layout.manager_token)
    tasks: set[asyncio.Task] = set()
    critical_tasks: set[asyncio.Task] = set()
    release_tasks: set[asyncio.Task] = set()
    startup_recovery: dict[str, Any] = {
        "detected": False,
        "results": [],
        "task": None,
    }

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        scheduler_task: asyncio.Task | None = None
        handoff_task: asyncio.Task | None = None
        try:
            await archive_coordinator.recover()
            recovered = await upgrade_coordinator.recover(
                prepare_windows_handoff_only=True
            )
            startup_recovery["detected"] = any(
                item.get("owns_runtime_state") is True
                for item in recovered
            )
            startup_recovery["results"] = recovered
            if any(
                item.get("action") == "awaiting_api_bind"
                for item in recovered
            ):
                async def finish_handoff_after_bind() -> None:
                    await upgrade_coordinator.wait_api_ready()
                    current = asyncio.current_task()
                    if current is not None:
                        critical_tasks.add(current)
                    try:
                        results = await upgrade_coordinator.recover(
                            allow_startup_recovery=True
                        )
                        startup_recovery["results"] = results
                    finally:
                        if current is not None:
                            critical_tasks.discard(current)

                handoff_task = asyncio.create_task(
                    finish_handoff_after_bind()
                )
                startup_recovery["task"] = handoff_task
            if settings.release_scheduler_enabled:
                scheduler_task = asyncio.create_task(release_scheduler())
            yield
        finally:
            if (
                handoff_task is not None
                and not handoff_task.done()
                and handoff_task not in critical_tasks
            ):
                # Before API readiness the helper owns no transaction/lease.
                # Once recovery begins it moves into critical_tasks and must
                # reach a durable outcome without cancellation.
                handoff_task.cancel()
                await asyncio.gather(handoff_task, return_exceptions=True)
            if scheduler_task is not None:
                scheduler_task.cancel()
                await asyncio.gather(scheduler_task, return_exceptions=True)
            cancel_active = getattr(release_manager, "cancel_active", None)
            if cancel_active is not None:
                cancel_active()
            if release_tasks:
                await asyncio.gather(*release_tasks, return_exceptions=True)
            if tasks:
                _done, pending = await asyncio.wait(tasks, timeout=5)
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
            if critical_tasks:
                # Upgrade transactions own the instance maintenance lease and
                # may be the reason shutdown was requested.  Cancelling one
                # here can strand the program/data switch half-finished and
                # release Manager ownership before its durable outcome exists.
                await asyncio.gather(*critical_tasks, return_exceptions=True)
            if handoff_task is not None:
                await asyncio.gather(handoff_task, return_exceptions=True)
            if getattr(manager_service, "_startup_maintenance_active", False):
                # Public lifecycle requests remain gated during a failed
                # startup recovery.  Shutdown is the one internal boundary
                # that must still make a final attempt to release current/.
                try:
                    with manager_service.maintenance(
                        timeout=1,
                        allow_startup_recovery=True,
                    ) as maintenance:
                        await archive_coordinator.runtime_support.quiesce(
                            maintenance
                        )
                except Exception:
                    logger.exception(
                        "Manager shutdown could not quiesce Runtime during "
                        "startup recovery"
                    )
            await control_service.close()
            manager_service.close()

    app = FastAPI(title="DicePP Manager", version="2", lifespan=lifespan)
    app.state.manager_service = manager_service
    app.state.operation_tasks = tasks
    app.state.critical_operation_tasks = critical_tasks
    app.state.release_tasks = release_tasks
    app.state.control_service = control_service
    app.state.query_database_coordinator = query_database_coordinator

    manager_bearer = HTTPBearer(
        auto_error=False,
        scheme_name="ManagerBearerAuth",
        description="Private local Manager API token.",
    )

    async def require_manager_auth(
        credentials: HTTPAuthorizationCredentials | None = Depends(manager_bearer),
    ) -> None:
        if (
            credentials is None
            or credentials.scheme.lower() != "bearer"
            or not token_matches(expected_token, credentials.credentials)
        ):
            raise HTTPException(
                status_code=401,
                detail="Invalid Manager API token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    auth = [Depends(require_manager_auth)]

    @app.exception_handler(HTTPException)
    async def http_error(_request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"ok": False, "message": str(exc.detail)},
            headers=exc.headers,
        )

    @app.get("/v1/status", dependencies=auth)
    async def status():
        return {
            "ok": True,
            **(await manager_service.status()),
            "control": control_service.capability(),
        }

    @app.websocket("/v1/control/ws")
    async def control_websocket(ws: WebSocket):
        """Bot-only v1 control endpoint; Dashboard never accepts this traffic."""
        await control_service.websocket_endpoint(ws)

    @app.get("/v1/control/bots", dependencies=auth)
    async def control_bots():
        return {"ok": True, "bots": control_service.bot_statuses()}

    @app.post("/v1/control/reload", dependencies=auth)
    async def control_reload(request: Request):
        body = await _json_body(request)
        bot_id = body.get("bot_id")
        if bot_id is not None and (not isinstance(bot_id, str) or not bot_id):
            raise HTTPException(status_code=400, detail="bot_id must be a non-empty string")
        return {"ok": True, "results": await control_service.reload(bot_id)}

    @app.get("/v1/health", dependencies=auth)
    async def health():
        # This route can only run after Uvicorn has completed ASGI startup and
        # bound the authenticated local API.  Manager handoff recovery waits
        # for this boundary before running migrations and local hard health.
        upgrade_coordinator.mark_api_ready()
        handoff_task = startup_recovery.get("task")
        if isinstance(handoff_task, asyncio.Task) and handoff_task.done():
            # Surface unexpected recovery failures as an unhealthy Manager;
            # expected rollback/manual-recovery outcomes are returned normally.
            handoff_task.result()
        handoff = None
        if startup_recovery["detected"]:
            handoff = {
                "owns_runtime_state": True,
                "pending": (
                    isinstance(handoff_task, asyncio.Task)
                    and not handoff_task.done()
                ),
                "results": list(startup_recovery["results"]),
            }
        return {
            "ok": True,
            "dicepp_version": get_version(),
            "manager_identity": (
                handoff.get("manager_identity")
                if isinstance(handoff, dict)
                else None
            ),
            "upgrade_handoff": handoff,
        }

    @app.get("/v1/operations", dependencies=auth)
    async def operations(limit: int = Query(50, ge=1, le=200)):
        return {"ok": True, "operations": manager_service.list_operations(limit)}

    @app.get("/v1/operations/{operation_id}", dependencies=auth)
    async def operation(operation_id: str):
        result = manager_service.get_operation(operation_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Manager operation not found")
        return {"ok": True, "operation": result.to_dict()}

    @app.get("/v1/logs", dependencies=auth)
    async def runtime_logs(lines: int = Query(200, ge=1, le=1000)):
        try:
            result = await manager_service.runtime_logs(lines)
        except RuntimeOperationUnsupported as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        return {"ok": True, "logs": result}

    @app.get("/v1/runtime-units/{runtime_unit_id}/logs", dependencies=auth)
    async def unit_logs(runtime_unit_id: str, lines: int = Query(200, ge=1, le=1000)):
        try:
            result = await manager_service.logs(runtime_unit_id, lines)
        except UnknownRuntimeUnit as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeOperationUnsupported as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        return {"ok": True, "logs": result}

    async def finish_operation(manager_operation) -> None:
        try:
            await manager_service.run(manager_operation)
        except OperationFailed:
            # Durable state is the observable failure channel. Do not emit an
            # unhandled task exception after the caller has received the id.
            return

    async def finish_archive_create(
        manager_operation,
        body: dict,
        maintenance_lease: MaintenanceReservation,
    ) -> None:
        try:
            await await_critical_transaction(
                archive_coordinator.create(
                    manager_operation,
                    description=body.get("description"),
                    profile=body.get("profile", "regular"),
                    archive_kind=body.get("archive_kind", "manual"),
                    maintenance_lease=maintenance_lease,
                )
            )
        except ArchiveTransactionError:
            return
        except Exception as exc:
            manager_operation.transition(
                "failed",
                message=str(exc) or type(exc).__name__,
                detail={"error": "unexpected_archive_create_failure"},
            )
            manager_service.store.save(manager_operation)
        finally:
            maintenance_lease.release()

    async def finish_archive_restore(
        manager_operation,
        filename: str,
        body: dict,
        maintenance_lease: MaintenanceReservation,
    ) -> None:
        try:
            await await_critical_transaction(
                archive_coordinator.restore(
                    manager_operation,
                    filename=filename,
                    description=body.get("description"),
                    maintenance_lease=maintenance_lease,
                )
            )
        except ArchiveTransactionError:
            return
        except Exception as exc:
            manager_operation.transition(
                "failed",
                message=str(exc) or type(exc).__name__,
                detail={
                    "error": "unexpected_archive_restore_failure",
                    "target_filename": filename,
                },
            )
            manager_service.store.save(manager_operation)
        finally:
            maintenance_lease.release()

    async def finish_query_database_normalize(
        manager_operation,
        database: str,
        source: Path,
        maintenance_lease: MaintenanceReservation,
    ) -> None:
        try:
            await await_critical_transaction(
                query_database_coordinator.normalize(
                    manager_operation,
                    database=database,
                    source=source,
                    maintenance_lease=maintenance_lease,
                )
            )
        except QueryDatabaseNormalizationError:
            return
        except Exception as exc:
            manager_operation.transition(
                "failed",
                message=str(exc) or type(exc).__name__,
                detail={
                    "database": database,
                    "stage": "unexpected",
                    "error": str(exc) or type(exc).__name__,
                },
            )
            manager_service.store.save(manager_operation)
        finally:
            maintenance_lease.release()

    async def release_scheduler() -> None:
        while True:
            try:
                release_settings = release_manager.settings_loader()
                reservation = (
                    release_manager.queue_discovery()
                    if release_settings.discovery_enabled
                    else None
                )
                if reservation is not None:
                    worker = track_release_task(
                        finish_release_discovery(reservation)
                    )
                    await asyncio.shield(worker)
                release_settings = release_manager.settings_loader()
                delay = release_settings.check_interval_hours * 3600
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                release_manager.record_scheduler_error(exc)
                delay = release_manager.scheduler_error_delay
            await asyncio.sleep(delay)

    def track_task(coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return task

    def track_critical_task(coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        critical_tasks.add(task)
        task.add_done_callback(critical_tasks.discard)
        return task

    async def await_critical_transaction(coroutine):
        """Let a critical transaction reach a durable outcome after cancellation.

        ``asyncio.to_thread`` keeps running after its awaiter is cancelled.  The
        inner task is therefore shielded and drained before the task that owns
        the Manager maintenance reservation can leave its finally path.
        """
        transaction = asyncio.create_task(coroutine)
        while True:
            try:
                return await asyncio.shield(transaction)
            except asyncio.CancelledError:
                if transaction.done():
                    return transaction.result()

    def track_release_task(coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        release_tasks.add(task)
        task.add_done_callback(release_tasks.discard)
        return task

    async def finish_release_discovery(reservation) -> None:
        try:
            result = await asyncio.to_thread(
                release_manager.discover,
                reservation=reservation,
            )
        except (ReleaseError, ValueError):
            return
        try:
            release_settings = release_manager.settings_loader()
            available = result.get("available")
            if (
                release_settings.auto_download
                and isinstance(available, dict)
                and available.get("compatible") is True
                and (download_reservation := release_manager.queue_download())
                is not None
            ):
                track_release_task(
                    finish_release_download(None, download_reservation)
                )
        except (ReleaseError, ValueError) as exc:
            release_manager.record_scheduler_error(exc)

    async def finish_release_download(
        purpose: str | None,
        reservation,
    ) -> None:
        try:
            await asyncio.to_thread(
                release_manager.download,
                purpose=purpose,
                reservation=reservation,
            )
        except (ReleaseError, ValueError):
            return

    async def finish_upgrade(
        manager_operation,
        package,
        maintenance_lease: MaintenanceReservation,
    ) -> None:
        try:
            await await_critical_transaction(
                upgrade_coordinator.run(
                    manager_operation,
                    package,
                    maintenance_lease=maintenance_lease,
                )
            )
        except UpgradeTransactionError:
            return
        except Exception as exc:
            manager_operation.transition(
                "failed",
                message=str(exc) or type(exc).__name__,
                detail={
                    **manager_operation.detail,
                    "phase": "failed",
                    "error": str(exc) or type(exc).__name__,
                    "failure_code": "unexpected_upgrade_failure",
                },
            )
            manager_service.store.save(manager_operation)
        finally:
            maintenance_lease.release()

    @app.post("/v1/runtime-units/{runtime_unit_id}/{action}", dependencies=auth, status_code=202)
    async def operate(runtime_unit_id: str, action: str):
        if action not in VALID_ACTIONS:
            raise HTTPException(status_code=400, detail="Allowed actions: start, stop, restart")
        try:
            manager_operation = manager_service.submit(runtime_unit_id, cast(ManagerAction, action))
        except UnknownRuntimeUnit as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except OperationConflict as exc:
            return JSONResponse(
                status_code=409,
                content={"ok": False, "message": str(exc), "operation": exc.operation.to_dict()},
            )
        except OperationFailed as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"ok": False, "message": str(exc), "operation": exc.operation.to_dict()},
            )
        task = asyncio.create_task(finish_operation(manager_operation))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return {"ok": True, "operation": manager_operation.to_dict()}

    @app.get("/v1/config/user", dependencies=auth)
    async def config_user_get():
        try:
            config = read_config_object(settings.layout.config_user)
        except ConfigurationValidationError:
            return _stored_configuration_response()
        return {"ok": True, "config": config}

    @app.put("/v1/config/user", dependencies=auth)
    async def config_user_save(request: Request):
        body = await _json_body(request)
        try:
            with manager_service.maintenance():
                canonical = validate_user_candidate(settings.layout, body)
                _write_managed_config(settings.layout.config_user, canonical)
        except MaintenanceConflict as exc:
            return _maintenance_conflict_response(exc)
        except ConfigurationValidationError as exc:
            return _invalid_configuration_response(exc)
        return {
            "ok": True,
            "saved": True,
            "application": "deferred",
            "restart_required": True,
        }

    @app.get("/v1/config/bots/{bot_id}", dependencies=auth)
    async def config_bot_get(bot_id: str):
        try:
            path = settings.layout.bot_config_path(bot_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not path.exists():
            raise HTTPException(status_code=404, detail="Bot configuration not found")
        try:
            config = read_config_object(path, missing_is_empty=False)
        except ConfigurationValidationError:
            return _stored_configuration_response()
        return {"ok": True, "config": config}

    @app.put("/v1/config/bots/{bot_id}", dependencies=auth)
    async def config_bot_save(bot_id: str, request: Request):
        body = await _json_body(request)
        try:
            path = settings.layout.bot_config_path(bot_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            with manager_service.maintenance():
                canonical = validate_bot_candidate(settings.layout, bot_id, body)
                _write_managed_config(path, canonical)
        except MaintenanceConflict as exc:
            return _maintenance_conflict_response(exc)
        except ConfigurationValidationError as exc:
            return _invalid_configuration_response(exc)
        return {
            "ok": True,
            "saved": True,
            "application": "deferred",
            "restart_required": True,
        }

    @app.get("/v1/content/query-databases", dependencies=auth)
    async def query_databases_list():
        directory = settings.layout.content_dir / "queries"
        try:
            state = load_query_database_state(directory)
        except QueryDatabaseStateError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        databases = []
        if directory.exists():
            for path in sorted(directory.glob("*.db"), key=lambda item: item.name.casefold()):
                try:
                    resolved = _query_database_path(settings, path.stem)
                except HTTPException:
                    continue
                stat = resolved.stat()
                databases.append({
                    "name": path.stem,
                    "enabled": state.is_enabled(path.stem),
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                })
        return {"ok": True, "databases": databases}

    @app.put("/v1/content/query-databases/{database}/enabled", dependencies=auth)
    async def query_database_enabled(database: str, request: Request):
        _query_database_path(settings, database)
        body = await _json_body(request)
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=400, detail="enabled must be a boolean")
        try:
            with manager_service.maintenance():
                state = set_query_database_enabled(
                    settings.layout.content_dir / "queries",
                    database,
                    enabled,
                )
        except MaintenanceConflict as exc:
            return _maintenance_conflict_response(exc)
        except QueryDatabaseStateError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "ok": True,
            "database": database,
            "enabled": state.is_enabled(database),
            "application": "immediate",
            "restart_required": False,
        }

    @app.post(
        "/v1/content/query-databases/{database}/normalize/dry-run",
        dependencies=auth,
    )
    async def query_database_normalize_dry_run(database: str):
        """Build and validate a disposable candidate without changing the source."""
        source = _query_database_path(settings, database)
        candidate = source.with_name(
            f".{source.name}.{uuid4().hex}.dry-run.tmp"
        )
        try:
            report = await asyncio.to_thread(
                prepare_query_database_candidate,
                source,
                candidate,
            )
        except (OSError, ValueError, sqlite3.DatabaseError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"数据库无法自动修复：{exc}",
            ) from exc
        finally:
            candidate.unlink(missing_ok=True)
        detail = query_normalization_report_detail(report)
        return {
            "ok": True,
            "database": database,
            "requires_confirmation": True,
            "report": detail,
        }

    @app.post(
        "/v1/content/query-databases/{database}/normalize",
        dependencies=auth,
        status_code=202,
    )
    async def query_database_normalize(database: str):
        source = _query_database_path(settings, database)
        try:
            maintenance_lease = manager_service.reserve_maintenance()
        except MaintenanceConflict as exc:
            return _maintenance_conflict_response(exc)
        try:
            manager_operation = query_database_coordinator.new_operation()
            track_critical_task(
                finish_query_database_normalize(
                    manager_operation,
                    database,
                    source,
                    maintenance_lease,
                )
            )
        except BaseException:
            maintenance_lease.release()
            raise
        return {"ok": True, "operation": manager_operation.to_dict()}

    @app.get("/v1/archives", dependencies=auth)
    async def archives_list():
        return {"ok": True, "archives": archive_coordinator.list()}

    @app.post("/v1/archives/estimate", dependencies=auth)
    async def archives_estimate(request: Request):
        body = await _json_body(request)
        try:
            estimate = archive_coordinator.estimate(str(body.get("profile", "regular")))
        except ArchiveError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "estimate": estimate}

    @app.post("/v1/archives", dependencies=auth, status_code=202)
    async def archives_create(request: Request):
        body = await _json_body(request)
        description = body.get("description")
        if description is not None and not isinstance(description, str):
            raise HTTPException(status_code=400, detail="description must be a string")
        try:
            maintenance_lease = manager_service.reserve_maintenance()
        except MaintenanceConflict as exc:
            return _maintenance_conflict_response(exc)
        try:
            manager_operation = archive_coordinator.new_operation("archive.create")
            track_critical_task(
                finish_archive_create(manager_operation, body, maintenance_lease)
            )
        except BaseException:
            maintenance_lease.release()
            raise
        return {"ok": True, "operation": manager_operation.to_dict()}

    @app.get("/v1/archives/{filename}", dependencies=auth)
    async def archives_detail(filename: str):
        try:
            archive, manifest = archive_coordinator.detail(filename)
        except ArchiveNameError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ArchiveNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ArchiveInvalidError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": True, "archive": archive, "manifest": manifest}

    @app.post("/v1/archives/{filename}/verify", dependencies=auth)
    async def archives_verify(filename: str):
        try:
            verification = archive_coordinator.verify(filename)
        except ArchiveNameError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ArchiveNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ArchiveInvalidError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": True, "verification": verification}

    @app.post("/v1/archives/{filename}/restore-plan", dependencies=auth)
    async def archives_restore_plan(filename: str):
        try:
            plan = archive_coordinator.plan(filename)
        except ArchiveRestorePlanVerificationError as exc:
            return JSONResponse(
                status_code=409,
                content={
                    "ok": False,
                    "message": str(exc),
                    "verification": exc.verification,
                },
            )
        except (ArchiveNameError, ArchiveError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": True, "plan": plan}

    @app.post("/v1/archives/{filename}/restore", dependencies=auth, status_code=202)
    async def archives_restore(filename: str, request: Request):
        body = await _json_body(request)
        if body.get("confirm_restore") is not True:
            raise HTTPException(status_code=400, detail="confirm_restore must be true")
        try:
            maintenance_lease = manager_service.reserve_maintenance()
        except MaintenanceConflict as exc:
            return _maintenance_conflict_response(exc)
        try:
            plan = archive_coordinator.plan(filename)
        except ArchiveRestorePlanVerificationError as exc:
            maintenance_lease.release()
            return JSONResponse(
                status_code=409,
                content={
                    "ok": False,
                    "message": str(exc),
                    "verification": exc.verification,
                },
            )
        except ArchiveError as exc:
            maintenance_lease.release()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if plan.get("problems") or plan.get("blocked"):
            maintenance_lease.release()
            raise HTTPException(status_code=409, detail="Archive restore plan is blocked")
        try:
            manager_operation = archive_coordinator.new_operation("archive.restore")
            track_critical_task(
                finish_archive_restore(
                    manager_operation,
                    filename,
                    body,
                    maintenance_lease,
                )
            )
        except BaseException:
            maintenance_lease.release()
            raise
        return {"ok": True, "operation": manager_operation.to_dict()}

    @app.delete("/v1/archives/{filename}", dependencies=auth)
    async def archives_delete(filename: str):
        try:
            archive = archive_coordinator.delete(filename)
        except ArchiveNameError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ArchiveNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ArchiveError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, "deleted": filename, "archive": archive}

    @app.get("/v1/archives/{filename}/export", dependencies=auth)
    async def archives_export(filename: str):
        try:
            path = archive_coordinator.export_path(filename)
        except ArchiveNameError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ArchiveNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ArchiveInvalidError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return FileResponse(
            path,
            media_type="application/zip",
            filename=path.name,
        )

    @app.post("/v1/archives/import", dependencies=auth)
    async def archives_import(request: Request, x_archive_filename: str | None = Header(None)):
        if not x_archive_filename:
            raise HTTPException(status_code=400, detail="X-Archive-Filename is required")
        total = 0
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024**2) as upload:
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    raise HTTPException(status_code=413, detail="Archive upload is too large")
                upload.write(chunk)
            upload.seek(0)
            try:
                result = await asyncio.to_thread(
                    archive_coordinator.import_stream,
                    urllib.parse.unquote(x_archive_filename),
                    upload,
                )
            except ArchiveNameError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except ArchiveInvalidError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": True, "import": result}

    @app.get("/v1/releases/status", dependencies=auth)
    async def releases_status():
        try:
            result = release_manager.status()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "ok": True,
            **result,
            "install_supported": upgrade_coordinator.install_supported,
        }

    @app.post("/v1/releases/check", dependencies=auth, status_code=202)
    async def releases_check():
        try:
            reservation = release_manager.queue_discovery(manual=True)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if reservation is None:
            return JSONResponse(
                status_code=409,
                content={
                    "ok": False,
                    "message": "Another release operation is already running",
                },
            )
        track_release_task(finish_release_discovery(reservation))
        return {"ok": True, **release_manager.status()}

    @app.post("/v1/releases/download", dependencies=auth, status_code=202)
    async def releases_download(request: Request):
        body = await _json_body(request)
        purpose = body.get("purpose")
        if purpose is not None and (not isinstance(purpose, str) or not purpose):
            raise HTTPException(status_code=400, detail="purpose must be a non-empty string")
        reservation = release_manager.queue_download()
        if reservation is None:
            return JSONResponse(
                status_code=409,
                content={"ok": False, "message": "A release download is already running"},
            )
        track_release_task(finish_release_download(purpose, reservation))
        return {"ok": True, **release_manager.status()}

    @app.get("/v1/upgrades/preview", dependencies=auth)
    async def upgrades_preview(version: str | None = None):
        try:
            preview = await upgrade_coordinator.preview(version)
        except (UpgradeError, ReleaseError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, "preview": preview}

    @app.get("/v1/upgrades/status", dependencies=auth)
    async def upgrades_status():
        return {"ok": True, **upgrade_coordinator.status()}

    @app.post("/v1/upgrades/confirm", dependencies=auth, status_code=202)
    async def upgrades_confirm(request: Request):
        body = await _json_body(request)
        version = body.get("version")
        token = body.get("confirmation_token")
        if not isinstance(version, str) or not version:
            raise HTTPException(status_code=400, detail="version is required")
        if not isinstance(token, str) or not token:
            raise HTTPException(
                status_code=400, detail="confirmation_token is required"
            )
        if upgrade_coordinator.status()["active_operation"] is not None:
            raise HTTPException(
                status_code=409, detail="Another upgrade operation is active"
            )
        try:
            maintenance_lease = manager_service.reserve_maintenance()
        except MaintenanceConflict as exc:
            return _maintenance_conflict_response(exc)
        try:
            manager_operation, package = upgrade_coordinator.confirm(
                version=version,
                confirmation_token=token,
            )
        except UpgradeConfirmationError as exc:
            maintenance_lease.release()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (UpgradeCompatibilityError, ReleaseError, ValueError) as exc:
            maintenance_lease.release()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        try:
            track_critical_task(
                finish_upgrade(manager_operation, package, maintenance_lease)
            )
        except BaseException:
            maintenance_lease.release()
            raise
        return {"ok": True, "operation": manager_operation.to_dict()}

    return app


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Request body must be JSON") from exc
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Request body must be an object")
    return body
