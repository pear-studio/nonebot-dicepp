"""Shared contracts for DicePP query database files.

The module intentionally uses only the standard library so Bot, Dashboard and
Dashboard and Bot code can agree on the same logical fields and enablement state.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile


QUERY_DATA_REQUIRED_FIELDS = ("名称", "内容")
QUERY_DATA_OPTIONAL_FIELDS = ("英文", "来源")
QUERY_DATA_FIELDS = (*QUERY_DATA_REQUIRED_FIELDS[:1], *QUERY_DATA_OPTIONAL_FIELDS, "内容")
QUERY_REDIRECT_FIELDS = ("名称", "重定向")
QUERY_DATABASE_STATE_FILE = ".dicepp-query-databases.json"


class QueryDatabaseStateError(ValueError):
    """The query database state file is malformed."""


@dataclass(frozen=True)
class QueryDatabaseState:
    """Persistent per-directory state; unlisted databases are enabled."""

    disabled: frozenset[str] = frozenset()

    def is_enabled(self, database: str) -> bool:
        return database not in self.disabled


def is_query_database_name(value: object) -> bool:
    """Return whether *value* is a safe extension-free database name."""
    if not isinstance(value, str) or not value or len(value) > 128:
        return False
    if value in {".", ".."} or value.endswith(".db"):
        return False
    return Path(value).name == value and "/" not in value and "\\" not in value


def query_database_state_path(directory: Path) -> Path:
    return directory / QUERY_DATABASE_STATE_FILE


def load_query_database_state(directory: Path) -> QueryDatabaseState:
    """Load state, treating a missing file as all databases enabled."""
    path = query_database_state_path(directory)
    if not path.exists():
        return QueryDatabaseState()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QueryDatabaseStateError(f"查询数据库启停状态读取失败：{exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise QueryDatabaseStateError("查询数据库启停状态缺少受支持的 version=1")
    disabled = payload.get("disabled", [])
    if not isinstance(disabled, list) or any(
        not is_query_database_name(name) for name in disabled
    ):
        raise QueryDatabaseStateError("查询数据库启停状态中的 disabled 必须是安全的数据库名称列表")
    return QueryDatabaseState(frozenset(disabled))


def save_query_database_state(directory: Path, state: QueryDatabaseState) -> None:
    """Atomically persist state without touching Bot configuration files."""
    directory.mkdir(parents=True, exist_ok=True)
    path = query_database_state_path(directory)
    payload = {
        "version": 1,
        "disabled": sorted(state.disabled, key=str.casefold),
    }
    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=directory,
        prefix=f"{QUERY_DATABASE_STATE_FILE}.",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def set_query_database_enabled(directory: Path, database: str, enabled: bool) -> QueryDatabaseState:
    """Update one database while preserving all other enablement choices."""
    if not is_query_database_name(database):
        raise QueryDatabaseStateError("数据库名称无效")
    current = load_query_database_state(directory)
    disabled = set(current.disabled)
    if enabled:
        disabled.discard(database)
    else:
        disabled.add(database)
    updated = QueryDatabaseState(frozenset(disabled))
    save_query_database_state(directory, updated)
    return updated
