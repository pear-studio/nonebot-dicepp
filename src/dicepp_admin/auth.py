"""鉴权层：首次设置密码 + cookie session。

密码用 pbkdf2_hmac 加盐 hash，session token 用 secrets.token_urlsafe
生成并存入 sessions.json，cookie 仅带 token 不签名（token 本身不可猜）。
"""
import hashlib
import hmac
import json
import secrets
import time
from typing import Dict, Optional

from fastapi import HTTPException, Request

from dicepp_admin.config import AdminPaths, DEFAULT_USERNAME, SESSION_TTL_SECONDS

_PBKDF2_ITERATIONS = 200_000
_COOKIE_NAME = "dpp_admin_session"


# ─── 密码 hash ───────────────────────────────────────────────────────────

def _hash_password(password: str, salt: bytes) -> str:
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return derived.hex()


def _load_auth() -> Optional[Dict]:
    if not AdminPaths.AUTH_FILE.exists():
        return None
    try:
        return json.loads(AdminPaths.AUTH_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_auth(data: Dict) -> None:
    AdminPaths.AUTH_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def is_initialized() -> bool:
    auth = _load_auth()
    return bool(auth and auth.get("password_hash"))


def set_password(password: str, username: str = DEFAULT_USERNAME) -> None:
    if not password or len(password) < 6:
        raise HTTPException(status_code=400, detail={"message": "密码至少 6 位"})
    salt = secrets.token_bytes(16)
    _save_auth({
        "username": username,
        "salt": salt.hex(),
        "password_hash": _hash_password(password, salt),
        "created_at": int(time.time()),
    })


def verify_password(username: str, password: str) -> bool:
    auth = _load_auth()
    if not auth or auth.get("username") != username:
        return False
    salt = bytes.fromhex(auth["salt"])
    expected = auth["password_hash"]
    actual = _hash_password(password, salt)
    return hmac.compare_digest(expected, actual)


# ─── Session ─────────────────────────────────────────────────────────────

def _load_sessions() -> Dict[str, Dict]:
    if not AdminPaths.SESSION_FILE.exists():
        return {}
    try:
        return json.loads(AdminPaths.SESSION_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_sessions(sessions: Dict[str, Dict]) -> None:
    AdminPaths.SESSION_FILE.write_text(
        json.dumps(sessions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _prune_expired(sessions: Dict[str, Dict]) -> Dict[str, Dict]:
    now = int(time.time())
    return {tok: s for tok, s in sessions.items() if s.get("expires_at", 0) > now}


def create_session(username: str) -> str:
    sessions = _prune_expired(_load_sessions())
    token = secrets.token_urlsafe(32)
    sessions[token] = {
        "username": username,
        "created_at": int(time.time()),
        "expires_at": int(time.time()) + SESSION_TTL_SECONDS,
    }
    _save_sessions(sessions)
    return token


def revoke_session(token: str) -> None:
    sessions = _load_sessions()
    sessions.pop(token, None)
    _save_sessions(sessions)


def get_session(token: str) -> Optional[Dict]:
    sessions = _load_sessions()
    s = sessions.get(token)
    if not s:
        return None
    if s.get("expires_at", 0) < int(time.time()):
        sessions.pop(token, None)
        _save_sessions(sessions)
        return None
    return s


# ─── FastAPI 依赖 ────────────────────────────────────────────────────────

def require_auth(request: Request) -> Dict:
    token = request.cookies.get(_COOKIE_NAME, "")
    session = get_session(token) if token else None
    if not session:
        raise HTTPException(status_code=401, detail={"message": "未登录"})
    return session


def get_cookie_name() -> str:
    return _COOKIE_NAME
