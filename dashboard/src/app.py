import asyncio
import ipaddress
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

import logging

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from dicepp_data import (
    PERSONA_DB_ASSET,
    QueryDatabaseStateError,
    load_query_database_state,
    set_query_database_enabled,
)
from dicepp_data.archive import (
    MAX_ARCHIVE_BYTES,
    ArchiveError,
    ArchiveInvalidError,
    ArchiveNameError,
    ArchiveNotFoundError,
    create_archive,
    delete_archive,
    estimate_archive,
    export_archive_path,
    import_archive,
    list_archives,
    read_archive_detail,
    verify_archive,
)
from dicepp_data.instance_data import (
    InstanceDataError,
    InstanceDataNotEmptyError,
    clear_instance_data,
    import_instance_data,
)
from dicepp_meta import get_project_info, get_version

from .auth import (
    create_session,
    get_session,
    is_initialized,
    require_auth,
    revoke_session,
    set_password_db,
    validate_password,
    verify_password_db,
)
from .audit import get_recent as audit_get_recent
from .audit import log as audit_log
from .bot_process import BotProcessController, create_bot_process_controller
from .config import DashboardPaths
from .config_store import (
    ConfigurationValidationError,
    read_config_object,
    validate_bot_candidate,
    validate_user_candidate,
    write_config_object,
)
from .query_database import normalization_report, report_detail, write_normalized_database
from .query_audit import (
    QueryAuditFormatError,
    inspect_query_database,
    list_query_entries,
    list_query_redirects,
)
from .runtime_service import BotNotStopped, BotRuntimeService

logger = logging.getLogger("dashboard")


# ── FastAPI app ───────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialize Dashboard state and its optional Bot controller."""
    db_path = str(DashboardPaths.DASHBOARD_DB)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    _init_db(db_path)
    app.state.dashboard_db = db_path
    app.state.dashboard_paths = DashboardPaths
    app.state.login_failures = {}
    # Serialize Bot lifecycle changes with in-place data maintenance.
    controller = getattr(app.state, "bot_process_controller", None)
    if controller is None:
        controller = create_bot_process_controller(
            project_root=DashboardPaths.instance_layout().root,
            log_path=DashboardPaths.runtime_log_path(),
        )
        app.state.bot_process_controller = controller
    runtime_service = getattr(app.state, "bot_runtime_service", None)
    if runtime_service is None or runtime_service.controller is not controller:
        runtime_service = BotRuntimeService(
            controller,
        )
        app.state.bot_runtime_service = runtime_service
    auto_start = bool(getattr(app.state, "bot_auto_start", False))
    try:
        if auto_start:
            await runtime_service.operate("start")
        yield
    finally:
        await asyncio.to_thread(controller.shutdown)


app = FastAPI(title="DicePP Dashboard", version=get_version(), lifespan=lifespan)

_LOGIN_FAILURE_LIMIT = 5
_LOGIN_FAILURE_COOLDOWN_SECONDS = 30
_LOGIN_FAILURE_STALE_SECONDS = 10 * 60
_SQLITE_MAX_INT = 2**63 - 1


# ── Exception handler ─────────────────────────────────────────────────────────


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    """Return error responses in the standard format {"ok": false, "message": "..."}."""
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.detail if isinstance(exc.detail, dict) and "ok" in exc.detail
               else {"ok": False, "message": str(exc.detail)},
        headers=exc.headers,
    )


# ── Database init ─────────────────────────────────────────────────────────────


def _init_db(db_path: str) -> None:
    """Initialize dashboard.db with required tables and WAL mode."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")

        conn.execute(
            """CREATE TABLE IF NOT EXISTS auth (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL
            )"""
        )

        conn.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                expires_at TEXT NOT NULL
            )"""
        )

        conn.execute(
            """CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                operator TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT NOT NULL,
                detail TEXT DEFAULT '',
                ip TEXT DEFAULT ''
            )"""
        )

        conn.commit()
    finally:
        conn.close()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _ok(data: dict = None) -> dict:
    """Wrap success response."""
    result = {"ok": True}
    if data:
        result.update(data)
    return result


def _err(message: str, status_code: int = 400) -> HTTPException:
    """Raise an error response."""
    raise HTTPException(status_code=status_code, detail={"ok": False, "message": message})


def _read_json_safe(path: Path) -> dict:
    """Read a JSON file, return empty dict if missing or corrupted."""
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.warning(f"Skipping unreadable config file: {path}")
        return {}


def _is_path_traversal(path: str, base: Path) -> bool:
    """Check if the resolved path escapes the given base directory."""
    if not path:
        return True
    try:
        resolved = (base / path).resolve()
        return not resolved.is_relative_to(base.resolve())
    except (ValueError, OSError):
        return True


_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')


def _validate_identifier(value: str, name: str) -> None:
    """Validate identifier: 1-64 chars, alphanumeric + underscore + hyphen."""
    if not value or not _ID_PATTERN.match(value):
        _err(f"{name} 格式无效：仅允许 1~64 位字母、数字、下划线或连字符", 400)


def _apply_deep(target: dict, path: str, value) -> None:
    """Set a value at a dotted path in a nested dict, creating intermediate keys."""
    parts = path.split(".")
    d = target
    for part in parts[:-1]:
        if part not in d or not isinstance(d[part], dict):
            d[part] = {}
        d = d[part]
    d[parts[-1]] = value


def _remove_deep(target: dict, path: str) -> bool:
    """Remove a key at a dotted path in a nested dict. Returns True if removed."""
    parts = path.split(".")
    d = target
    for part in parts[:-1]:
        if part not in d or not isinstance(d[part], dict):
            return False
        d = d[part]
    if parts[-1] in d:
        del d[parts[-1]]
        return True
    return False


_CONTENT_SUBDIRS = {"decks", "random", "queries"}

_XLSX_MAGIC = b"\x50\x4b\x03\x04"


def _is_xlsx(path: Path) -> bool:
    """Check if a file is an xlsx by its magic bytes."""
    try:
        with open(path, "rb") as f:
            header = f.read(4)
            return header == _XLSX_MAGIC
    except OSError:
        return False


def _config_save_result(result: dict, **extra: object) -> dict:
    """Expose the local save result and required restart indication."""
    return _ok({
        **extra,
        "saved": bool(result.get("saved", True)),
        "application": result.get("application", "deferred"),
        "restart_required": bool(result.get("restart_required", True)),
    })


# ── Auth endpoints ────────────────────────────────────────────────────────────


def _is_windows_runtime() -> bool:
    return sys.platform == "win32"


def _is_local_or_private_address(value: Optional[str]) -> bool:
    """Return whether *value* is localhost or an actual LAN address."""
    if not value:
        return False
    value = value.strip().strip("[]").lower()
    if value == "localhost":
        return True
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if address.is_loopback or address.is_link_local:
        return True
    if isinstance(address, ipaddress.IPv4Address):
        private_v4 = (
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
        )
        return any(address in network for network in private_v4)
    return address in ipaddress.ip_network("fc00::/7")


def _web_setup_allowed(request: Request) -> bool:
    """Allow initial web setup only from a direct Windows local/LAN URL."""
    if not _is_windows_runtime():
        return False
    client_host = request.client.host if request.client else None
    return _is_local_or_private_address(
        client_host
    ) and _is_local_or_private_address(request.url.hostname)


def _setup_denied_message() -> str:
    if not _is_windows_runtime():
        return "Linux 请先通过命令行执行 Dashboard 管理员初始化"
    return "首次初始化请通过本机或局域网 IP 直接访问，不能通过公网域名或反向代理初始化"


def _login_failure_key(request: Request) -> str:
    return request.client.host if request.client and request.client.host else "unknown"


def _login_failure_entries(request: Request) -> dict[str, dict[str, float]]:
    entries = getattr(request.app.state, "login_failures", None)
    if entries is None:
        entries = {}
        request.app.state.login_failures = entries
    return entries


def _raise_if_login_rate_limited(request: Request) -> None:
    entries = _login_failure_entries(request)
    key = _login_failure_key(request)
    entry = entries.get(key)
    if not entry:
        return

    now = time.monotonic()
    if now - entry.get("last_failed_at", 0) > _LOGIN_FAILURE_STALE_SECONDS:
        entries.pop(key, None)
        return

    blocked_until = entry.get("blocked_until", 0)
    if blocked_until > now:
        retry_after = max(1, int(blocked_until - now))
        raise HTTPException(
            status_code=429,
            detail={"ok": False, "message": f"登录失败次数过多，请 {retry_after} 秒后再试"},
            headers={"Retry-After": str(retry_after)},
        )
    if blocked_until:
        entries.pop(key, None)


def _record_login_failure(request: Request) -> None:
    entries = _login_failure_entries(request)
    key = _login_failure_key(request)
    now = time.monotonic()
    entry = entries.get(key)
    if not entry or now - entry.get("last_failed_at", 0) > _LOGIN_FAILURE_STALE_SECONDS:
        entry = {"count": 0, "blocked_until": 0}

    entry["count"] = entry.get("count", 0) + 1
    entry["last_failed_at"] = now
    if entry["count"] >= _LOGIN_FAILURE_LIMIT:
        entry["blocked_until"] = now + _LOGIN_FAILURE_COOLDOWN_SECONDS
    entries[key] = entry


def _clear_login_failures(request: Request) -> None:
    _login_failure_entries(request).pop(_login_failure_key(request), None)


@app.post("/api/auth/setup")
async def auth_setup(request: Request):
    """Set initial password. Returns 403 if already initialized."""
    db_path = request.app.state.dashboard_db
    if is_initialized(db_path):
        raise HTTPException(status_code=403, detail={"ok": False, "message": "Already initialized"})
    if not _web_setup_allowed(request):
        raise HTTPException(
            status_code=403,
            detail={"ok": False, "message": _setup_denied_message()},
        )

    body = await request.json()
    password = body.get("password", "")

    err = validate_password(password)
    if err:
        raise HTTPException(status_code=400, detail={"ok": False, "message": err})

    set_password_db(db_path, password)
    audit_log(db_path, "auth.setup", "auth", "Initial password set", ip=request.client.host if request.client else "")

    # Auto-login: create session
    token = create_session(db_path)
    response = JSONResponse(_ok())
    response.set_cookie(key="session", value=token, httponly=True, samesite="strict", max_age=7 * 86400)
    return response


@app.post("/api/auth/login")
async def auth_login(request: Request):
    """Login with password, set session cookie."""
    _raise_if_login_rate_limited(request)

    body = await request.json()
    password = body.get("password", "")

    db_path = request.app.state.dashboard_db
    if not verify_password_db(db_path, password):
        _record_login_failure(request)
        raise HTTPException(status_code=401, detail={"ok": False, "message": "Invalid password"})

    _clear_login_failures(request)
    token = create_session(db_path)
    audit_log(db_path, "auth.login", "auth", "Login", ip=request.client.host if request.client else "")

    response = JSONResponse(_ok())
    response.set_cookie(key="session", value=token, httponly=True, samesite="strict", max_age=7 * 86400)
    return response


@app.post("/api/auth/logout", dependencies=[Depends(require_auth)])
async def auth_logout(request: Request):
    """Revoke session."""
    db_path = request.app.state.dashboard_db
    token = request.cookies.get("session")
    if token:
        revoke_session(db_path, token)

    response = JSONResponse(_ok())
    response.delete_cookie(key="session")
    return response


@app.post("/api/auth/change_password", dependencies=[Depends(require_auth)])
async def auth_change_password(request: Request):
    """Change password (verify old first)."""
    body = await request.json()
    old_pwd = body.get("old_password", "")
    new_pwd = body.get("new_password", "")

    db_path = request.app.state.dashboard_db
    if not verify_password_db(db_path, old_pwd):
        raise HTTPException(status_code=401, detail={"ok": False, "message": "Old password is incorrect"})

    err = validate_password(new_pwd)
    if err:
        raise HTTPException(status_code=400, detail={"ok": False, "message": err})

    new_token = set_password_db(db_path, new_pwd, rotate_session=True)
    audit_log(db_path, "auth.change_password", "auth", "Password changed", ip=request.client.host if request.client else "")

    response = JSONResponse(_ok())
    response.set_cookie(key="session", value=new_token, httponly=True, samesite="strict", max_age=7 * 86400)
    return response


@app.get("/api/auth/status")
async def auth_status(request: Request):
    """Return initialization and authentication status."""
    db_path = request.app.state.dashboard_db
    initialized = is_initialized(db_path)

    authenticated = False
    token = request.cookies.get("session")
    if token:
        authenticated = get_session(db_path, token) is not None

    setup_allowed = not initialized and _web_setup_allowed(request)
    return _ok({
        "initialized": initialized,
        "authenticated": authenticated,
        "setup_allowed": setup_allowed,
        "setup_message": "" if setup_allowed else _setup_denied_message(),
        "project": get_project_info(),
    })


def _archive_layout():
    return DashboardPaths.instance_layout()


def _archive_error_response(exc: ArchiveError) -> JSONResponse:
    status = 400 if isinstance(exc, ArchiveNameError) else 404 if isinstance(exc, ArchiveNotFoundError) else 422 if isinstance(exc, ArchiveInvalidError) else 409
    return JSONResponse(status_code=status, content={"ok": False, "message": str(exc)})


def _instance_data_error_response(exc: InstanceDataError) -> JSONResponse:
    status = 409 if isinstance(exc, InstanceDataNotEmptyError) else 422
    return JSONResponse(status_code=status, content={"ok": False, "message": str(exc)})


@app.get("/api/archives", dependencies=[Depends(require_auth)])
async def archives_list(request: Request):
    del request
    try:
        archives = await asyncio.to_thread(list_archives, layout=_archive_layout())
    except ArchiveError as exc:
        return _archive_error_response(exc)
    return _ok({"archives": archives})


@app.post("/api/archives/estimate", dependencies=[Depends(require_auth)])
async def archives_estimate(request: Request):
    body = await request.json()
    profile = body.get("profile", "regular") if isinstance(body, dict) else "regular"
    try:
        result = await asyncio.to_thread(estimate_archive, layout=_archive_layout(), profile=profile)
    except ArchiveError as exc:
        return _archive_error_response(exc)
    return _ok(result)


@app.post("/api/archives", dependencies=[Depends(require_auth)])
async def archives_create(request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        _err("Request body must be a JSON object", 400)
    profile = body.get("profile", "regular")
    description = body.get("description")
    if profile not in {"regular", "full"}:
        _err("profile must be regular or full", 400)
    if description is not None and not isinstance(description, str):
        _err("description must be a string or null", 400)
    controller = _get_bot_process_controller(request)
    runtime_service = _get_bot_runtime_service(request)

    def create_when_stopped():
        if controller.status().state != "stopped":
            raise BotNotStopped("Bot must be stopped before archive creation")
        return create_archive(
            layout=_archive_layout(),
            profile=profile,
            description=description,
        )

    try:
        summary, manifest = await runtime_service.run_maintenance(create_when_stopped)
    except BotNotStopped as exc:
        return JSONResponse(status_code=409, content={"ok": False, "message": str(exc)})
    except ArchiveError as exc:
        return _archive_error_response(exc)
    return _ok({"archive": summary, "manifest": manifest})


@app.post("/api/archives/{filename:path}/verify", dependencies=[Depends(require_auth)])
async def archives_verify(filename: str, request: Request):
    del request
    try:
        payload = await asyncio.to_thread(verify_archive, filename, layout=_archive_layout())
    except ArchiveError as exc:
        return _archive_error_response(exc)
    return _ok({"verification": payload})


@app.get("/api/archives/{filename:path}/export", dependencies=[Depends(require_auth)])
async def archives_export(filename: str, request: Request):
    del request
    try:
        path = await asyncio.to_thread(export_archive_path, filename, layout=_archive_layout())
    except ArchiveError as exc:
        return _archive_error_response(exc)
    return FileResponse(path, media_type="application/zip", filename=path.name)


@app.get("/api/health")
async def dashboard_health(request: Request):
    """Return Dashboard readiness; Bot lifecycle is local to this process."""
    db_path = getattr(request.app.state, "dashboard_db", None)
    if not isinstance(db_path, str):
        return JSONResponse(
            status_code=503,
            content={
                "status": "failed",
                "component": "dashboard",
                "message": "Dashboard state is not initialized",
            },
        )
    try:
        with sqlite3.connect(db_path) as connection:
            connection.execute("SELECT 1")
    except sqlite3.Error as exc:
        return JSONResponse(
            status_code=503,
            content={
                "status": "failed",
                "component": "dashboard",
                "message": f"Dashboard store is unavailable: {exc}",
            },
        )
    return {
        "status": "ok",
        "component": "dashboard",
        "version": get_version(),
    }


@app.post("/api/archives/import", dependencies=[Depends(require_auth)])
async def archives_import(request: Request):
    filename = request.headers.get("X-Archive-Filename", "")
    if not filename:
        _err("X-Archive-Filename is required", 400)
    declared_length = request.headers.get("content-length")
    if declared_length is not None:
        try:
            if int(declared_length) > MAX_ARCHIVE_BYTES:
                _err("Archive upload is too large", 413)
        except ValueError:
            _err("Invalid Content-Length", 400)
    total = 0
    with tempfile.SpooledTemporaryFile(max_size=8 * 1024**2) as upload:
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_ARCHIVE_BYTES:
                _err("Archive upload is too large", 413)
            upload.write(chunk)
        upload.seek(0)
        try:
            payload = await asyncio.to_thread(import_archive, filename, upload, layout=_archive_layout())
        except ArchiveError as exc:
            return _archive_error_response(exc)
    return _ok({"import": payload})


@app.get("/api/archives/{filename:path}", dependencies=[Depends(require_auth)])
async def archives_detail(filename: str, request: Request):
    del request
    try:
        archive, manifest = await asyncio.to_thread(read_archive_detail, filename, layout=_archive_layout())
    except ArchiveError as exc:
        return _archive_error_response(exc)
    return _ok({"archive": archive, "manifest": manifest})


@app.delete("/api/archives/{filename:path}", dependencies=[Depends(require_auth)])
async def archives_delete(filename: str, request: Request):
    del request
    try:
        archive = await asyncio.to_thread(delete_archive, filename, layout=_archive_layout())
    except ArchiveError as exc:
        return _archive_error_response(exc)
    return _ok({"deleted": filename, "archive": archive})


@app.post("/api/instance/clear", dependencies=[Depends(require_auth)])
async def instance_clear(request: Request):
    body = await request.json()
    if not isinstance(body, dict) or body.get("confirm") is not True:
        _err("confirm must be true for clearing business data", 400)
    controller = _get_bot_process_controller(request)
    runtime_service = _get_bot_runtime_service(request)

    def clear_when_stopped():
        if controller.status().state != "stopped":
            raise BotNotStopped("Bot must be stopped before clearing business data")
        return clear_instance_data(_archive_layout())

    try:
        result = await runtime_service.run_maintenance(clear_when_stopped)
    except BotNotStopped as exc:
        return JSONResponse(status_code=409, content={"ok": False, "message": str(exc)})
    except InstanceDataError as exc:
        return _instance_data_error_response(exc)
    return _ok(result)


@app.post("/api/instance/import", dependencies=[Depends(require_auth)])
async def instance_import(request: Request):
    body = await request.json()
    if not isinstance(body, dict) or body.get("confirm") is not True:
        _err("confirm must be true for importing business data", 400)
    archive = body.get("archive")
    source_path = body.get("source_path")
    if archive is not None and not isinstance(archive, str):
        _err("archive must be a filename", 400)
    if source_path is not None and not isinstance(source_path, str):
        _err("source_path must be a directory path", 400)
    if isinstance(source_path, str) and not source_path.strip():
        _err("source_path must be a non-empty directory path", 400)
    controller = _get_bot_process_controller(request)
    runtime_service = _get_bot_runtime_service(request)

    def import_when_stopped():
        if controller.status().state != "stopped":
            raise BotNotStopped("Bot must be stopped before importing business data")
        return import_instance_data(
            _archive_layout(),
            archive=archive,
            source_root=source_path,
        )

    try:
        result = await runtime_service.run_maintenance(import_when_stopped)
    except BotNotStopped as exc:
        return JSONResponse(status_code=409, content={"ok": False, "message": str(exc)})
    except InstanceDataError as exc:
        return _instance_data_error_response(exc)
    return _ok(result)


# ── Bot discovery ─────────────────────────────────────────────────────────────


@app.get("/api/bots", dependencies=[Depends(require_auth)])
async def list_bots(request: Request):
    """Scan config/bots/*.json, exclude _template.json, return bot_id list."""
    bots_dir = DashboardPaths.CONFIG_BOTS_DIR
    ids = []
    if bots_dir.exists():
        for f in sorted(bots_dir.iterdir()):
            if f.suffix == ".json" and f.stem != "_template":
                ids.append(f.stem)

    return _ok({"bots": ids})


# ── Config editing ────────────────────────────────────────────────────────────


# Module-level cache for the loaded pydantic_models module
_pydantic_module_cache = None


class DashboardConfigSchemaError(RuntimeError):
    """Raised when the required standalone Dashboard schema cannot be loaded."""


def _load_pydantic_models_module():
    """Load the standalone config schema without importing the bot package.

    Returns the module on success and raises if the required asset is missing.
    Module reference is cached so _cached_config_field_metadata() and
    _cached_config_layout() share a single import.  PyInstaller onefile builds
    carry the canonical schema as a minimal data asset under ``_MEIPASS``;
    source-tree candidates are development fallbacks only.
    """
    global _pydantic_module_cache
    if _pydantic_module_cache is not None:
        return _pydantic_module_cache

    import importlib.util
    import types

    source_relative_path = Path("src/plugins/DicePP/core/config/pydantic_models.py")
    frozen_root = getattr(sys, "_MEIPASS", None)
    frozen_candidate = (
        Path(frozen_root) / "dashboard_config_schema" / "pydantic_models.py"
        if frozen_root
        else None
    )
    candidates = tuple(
        path
        for path in (
            frozen_candidate,
            DashboardPaths.PROJECT_ROOT / source_relative_path,
            DashboardPaths.SOURCE_ROOT / source_relative_path,
        )
        if path is not None
    )
    _pydantic_path = next((path for path in candidates if path.exists()), None)
    if _pydantic_path is None:
        raise DashboardConfigSchemaError(
            "Dashboard configuration schema asset is missing"
        )

    package_name = "_dicepp_dashboard_config_schema"
    module_name = f"{package_name}.pydantic_models"
    try:
        sys.modules.pop(module_name, None)
        sys.modules.pop(f"{package_name}.builtin_providers", None)
        package = types.ModuleType(package_name)
        package.__path__ = [str(_pydantic_path.parent)]
        package.__package__ = package_name
        sys.modules[package_name] = package
        spec = importlib.util.spec_from_file_location(
            module_name,
            str(_pydantic_path),
        )
        if spec is None or spec.loader is None:
            raise DashboardConfigSchemaError(
                f"Cannot create schema loader for {_pydantic_path}"
            )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)
        _pydantic_module_cache = mod
        return mod
    except DashboardConfigSchemaError:
        raise
    except Exception as exc:
        import logging
        logging.getLogger("dashboard").exception(
            "Unexpected error loading config field metadata from %s", _pydantic_path
        )
        raise DashboardConfigSchemaError(
            f"Cannot load Dashboard configuration schema from {_pydantic_path}"
        ) from exc


def _flatten_json_schema(s: dict, defs: dict, prefix: str = "",
                         inherited_tab: str = "", inherited_section: str = "") -> dict:
    """Flatten a Pydantic v2 JSON schema to dotted-key → {title, description, tab, section}.

    Model-level json_schema_extra (ConfigDict) keys are merged directly into the
    model schema by Pydantic v2.  Likewise, Field(json_schema_extra={...}) keys
    are merged directly into each property schema — *not* nested under a
    ``json_schema_extra`` sub-key.  This function reads ``dashboard_tab`` and
    ``dashboard_section`` directly from schema nodes.

    Known limitation: does not handle anyOf (Optional[BaseModel]) or
    items.$ref (List[BaseModel]).  These patterns exist for fields like
    persona_ai.providers.<name>.models[*].circuit_breaker, but their dotted
    keys contain dynamic segments the flat config_merged output cannot match.
    """
    # Model-level json_schema_extra keys are merged at the model schema top level
    tab = s.get("dashboard_tab", inherited_tab)
    section = s.get("dashboard_section", inherited_section)

    result = {}
    for key, prop in s.get("properties", {}).items():
        full = f"{prefix}.{key}" if prefix else key
        title = prop.get("title", key)
        desc = prop.get("description", "") or ""

        # Field-level json_schema_extra keys are merged directly into the
        # property schema by Pydantic v2 (not nested under json_schema_extra).
        field_tab = prop.get("dashboard_tab", tab)
        field_section = prop.get("dashboard_section", section)

        result[full] = {
            "title": title,
            "description": desc,
            "tab": field_tab,
            "section": field_section,
        }

        # Resolve $ref for nested models
        if "$ref" in prop:
            ref_name = prop["$ref"].split("/")[-1]
            ref_schema = defs.get(ref_name, {})
            result.update(_flatten_json_schema(ref_schema, defs, full,
                                               field_tab, field_section))
        elif "properties" in prop:
            result.update(_flatten_json_schema(prop, defs, full,
                                               field_tab, field_section))
        # Handle allOf (e.g., for Optional with $ref)
        elif "allOf" in prop:
            for item in prop["allOf"]:
                if "$ref" in item:
                    ref_name = item["$ref"].split("/")[-1]
                    ref_schema = defs.get(ref_name, {})
                    result.update(_flatten_json_schema(ref_schema, defs, full,
                                                       field_tab, field_section))
        # Handle additionalProperties.$ref (Dict[str, BaseModel], e.g. providers)
        elif "additionalProperties" in prop and isinstance(prop["additionalProperties"], dict):
            ap = prop["additionalProperties"]
            if "$ref" in ap:
                ref_name = ap["$ref"].split("/")[-1]
                ref_schema = defs.get(ref_name, {})
                result.update(_flatten_json_schema(ref_schema, defs, full,
                                                   field_tab, field_section))

    return result


def _get_config_field_metadata() -> dict:
    """Extract field titles, descriptions, tab and section from BotConfig Pydantic model.

    Returns a flat dict mapping dotted keys to {title, description, tab, section}.
    Model-level json_schema_extra (ConfigDict) provides default tab/section;
    field-level json_schema_extra (Field) can override section per-field.
    """
    mod = _load_pydantic_models_module()

    try:
        BotConfig = mod.BotConfig
        schema = BotConfig.model_json_schema()
        defs = schema.get("$defs", {})
        return _flatten_json_schema(schema, defs)
    except Exception:
        import logging
        logging.getLogger("dashboard").exception(
            "Failed to extract field metadata from BotConfig"
        )
        return {}


# Cache metadata at module level (computed once on first use)
_config_field_metadata_cache: Optional[dict] = None
_config_layout_cache: Optional[dict] = None


def _cached_config_field_metadata() -> dict:
    global _config_field_metadata_cache
    if _config_field_metadata_cache is None:
        result = _get_config_field_metadata()
        if result:
            _config_field_metadata_cache = result
        return result
    return _config_field_metadata_cache


def _cached_config_layout() -> dict:
    """Return DASHBOARD_LAYOUT from the required schema module."""
    global _config_layout_cache
    if _config_layout_cache is None:
        mod = _load_pydantic_models_module()
        _config_layout_cache = getattr(mod, "DASHBOARD_LAYOUT", {})
    return _config_layout_cache


def _find_meta(dotted: str, meta: dict) -> dict:
    """Match a dotted data key against static schema metadata keys.

    Three-level fallback for dynamic keys (e.g. providers.<name>.api_key):
    1. Exact match
    2. Remove one segment at a time (skip the dynamic key segment)
    3. Prefix truncation from right (parent node fallback for tab/section)
    """
    if dotted in meta:
        return meta[dotted]
    parts = dotted.split(".")
    # Level 2: try removing each segment (skip dynamic key)
    for i in range(len(parts)):
        candidate = ".".join(parts[:i] + parts[i+1:])
        if candidate in meta:
            return meta[candidate]
    # Level 3: prefix fallback (get tab/section from parent, label less precise)
    for i in range(len(parts) - 1, 0, -1):
        prefix = ".".join(parts[:i])
        if prefix in meta:
            return meta[prefix]
    return {}


@app.get("/api/config/merged", dependencies=[Depends(require_auth)])
async def config_merged(request: Request, bot_id: Optional[str] = Query(None)):
    """Merge code defaults + user.json + bots/{bot_id}.json with source annotation."""
    models = _load_pydantic_models_module()
    default_cfg = models.BotConfig().model_dump(mode="json", by_alias=True)
    try:
        user_cfg = read_config_object(DashboardPaths.CONFIG_USER)
    except ConfigurationValidationError:
        _err("Stored configuration is unreadable", 500)

    # Annotate merged config: global=default, user overlays=user, bot overlays=bot
    result_annotated = {}

    def _annotate_deep(base: dict, overlay: dict, source: str, prefix: str = ""):
        for key, value in base.items():
            if key.startswith("_comment"):
                continue
            dotted = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                # Recurse into nested dicts — if overlay has a dict for this key, pass it
                overlay_child = overlay.get(key, {}) if isinstance(overlay.get(key), dict) else {}
                _annotate_deep(value, overlay_child, source, dotted)
            else:
                if key in overlay:
                    result_annotated[dotted] = {"value": overlay[key], "source": source}
                else:
                    result_annotated[dotted] = {"value": value, "source": "default"}

        # Extra keys from overlay not in base
        for key, value in overlay.items():
            if key.startswith("_comment"):
                continue
            dotted = f"{prefix}.{key}" if prefix else key
            if key not in base:
                if isinstance(value, dict):
                    _annotate_deep(value, {}, source, dotted)
                else:
                    result_annotated[dotted] = {"value": value, "source": source}

    # Merge user over default
    _annotate_deep(default_cfg, user_cfg, "user")

    # Merge bot over user+default
    if bot_id:
        _validate_identifier(bot_id, "bot_id")
        try:
            bot_cfg = read_config_object(DashboardPaths.bot_config_path(bot_id))
        except ConfigurationValidationError:
            _err("Stored configuration is unreadable", 500)
        # Re-annotate: start from the previous result, update with bot overrides
        # For each key in bot_cfg, overwrite source to "bot"
        def _flatten_and_annotate(d: dict, prefix: str = ""):
            """Flatten dict to dotted keys, return {dotted: value}."""
            items = {}
            for key, value in d.items():
                if key.startswith("_comment"):
                    continue
                dotted = f"{prefix}.{key}" if prefix else key
                if isinstance(value, dict):
                    items.update(_flatten_and_annotate(value, dotted))
                else:
                    items[dotted] = value
            return items

        bot_flat = _flatten_and_annotate(bot_cfg)
        for dotted, value in bot_flat.items():
            result_annotated[dotted] = {"value": value, "source": "bot"}

    # Load field metadata from Pydantic models (title=label, description=tooltip, tab, section)
    field_meta = _cached_config_field_metadata()

    # Build output with labels, descriptions, tab and section
    output = {}
    for dotted, entry in result_annotated.items():
        meta = _find_meta(dotted, field_meta)
        output[dotted] = {
            "value": entry["value"],
            "source": entry["source"],
            "label": meta.get("title", dotted),
            "description": meta.get("description", ""),
            "tab": meta.get("tab", "config"),
            "section": meta.get("section", "runtime"),
        }

    layout = _cached_config_layout()
    return _ok({"config": output, "layout": layout})


@app.post("/api/config/set", dependencies=[Depends(require_auth)])
async def config_set(request: Request):
    """Deep merge a value into user.json and persist it locally."""
    body = await request.json()
    if not isinstance(body, dict):
        _err("Body must be a JSON object")
    path = body.get("path", "")
    value = body.get("value")

    if not path:
        _err("path is required")

    try:
        user_cfg = read_config_object(DashboardPaths.CONFIG_USER)
    except ConfigurationValidationError:
        _err("Stored configuration is unreadable", 500)

    _apply_deep(user_cfg, path, value)
    try:
        validate_user_candidate(DashboardPaths.instance_layout(), user_cfg)
        write_config_object(DashboardPaths.CONFIG_USER, user_cfg)
    except ConfigurationValidationError as exc:
        return JSONResponse(status_code=422, content={"ok": False, "message": str(exc), "errors": exc.errors})

    db_path = request.app.state.dashboard_db
    audit_detail = json.dumps({"value": "***"}, ensure_ascii=False) if re.search(r'\.api_key$', path) else json.dumps({"value": value}, ensure_ascii=False)
    audit_log(db_path, "config.set", path, audit_detail,
              ip=request.client.host if request.client else "")

    return _config_save_result({})


@app.post("/api/config/reset", dependencies=[Depends(require_auth)])
async def config_reset(request: Request):
    """Remove a key from user.json, then persist it locally."""
    body = await request.json()
    if not isinstance(body, dict):
        _err("Body must be a JSON object")
    path = body.get("path", "")

    if not path:
        _err("path is required")

    try:
        user_cfg = read_config_object(DashboardPaths.CONFIG_USER)
    except ConfigurationValidationError:
        _err("Stored configuration is unreadable", 500)

    removed = _remove_deep(user_cfg, path)
    try:
        validate_user_candidate(DashboardPaths.instance_layout(), user_cfg)
        write_config_object(DashboardPaths.CONFIG_USER, user_cfg)
    except ConfigurationValidationError as exc:
        return JSONResponse(status_code=422, content={"ok": False, "message": str(exc), "errors": exc.errors})

    db_path = request.app.state.dashboard_db
    audit_log(db_path, "config.reset", path, "reset to default",
              ip=request.client.host if request.client else "")

    return _config_save_result({}, removed=removed)


@app.get("/api/config/bots/{bot_id}", dependencies=[Depends(require_auth)])
async def config_bot_get(bot_id: str, request: Request):
    """Read bot config file content."""
    _validate_identifier(bot_id, "bot_id")
    try:
        cfg = read_config_object(DashboardPaths.bot_config_path(bot_id), missing_is_empty=False)
    except FileNotFoundError:
        _err("Bot configuration not found", 404)
    except ConfigurationValidationError:
        _err("Stored configuration is unreadable", 500)
    return _ok({"config": cfg})


@app.post("/api/config/bots/{bot_id}/save", dependencies=[Depends(require_auth)])
async def config_bot_save(bot_id: str, request: Request):
    """Validate JSON, persist bot config locally, and audit the save."""
    _validate_identifier(bot_id, "bot_id")
    body = await request.json()
    if not isinstance(body, dict):
        _err("Body must be a JSON object")

    try:
        validate_bot_candidate(DashboardPaths.instance_layout(), bot_id, body)
        write_config_object(DashboardPaths.bot_config_path(bot_id), body)
    except ConfigurationValidationError as exc:
        return JSONResponse(status_code=422, content={"ok": False, "message": str(exc), "errors": exc.errors})

    db_path = request.app.state.dashboard_db
    audit_log(db_path, "config.bot.save", f"bots/{bot_id}", "",
              ip=request.client.host if request.client else "")

    return _config_save_result({})


@app.get("/api/config/user", dependencies=[Depends(require_auth)])
async def config_user_get(request: Request):
    """Return raw user.json content for JSON view editing."""
    try:
        user_cfg = read_config_object(DashboardPaths.CONFIG_USER)
    except ConfigurationValidationError:
        _err("Stored configuration is unreadable", 500)
    return _ok({"config": user_cfg})


@app.post("/api/config/user/save", dependencies=[Depends(require_auth)])
async def config_user_save(request: Request):
    """Validate then overwrite user.json locally and audit the save."""
    body = await request.json()
    if not isinstance(body, dict):
        _err("Body must be a JSON object")

    try:
        validate_user_candidate(DashboardPaths.instance_layout(), body)
        write_config_object(DashboardPaths.CONFIG_USER, body)
    except ConfigurationValidationError as exc:
        return JSONResponse(status_code=422, content={"ok": False, "message": str(exc), "errors": exc.errors})

    db_path = request.app.state.dashboard_db
    audit_log(db_path, "config.user.save", "user.json", "",
              ip=request.client.host if request.client else "")

    return _config_save_result({})


# ── Persona character cards ──────────────────────────────────────────────────

_CHAR_NAME_PATTERN = re.compile(r'^[^\x00/\\]{1,128}$')


def _validate_character_name(name: str) -> None:
    """Validate character name: 1-128 chars, no path separators or null bytes."""
    if not name or not _CHAR_NAME_PATTERN.match(name):
        _err(f"角色名格式无效：1~128 位非空字符，禁止路径分隔符和空字节", 400)


@app.get("/api/persona/characters", dependencies=[Depends(require_auth)])
async def persona_characters(request: Request):
    """List character directories under content/characters/."""
    chars_dir = DashboardPaths.CONTENT_DIR / "characters"
    characters = []
    if chars_dir.exists():
        for d in sorted(chars_dir.iterdir()):
            if d.is_dir() and not d.name.startswith('.'):
                char_yaml = d / "character.yaml"
                characters.append({
                    "name": d.name,
                    "has_config": char_yaml.exists(),
                })
    return _ok({"characters": characters})


@app.get("/api/persona/characters/{name}", dependencies=[Depends(require_auth)])
async def persona_character_get(name: str, request: Request):
    """Read character.yaml for a character."""
    _validate_character_name(name)
    if _is_path_traversal(name, DashboardPaths.CONTENT_DIR / "characters"):
        _err("Path traversal detected", 400)

    yaml_path = DashboardPaths.CONTENT_DIR / "characters" / name / "character.yaml"
    if not yaml_path.exists():
        _err(f"Character not found: {name}", 404)

    try:
        content = yaml_path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        _err("Cannot read character.yaml as text", 400)

    return _ok({"name": name, "content": content})


@app.post("/api/persona/characters/{name}/save", dependencies=[Depends(require_auth)])
async def persona_character_save(name: str, request: Request):
    """Save character.yaml for a character (creates directory if needed)."""
    _validate_character_name(name)
    if _is_path_traversal(name, DashboardPaths.CONTENT_DIR / "characters"):
        _err("Path traversal detected", 400)

    body = await request.json()
    content = body.get("content", "")
    if not isinstance(content, str):
        _err("content must be a string")

    yaml_path = DashboardPaths.CONTENT_DIR / "characters" / name / "character.yaml"
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: .tmp + fsync + os.replace.
    tmp_path = yaml_path.with_suffix(".yaml.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    fd = os.open(str(tmp_path), os.O_RDWR)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp_path, yaml_path)

    db_path = request.app.state.dashboard_db
    audit_log(db_path, "persona.character.save", name, "",
              ip=request.client.host if request.client else "")

    return _ok({"saved": True})


# ── Shared bot status computation ──────────────────────────────────────────────


_overview_logger = logging.getLogger("dashboard.overview")


def _find_persona_db(bot_id: str) -> Optional[Path]:
    """Scan bot data dir for personas_data_*.db, return the newest by mtime."""
    matches = [
        match.path
        for match in PERSONA_DB_ASSET.iter_matches(
            DashboardPaths.instance_layout(),
            bot_id=bot_id,
        )
    ]
    if not matches:
        return None
    if len(matches) > 1:
        newest = max(matches, key=lambda p: p.stat().st_mtime)
        _overview_logger.warning(
            "Multiple persona DBs found for %s, using newest: %s",
            bot_id, newest.name,
        )
        return newest
    return matches[0]


def _compute_core_stats(bot_id: str) -> dict:
    """Query bot_data.db for simple row counts (user_stat / group_stat)."""
    db_path = DashboardPaths.bot_data_db_path(bot_id)
    if not db_path.exists():
        return {"users": 0, "groups": 0}

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        users = conn.execute(
            "SELECT COUNT(*) as cnt FROM user_stat"
        ).fetchone()["cnt"]
        groups = conn.execute(
            "SELECT COUNT(*) as cnt FROM group_stat"
        ).fetchone()["cnt"]
    except sqlite3.Error:
        _overview_logger.warning(
            "Core stats query failed for %s", db_path, exc_info=True,
        )
        return {"users": 0, "groups": 0}
    finally:
        conn.close()
    return {"users": users, "groups": groups}


def _compute_persona_stats(persona_db_path: Path, today: str) -> Optional[dict]:
    """Query persona.db for today's chat stats, character state, and events."""

    conn = sqlite3.connect(f"file:{persona_db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        # Chat stats today
        row = conn.execute(
            "SELECT COUNT(*) as total,"
            " COUNT(DISTINCT user_id) as users,"
            " COUNT(DISTINCT CASE WHEN group_id != '' THEN group_id END) as groups"
            " FROM message_stream WHERE type = 'chat' AND date(created_at) = ?",
            (today,),
        ).fetchone()

        chat_messages = row["total"] or 0
        chat_users = row["users"] or 0
        chat_groups = row["groups"] or 0

        # Character state (stored as JSON in text column)
        character_state = {}
        row = conn.execute(
            "SELECT text FROM persona_character_state WHERE id = 1"
        ).fetchone()
        if row and row["text"]:
            try:
                data = json.loads(row["text"])
                if isinstance(data, dict):
                    for key in ("energy", "mood", "health", "current_intention"):
                        if data.get(key) is not None:
                            character_state[key] = data[key]
            except json.JSONDecodeError:
                # Legacy plain-text format
                character_state["text"] = row["text"][:200]

        # Today's events (most recent 3)
        events = []
        cursor = conn.execute(
            "SELECT id, event_type, description FROM persona_daily_events"
            " WHERE date = ? ORDER BY id DESC LIMIT 3",
            (today,),
        )
        for r in cursor.fetchall():
            events.append({
                "id": r["id"],
                "type": r["event_type"],
                "description": r["description"],
            })

    except sqlite3.Error:
        _overview_logger.warning(
            "Persona stats query failed for %s", persona_db_path, exc_info=True,
        )
        return None
    finally:
        conn.close()

    return {
        "chat_messages": chat_messages,
        "chat_users": chat_users,
        "chat_groups": chat_groups,
        "character_state": character_state,
        "events": events,
    }


def _compute_llm_usage(persona_db_path: Path, today: str) -> Optional[dict]:
    """Query persona_llm_traces for today's usage, aggregated and by-model."""

    conn = sqlite3.connect(f"file:{persona_db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(tokens_in + tokens_out), 0) as total_tokens,"
            " COUNT(*) as requests,"
            " COUNT(CASE WHEN status != 'ok' THEN 1 END) as errors"
            " FROM persona_llm_traces WHERE date(created_at) = ?",
            (today,),
        ).fetchone()

        total_tokens = row["total_tokens"] or 0
        requests = row["requests"] or 0
        errors = row["errors"] or 0

        by_model = []
        cursor = conn.execute(
            "SELECT selected_provider, selected_model,"
            " COUNT(*) as requests,"
            " COALESCE(SUM(tokens_in + tokens_out), 0) as tokens"
            " FROM persona_llm_traces WHERE date(created_at) = ?"
            " GROUP BY selected_provider, selected_model"
            " ORDER BY tokens DESC",
            (today,),
        )
        for r in cursor.fetchall():
            by_model.append({
                "provider": r["selected_provider"] or "",
                "model": r["selected_model"] or "",
                "requests": r["requests"],
                "tokens": r["tokens"],
            })

    except sqlite3.Error:
        _overview_logger.warning(
            "LLM usage query failed for %s", persona_db_path, exc_info=True,
        )
        return None
    finally:
        conn.close()

    return {
        "total_tokens": total_tokens,
        "requests": requests,
        "errors": errors,
        "by_model": by_model,
    }


@app.get("/api/overview", dependencies=[Depends(require_auth)])
async def overview(request: Request, bot_id: Optional[str] = Query(None)):
    """Aggregate overview: bot status, core stats, persona stats, LLM usage."""
    result = {"bots": await _local_bot_statuses(request)}

    if bot_id:
        _validate_identifier(bot_id, "bot_id")
        result["core_stats"] = _compute_core_stats(bot_id)

        persona_db = _find_persona_db(bot_id)
        if persona_db:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            result["persona_stats"] = _compute_persona_stats(persona_db, today)
            result["llm_usage"] = _compute_llm_usage(persona_db, today)

    return _ok(result)


@app.get("/api/bots/status", dependencies=[Depends(require_auth)])
async def bot_status(request: Request):
    """Return local config identities and the single Bot process state."""
    return _ok({"bots": await _local_bot_statuses(request)})


async def _local_bot_statuses(request: Request) -> list[dict]:
    bots = []
    config_dir = DashboardPaths.instance_layout().config_bots_dir
    if config_dir.exists():
        for path in sorted(config_dir.glob("*.json")):
            if path.name == "_template.json":
                continue
            bots.append({"bot_id": path.stem})
    status = await asyncio.to_thread(_get_bot_process_controller(request).status)
    for bot in bots:
        bot["online"] = status.running
        bot["version"] = get_version()
        bot["last_heartbeat_ts"] = None
        bot["status"] = status.to_dict()
    return bots


def _get_bot_process_controller(request: Request) -> BotProcessController:
    controller = getattr(request.app.state, "bot_process_controller", None)
    if controller is None:
        controller = create_bot_process_controller(
            project_root=DashboardPaths.instance_layout().root,
            log_path=DashboardPaths.runtime_log_path(),
        )
        request.app.state.bot_process_controller = controller
    return controller


def _get_bot_runtime_service(request: Request) -> BotRuntimeService:
    controller = _get_bot_process_controller(request)
    service = getattr(request.app.state, "bot_runtime_service", None)
    if service is None or service.controller is not controller:
        service = BotRuntimeService(
            controller,
        )
        request.app.state.bot_runtime_service = service
    return service


@app.get("/api/bot/status", dependencies=[Depends(require_auth)])
async def bot_process_status(request: Request):
    status = await asyncio.to_thread(_get_bot_process_controller(request).status)
    return _ok({"status": status.to_dict()})


@app.post("/api/bot/{action}", dependencies=[Depends(require_auth)])
async def bot_process_action(action: str, request: Request):
    if action not in {"start", "stop", "restart"}:
        _err("Bot action must be start, stop, or restart", 400)
    runtime_service = _get_bot_runtime_service(request)
    try:
        status = await runtime_service.operate(action)
    except (OSError, RuntimeError) as exc:
        audit_log(
            request.app.state.dashboard_db,
            f"bot.{action}",
            "bot",
            json.dumps({"status": "failed", "message": str(exc)}, ensure_ascii=False),
            ip=request.client.host if request.client else "",
        )
        _err(str(exc) or type(exc).__name__, 500)
    audit_log(
        request.app.state.dashboard_db,
        f"bot.{action}",
        "bot",
        json.dumps(status.to_dict(), ensure_ascii=False),
        ip=request.client.host if request.client else "",
    )
    return _ok({"status": status.to_dict()})


@app.get("/api/bot/logs", dependencies=[Depends(require_auth)])
async def bot_process_logs(
    request: Request,
    lines: int = Query(200, ge=1, le=1000),
):
    controller = _get_bot_process_controller(request)
    text = await asyncio.to_thread(controller.tail_logs, lines)
    return _ok({
        "logs": {
            "text": text,
            "source": str(DashboardPaths.runtime_log_path()),
            "lines": len(text.splitlines()),
            "truncated": False,
        }
    })


# ── SSE endpoint ────────────────────────────────────────────────────────────────


@app.get("/api/events", dependencies=[Depends(require_auth)])
async def events_stream(request: Request):
    """SSE endpoint: pushes bot status updates to connected dashboard clients."""

    async def _generate():
        bots = await _local_bot_statuses(request)
        yield f"data: {json.dumps({'bots': bots})}\n\n"
        while True:
            await asyncio.sleep(2)
            bots = await _local_bot_statuses(request)
            yield f"data: {json.dumps({'bots': bots})}\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")


# ── Content management ────────────────────────────────────────────────────────


@app.get("/api/content/{subdir}", dependencies=[Depends(require_auth)])
async def content_list(subdir: str, request: Request):
    """List files in a content subdirectory."""
    if subdir not in _CONTENT_SUBDIRS:
        _err(f"Invalid subdirectory: {subdir}. Allowed: {', '.join(sorted(_CONTENT_SUBDIRS))}", 404)

    content_dir = DashboardPaths.CONTENT_DIR / subdir
    if not content_dir.exists():
        return _ok({"files": []})

    query_state = None
    if subdir == "queries":
        try:
            query_state = load_query_database_state(content_dir)
        except QueryDatabaseStateError as exc:
            _err(str(exc), 500)

    files = []
    for f in sorted(content_dir.iterdir()):
        if f.name.startswith('.'):
            continue
        if f.is_file():
            stat = f.stat()
            item = {
                "name": f.name,
                "size": stat.st_size,
                "modified": stat.st_mtime,
            }
            if query_state is not None and f.suffix == ".db":
                item["enabled"] = query_state.is_enabled(f.stem)
            files.append(item)

    return _ok({"files": files})


def _query_database_path(db_name: str) -> Path:
    """Resolve a validated query database name inside content/queries."""
    if not db_name or len(db_name) > 128 or db_name.endswith(".db"):
        _err("db_name 无效", 400)
    queries_dir = (DashboardPaths.CONTENT_DIR / "queries").resolve()
    candidate = queries_dir / f"{db_name}.db"
    try:
        db_path = candidate.resolve(strict=True)
    except FileNotFoundError:
        _err(f"Query database not found: {db_name}", 404)
    try:
        db_path.relative_to(queries_dir)
    except ValueError:
        _err("Path traversal detected", 400)
    if not db_path.is_file():
        _err(f"Query database is not a file: {db_name}", 400)
    return db_path


def _query_rowids(value: Optional[str]) -> Optional[list[int]]:
    """Parse a bounded rowid filter supplied by a concrete audit warning."""
    if value is None:
        return None
    if not value.strip():
        return []
    parts = value.split(",")
    if len(parts) > 200:
        _err("rowids 最多允许 200 项", 400)
    try:
        rowids = [int(part) for part in parts]
    except ValueError:
        _err("rowids 必须是用逗号分隔的正整数", 400)
    if any(rowid <= 0 or rowid > _SQLITE_MAX_INT for rowid in rowids):
        _err(f"rowids 必须是 1 到 {_SQLITE_MAX_INT} 之间的正整数", 400)
    return list(dict.fromkeys(rowids))


@app.post("/api/content/queries/{db_name}/enabled", dependencies=[Depends(require_auth)])
async def content_queries_enabled(db_name: str, request: Request):
    """Enable or disable one query database in Dashboard-owned state."""
    _query_database_path(db_name)
    body = await request.json()
    if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
        _err("enabled 必须是布尔值", 400)
    try:
        state = set_query_database_enabled(
            DashboardPaths.CONTENT_DIR / "queries", db_name, body["enabled"]
        )
    except QueryDatabaseStateError as exc:
        _err(str(exc), 400)
    enabled = state.is_enabled(db_name)
    audit_log(
        request.app.state.dashboard_db,
        "content.query.enable" if enabled else "content.query.disable",
        db_name,
        "",
        ip=request.client.host if request.client else "",
    )
    return _ok({"database": db_name, "enabled": enabled})


@app.post(
    "/api/content/queries/{db_name}/normalize/dry-run",
    dependencies=[Depends(require_auth)],
)
async def content_queries_normalize_dry_run(db_name: str, request: Request):
    """Preview normalization without writing the source database."""
    db_path = _query_database_path(db_name)
    try:
        report = await asyncio.to_thread(normalization_report, db_path)
    except (OSError, ValueError, sqlite3.DatabaseError) as exc:
        _err(f"数据库无法自动修复：{exc}", 400)
    return _ok({"database": db_name, "requires_confirmation": True, "report": report_detail(report)})


@app.post("/api/content/queries/{db_name}/normalize", dependencies=[Depends(require_auth)])
async def content_queries_normalize(db_name: str, request: Request):
    """Normalize a query database locally while the controlled Bot is stopped."""
    db_path = _query_database_path(db_name)
    controller = _get_bot_process_controller(request)
    runtime_service = _get_bot_runtime_service(request)

    def normalize_when_stopped():
        if controller.status().state != "stopped":
            raise BotNotStopped("Bot must be stopped before query database normalization")
        report = normalization_report(db_path)
        write_normalized_database(db_path, report)
        return report

    try:
        report = await runtime_service.run_maintenance(normalize_when_stopped)
    except BotNotStopped as exc:
        return JSONResponse(status_code=409, content={"ok": False, "message": str(exc)})
    except (OSError, ValueError, sqlite3.DatabaseError) as exc:
        _err(f"数据库无法自动修复：{exc}", 400)
    return _ok({"database": db_name, "normalized": True, "report": report_detail(report)})


@app.get("/api/content/queries/{db_name}/audit", dependencies=[Depends(require_auth)])
async def content_queries_audit(db_name: str, request: Request):
    """Return summary statistics for a simple query database."""
    db_path = _query_database_path(db_name)

    try:
        result = inspect_query_database(db_path)
        return _ok({"stats": result["stats"]})
    except QueryAuditFormatError as exc:
        _err(str(exc), 422)
    except sqlite3.DatabaseError as e:
        _err(f"数据库读取失败：{e}。请确认文件是有效且未损坏的 SQLite 查询库。", 500)


@app.get("/api/content/queries/{db_name}/warnings", dependencies=[Depends(require_auth)])
async def content_queries_warnings(
    db_name: str,
    request: Request,
    offset: int = Query(0, ge=0, le=_SQLITE_MAX_INT),
    limit: int = Query(50, ge=1, le=200),
):
    """Return concrete audit warnings without clustering them."""
    db_path = _query_database_path(db_name)
    try:
        warnings = inspect_query_database(db_path)["warnings"]
        return _ok({
            "records": warnings[offset:offset + limit],
            "total": len(warnings),
            "offset": offset,
            "limit": limit,
        })
    except QueryAuditFormatError as exc:
        _err(str(exc), 422)
    except sqlite3.DatabaseError as e:
        _err(f"数据库读取失败：{e}。请确认文件是有效且未损坏的 SQLite 查询库。", 500)


@app.get("/api/content/queries/{db_name}/entries", dependencies=[Depends(require_auth)])
async def content_queries_entries(
    db_name: str,
    request: Request,
    offset: int = Query(0, ge=0, le=_SQLITE_MAX_INT),
    limit: int = Query(50, ge=1, le=200),
    q: Optional[str] = Query(None),
    scope: str = Query("all"),
    rowids: Optional[str] = Query(None),
):
    """Return a semantic, filtered page of simple query data."""
    db_path = _query_database_path(db_name)

    try:
        return _ok(
            list_query_entries(
                db_path,
                offset=offset,
                limit=limit,
                query=q,
                scope=scope,
                rowids=_query_rowids(rowids),
            )
        )
    except QueryAuditFormatError as exc:
        _err(str(exc), 422)
    except sqlite3.DatabaseError as e:
        _err(f"数据库读取失败：{e}。请确认文件是有效且未损坏的 SQLite 查询库。", 500)


@app.get("/api/content/queries/{db_name}/redirects", dependencies=[Depends(require_auth)])
async def content_queries_redirects(
    db_name: str,
    request: Request,
    offset: int = Query(0, ge=0, le=_SQLITE_MAX_INT),
    limit: int = Query(50, ge=1, le=200),
    q: Optional[str] = Query(None),
    rowids: Optional[str] = Query(None),
):
    """Return a semantic, filtered page of optional query redirects."""
    db_path = _query_database_path(db_name)
    try:
        return _ok(
            list_query_redirects(
                db_path,
                offset=offset,
                limit=limit,
                query=q,
                rowids=_query_rowids(rowids),
            )
        )
    except QueryAuditFormatError as exc:
        _err(str(exc), 422)
    except sqlite3.DatabaseError as e:
        _err(f"数据库读取失败：{e}。请确认文件是有效且未损坏的 SQLite 查询库。", 500)


@app.get("/api/content/{subdir}/{name:path}", dependencies=[Depends(require_auth)])
async def content_read(subdir: str, name: str, request: Request):
    """Read file content from a content subdirectory."""
    if subdir not in _CONTENT_SUBDIRS:
        _err(f"Invalid subdirectory: {subdir}", 404)

    if _is_path_traversal(name, DashboardPaths.CONTENT_DIR / subdir):
        _err("Path traversal detected", 400)

    file_path = DashboardPaths.CONTENT_DIR / subdir / name

    if not file_path.exists() or not file_path.is_file():
        _err("File not found", 404)

    if _is_xlsx(file_path):
        stat = file_path.stat()
        return _ok({
            "name": file_path.name,
            "size": stat.st_size,
            "modified": stat.st_mtime,
            "type": "xlsx",
        })

    stat = file_path.stat()
    max_size = 10 * 1024 * 1024  # 10 MB
    if stat.st_size > max_size:
        _err(f"File too large ({stat.st_size} bytes), max {max_size} bytes", 413)

    try:
        content = file_path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        _err("Cannot read file as text", 400)

    return _ok({"name": file_path.name, "content": content})


# ── Audit ─────────────────────────────────────────────────────────────────────


@app.get("/api/audit", dependencies=[Depends(require_auth)])
async def audit_list(request: Request, limit: int = Query(200, ge=1, le=1000)):
    """Return recent audit entries, ordered by their actual event time."""
    db_path = request.app.state.dashboard_db
    entries = audit_get_recent(db_path, limit)
    return _ok({"entries": entries})


# ── Static files ──────────────────────────────────────────────────────────────


# Mount static files (no auth)
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/dashboard")
async def dashboard_page():
    """Serve dashboard.html from static directory."""
    static_file = static_dir / "dashboard.html"
    if static_file.exists():
        return FileResponse(str(static_file))
    return JSONResponse(_ok({"message": "Dashboard UI not found"}))


@app.get("/")
async def root_redirect():
    """Redirect / to /dashboard."""
    return RedirectResponse(url="/dashboard")
