import hashlib
import os
import secrets
import sqlite3
import time
from typing import Optional

from fastapi import Request, HTTPException


# ── Password hashing (pbkdf2) ─────────────────────────────────────────────────


def hash_password(password: str) -> tuple[str, str]:
    """Hash a password with pbkdf2_hmac sha256, 200k iterations, 16-byte salt.

    Returns (hash_hex, salt_hex).
    """
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200000)
    return h.hex(), salt.hex()


def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    """Verify a password against a stored hash and salt."""
    salt_bytes = bytes.fromhex(salt)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, 200000)
    return h.hex() == stored_hash


def validate_password(password: str) -> Optional[str]:
    """Validate password strength. Returns error message or None."""
    if not password or len(password) < 6:
        return "密码至少6位"
    return None


# ── Session management (dashboard.db) ─────────────────────────────────────────


def _ensure_sessions_table(db_path: str) -> None:
    """Ensure the sessions table exists."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                expires_at TEXT NOT NULL
            )"""
        )
        conn.commit()
    finally:
        conn.close()


def create_session(db_path: str) -> str:
    """Create a new session token with 7-day expiry. Returns the token."""
    _ensure_sessions_table(db_path)
    token = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + 7 * 86400

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO sessions (token, expires_at) VALUES (?, ?)",
            (token, str(expires_at)),
        )
        conn.commit()
    finally:
        conn.close()

    return token


def get_session(db_path: str, token: str) -> Optional[dict]:
    """Validate a session token. Prunes expired sessions. Returns session dict or None."""
    _ensure_sessions_table(db_path)

    conn = sqlite3.connect(db_path)
    try:
        # Prune expired sessions
        now = int(time.time())
        conn.execute("DELETE FROM sessions WHERE CAST(expires_at AS INTEGER) < ?", (now,))
        conn.commit()

        cursor = conn.execute(
            "SELECT token, expires_at FROM sessions WHERE token = ?",
            (token,),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        expires_at = int(row[1])
        if expires_at < now:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            conn.commit()
            return None

        return {"token": row[0], "expires_at": row[1]}
    finally:
        conn.close()


def revoke_session(db_path: str, token: str) -> None:
    """Delete a session token."""
    _ensure_sessions_table(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
    finally:
        conn.close()


def is_initialized(db_path: str) -> bool:
    """Check if auth table has a row (password has been set)."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("SELECT COUNT(*) FROM auth WHERE id = 1")
        row = cursor.fetchone()
        return row is not None and row[0] > 0
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


def set_password_db(db_path: str, password: str) -> None:
    """Hash and store password in auth table."""
    pwd_hash, salt = hash_password(password)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS auth (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL
            )"""
        )
        conn.execute(
            "INSERT OR REPLACE INTO auth (id, password_hash, salt) VALUES (1, ?, ?)",
            (pwd_hash, salt),
        )
        conn.commit()
    finally:
        conn.close()


def verify_password_db(db_path: str, password: str) -> bool:
    """Verify password against stored hash in auth table."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("SELECT password_hash, salt FROM auth WHERE id = 1")
        row = cursor.fetchone()
        if row is None:
            return False
        return verify_password(password, row[0], row[1])
    finally:
        conn.close()


# ── FastAPI dependency ────────────────────────────────────────────────────────


async def require_auth(request: Request) -> None:
    """FastAPI dependency: validate session cookie, raise 401 if invalid."""
    db_path = request.app.state.dashboard_db
    token = request.cookies.get("session")

    if not token:
        raise HTTPException(status_code=401, detail={"ok": False, "message": "Not authenticated"})

    session = get_session(db_path, token)
    if session is None:
        raise HTTPException(status_code=401, detail={"ok": False, "message": "Session expired or invalid"})

    # Store operator info for audit logging
    request.state.operator = "admin"
