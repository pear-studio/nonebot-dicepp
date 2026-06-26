"""
Persona character card API routes for the DicePP Dashboard.

Mounts under /api/persona/ with 5 endpoints:
  GET    /characters          — list characters (enriched)
  GET    /characters/{name}   — get structured character data
  POST   /characters/{name}/save — save character data
  PUT    /characters/{name}   — create new character
  DELETE /characters/{name}   — delete character
"""

import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Request

from ._helpers import _err, _is_path_traversal, _ok
from .audit import log as audit_log
from .auth import require_auth
from .config import DashboardPaths
from .persona import (
    create_character,
    delete_character,
    get_character,
    list_characters,
    save_character,
)

logger = logging.getLogger("dashboard.persona_routes")

router = APIRouter(prefix="/api/persona", dependencies=[Depends(require_auth)])

# ── Validation ─────────────────────────────────────────────────────────────────

_CHAR_NAME_PATTERN = re.compile(r"^[^\x00/\\]{1,128}$")


def _validate_character_name(name: str) -> None:
    """Validate character name: 1-128 chars, no path separators or null bytes."""
    if not name or not _CHAR_NAME_PATTERN.match(name):
        _err("角色名格式无效：1~128 位非空字符，禁止路径分隔符和空字节", 400)


# ── Routes ─────────────────────────────────────────────────────────────────────


@router.get("/characters")
async def persona_characters(request: Request):
    """List character directories with enriched metadata."""
    characters = list_characters()
    return _ok({"characters": characters})


@router.get("/characters/{name}")
async def persona_character_get(name: str, request: Request):
    """Get a single character card as structured JSON."""
    _validate_character_name(name)
    if _is_path_traversal(name, DashboardPaths.CONTENT_DIR / "characters"):
        _err("Path traversal detected", 400)

    try:
        data = get_character(name)
    except FileNotFoundError:
        _err(f"Character not found: {name}", 404)
    except Exception:
        logger.exception("Failed to read character: %s", name)
        _err("Cannot read character.yaml", 400)

    return _ok(data)


@router.post("/characters/{name}/save")
async def persona_character_save(name: str, request: Request):
    """Save (create or update) a character card."""
    _validate_character_name(name)
    if _is_path_traversal(name, DashboardPaths.CONTENT_DIR / "characters"):
        _err("Path traversal detected", 400)

    if name == "default":
        _err("默认角色卡不可编辑", 403)

    body = await request.json()
    if not isinstance(body, dict):
        _err("Request body must be a JSON object")
    character_data = body.get("character", body)
    if not isinstance(character_data, dict):
        _err("Request body must be a JSON object with character data")

    try:
        save_character(name, character_data)
    except Exception:
        logger.exception("Failed to save character: %s", name)
        _err("保存角色卡失败", 500)

    db_path = getattr(request.app.state, "dashboard_db", "")
    audit_log(db_path, "persona.character.save", name, "",
              ip=request.client.host if request.client else "")

    return _ok({"saved": True})


@router.put("/characters/{name}")
async def persona_character_create(name: str, request: Request):
    """Create a new character card."""
    _validate_character_name(name)
    if _is_path_traversal(name, DashboardPaths.CONTENT_DIR / "characters"):
        _err("Path traversal detected", 400)

    if name == "default":
        _err("不能创建名为 default 的角色卡", 403)

    body = await request.json()
    if not isinstance(body, dict):
        _err("Request body must be a JSON object")
    display_name = str(body.get("display_name", name))

    try:
        create_character(name, display_name)
    except FileExistsError:
        _err(f"角色卡已存在: {name}", 409)
    except Exception:
        logger.exception("Failed to create character: %s", name)
        _err("创建角色卡失败", 500)

    db_path = getattr(request.app.state, "dashboard_db", "")
    audit_log(db_path, "persona.character.create", name, "",
              ip=request.client.host if request.client else "")

    return _ok({"name": name})


@router.delete("/characters/{name}")
async def persona_character_delete(name: str, request: Request):
    """Delete a character card."""
    _validate_character_name(name)
    if _is_path_traversal(name, DashboardPaths.CONTENT_DIR / "characters"):
        _err("Path traversal detected", 400)

    if name == "default":
        _err("默认角色卡不可删除", 403)

    try:
        delete_character(name)
    except FileNotFoundError:
        _err(f"Character not found: {name}", 404)
    except Exception:
        logger.exception("Failed to delete character: %s", name)
        _err("删除角色卡失败", 500)

    db_path = getattr(request.app.state, "dashboard_db", "")
    audit_log(db_path, "persona.character.delete", name, "",
              ip=request.client.host if request.client else "")

    return _ok({"deleted": True})
