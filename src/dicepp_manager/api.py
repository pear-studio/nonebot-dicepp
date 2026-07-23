"""Private standalone Manager HTTP API."""

from __future__ import annotations

import asyncio
import tempfile
import urllib.parse
from contextlib import asynccontextmanager
from typing import cast

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from .archive import (
    MAX_ARCHIVE_BYTES,
    ArchiveError,
    ArchiveInvalidError,
    ArchiveNameError,
    ArchiveNotFoundError,
    ArchiveRestorePlanBlockedError,
    ArchiveRestorePlanVerificationError,
)
from .archive_coordinator import ArchiveCoordinator, ArchiveTransactionError
from .auth import ensure_api_token, token_matches
from .config import ManagerSettings
from .factory import create_manager_service
from .models import ManagerAction, VALID_ACTIONS
from .runtime import RuntimeOperationUnsupported
from .service import ManagerService, OperationConflict, OperationFailed, UnknownRuntimeUnit


def create_manager_app(
    settings: ManagerSettings,
    *,
    service: ManagerService | None = None,
    api_token: str | None = None,
) -> FastAPI:
    manager_service = service or create_manager_service(settings)
    if manager_service.archive_coordinator is None:
        manager_service.archive_coordinator = ArchiveCoordinator(
            layout=settings.layout,
            service=manager_service,
        )
    archive_coordinator = manager_service.archive_coordinator
    expected_token = api_token or ensure_api_token(settings.token_path or settings.layout.manager_token)
    tasks: set[asyncio.Task] = set()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            await archive_coordinator.recover()
            yield
            if tasks:
                _done, pending = await asyncio.wait(tasks, timeout=5)
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
        finally:
            manager_service.close()

    app = FastAPI(title="DicePP Manager", version="2", lifespan=lifespan)
    app.state.manager_service = manager_service
    app.state.operation_tasks = tasks

    async def require_manager_auth(authorization: str | None = Header(None)) -> None:
        scheme, _, supplied = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token_matches(expected_token, supplied):
            raise HTTPException(status_code=401, detail="Invalid Manager API token")

    auth = [Depends(require_manager_auth)]

    @app.exception_handler(HTTPException)
    async def http_error(_request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"ok": False, "message": str(exc.detail)})

    @app.get("/v1/status", dependencies=auth)
    async def status():
        return {"ok": True, **(await manager_service.status())}

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

    async def finish_archive_create(manager_operation, body: dict) -> None:
        try:
            await archive_coordinator.create(
                manager_operation,
                description=body.get("description"),
                profile=body.get("profile", "regular"),
                archive_kind=body.get("archive_kind", "manual"),
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

    async def finish_archive_restore(manager_operation, filename: str, body: dict) -> None:
        try:
            await archive_coordinator.restore(
                manager_operation,
                filename=filename,
                description=body.get("description"),
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
        manager_operation = archive_coordinator.new_operation("archive.create")
        task = asyncio.create_task(finish_archive_create(manager_operation, body))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
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
        if plan.get("problems") or plan.get("blocked"):
            raise HTTPException(status_code=409, detail="Archive restore plan is blocked")
        manager_operation = archive_coordinator.new_operation("archive.restore")
        task = asyncio.create_task(
            finish_archive_restore(manager_operation, filename, body)
        )
        tasks.add(task)
        task.add_done_callback(tasks.discard)
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
