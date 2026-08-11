"""Read-only inspection helpers for DicePP query databases.

This module deliberately understands only the fields DicePP uses for the
simple query format.  It is not a generic SQLite browser and never mutates the
database being inspected.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable

from dicepp_data import (
    QUERY_DATA_OPTIONAL_FIELDS,
    QUERY_DATA_REQUIRED_FIELDS,
    QUERY_REDIRECT_FIELDS,
)


class QueryAuditFormatError(ValueError):
    """The selected database cannot be inspected as a simple query database."""


_DATA_REQUIRED = QUERY_DATA_REQUIRED_FIELDS
_DATA_OPTIONAL = QUERY_DATA_OPTIONAL_FIELDS
_REDIRECT_REQUIRED = QUERY_REDIRECT_FIELDS
_SEARCH_SCOPES = {"all", "name", "english", "source", "content"}


def _connect(db_path: Path) -> sqlite3.Connection:
    uri = f"{db_path.resolve(strict=True).as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({_quote(table)})")}


def _require_columns(
    conn: sqlite3.Connection,
    table: str,
    required: Iterable[str],
    *,
    purpose: str,
) -> set[str]:
    if not _table_exists(conn, table):
        raise QueryAuditFormatError(
            f"数据库缺少 {table} 表，Dashboard 无法{purpose}。"
            f"请确认选中的是 DicePP 查询数据库。"
        )
    columns = _table_columns(conn, table)
    missing = [name for name in required if name not in columns]
    if missing:
        names = "、".join(f"“{name}”" for name in missing)
        raise QueryAuditFormatError(
            f"{table} 表缺少{names}列，Dashboard 无法{purpose}。"
            f"请补齐列名后重新加载；其他额外列不会影响检查。"
        )
    return columns


def _text_expr(column: str | None) -> str:
    if column is None:
        return "''"
    return f"COALESCE(CAST({_quote(column)} AS TEXT), '')"


def _like_pattern(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _data_select(columns: set[str]) -> str:
    optional = {name: name if name in columns else None for name in _DATA_OPTIONAL}
    return ", ".join(
        (
            "rowid AS rowid",
            f"{_text_expr('名称')} AS name",
            f"{_text_expr(optional['英文'])} AS english",
            f"{_text_expr(optional['来源'])} AS source",
            f"{_text_expr('内容')} AS content",
        )
    )


def _normalise_row(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    for field in ("name", "english", "source", "content", "target"):
        if field in record:
            record[field] = str(record[field] or "")
    if "content" in record:
        record["valid"] = bool(record["name"].strip() and record["content"].strip())
    return record


def _invalid_data_warning(record: dict[str, Any]) -> dict[str, Any] | None:
    missing: list[str] = []
    if not record["name"].strip():
        missing.append("名称")
    if not record["content"].strip():
        missing.append("内容")
    if not missing:
        return None

    rowid = int(record["rowid"])
    fields = "和".join(missing)
    if len(missing) == 2:
        effect = "机器人既无法匹配这个词条，也没有内容可以回复。"
    elif missing[0] == "名称":
        effect = "机器人无法通过任何关键词查到这条内容。"
    else:
        effect = "即使名称匹配，机器人也没有有效资料可以回复。"
    return {
        "id": f"invalid-data-{rowid}",
        "kind": "invalid_data",
        "view": "data",
        "rowids": [rowid],
        "title": f"数据第 {rowid} 行缺少{fields}",
        "message": f"这一行缺少{fields}，{effect}请补齐{fields}，或者删除这一行。",
    }


def inspect_query_database(db_path: Path) -> dict[str, Any]:
    """Return summary statistics and concrete, actionable warnings."""
    with closing(_connect(db_path)) as conn:
        data_columns = _require_columns(
            conn, "data", _DATA_REQUIRED, purpose="识别查询资料"
        )
        data_rows = [
            _normalise_row(row)
            for row in conn.execute(
                f"SELECT {_data_select(data_columns)} FROM data ORDER BY rowid"
            ).fetchall()
        ]

        redirects: list[dict[str, Any]] = []
        if _table_exists(conn, "redirect"):
            _require_columns(
                conn, "redirect", _REDIRECT_REQUIRED, purpose="识别重定向资料"
            )
            redirects = [
                _normalise_row(row)
                for row in conn.execute(
                    "SELECT rowid AS rowid, "
                    f"{_text_expr('名称')} AS name, "
                    f"{_text_expr('重定向')} AS target "
                    "FROM redirect ORDER BY rowid"
                ).fetchall()
            ]

    warnings: list[dict[str, Any]] = []
    valid_rows = [row for row in data_rows if row["valid"]]
    valid_names = {row["name"].strip() for row in valid_rows}

    for record in data_rows:
        warning = _invalid_data_warning(record)
        if warning:
            warnings.append(warning)
        for line_number, line in enumerate(record["content"].splitlines(), start=1):
            if not line.startswith("/"):
                continue
            rowid = int(record["rowid"])
            warnings.append({
                "id": f"outdated-content-{rowid}-{line_number}",
                "kind": "outdated_content",
                "view": "data",
                "rowids": [rowid],
                "title": f"数据第 {rowid} 行包含过时查询逻辑",
                "message": (
                    f"内容第 {line_number} 行以“/”开头，机器人不会再执行这条内嵌查询，"
                    "用户查询整个词条时会收到数据库需要规范的提示。"
                    "请使用“一键修复”转换成静态结果，或手工改写这一行。"
                ),
            })

    by_name_source: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in valid_rows:
        name = record["name"].strip()
        source = record["source"].strip()
        by_name_source[(name, source)].append(record)
        by_name[name].append(record)

    for (name, source), records in by_name_source.items():
        if len(records) <= 1:
            continue
        contents = {record["content"].strip() for record in records}
        has_conflict = len(contents) > 1
        rowids = [int(record["rowid"]) for record in records]
        source_label = f"来源“{source}”" if source else "未填写来源"
        warnings.append({
            "id": f"duplicate-content-{rowids[0]}",
            "kind": "duplicate_content",
            "view": "data",
            "rowids": rowids,
            "title": (
                f"“{name}”有多个不同内容"
                if has_conflict
                else f"“{name}”有重复数据"
            ),
            "message": (
                f"名称“{name}”在{source_label}下出现了 {len(records)} 行"
                f"{'不同内容' if has_conflict else '完全相同的数据'}。"
                f"机器人只会使用最前面的第 {rowids[0]} 行，后续内容会被隐藏。"
                + (
                    "请合并内容，或者修改后续行的名称或来源。"
                    if has_conflict
                    else "请删除重复行。"
                )
            ),
        })

    for name, records in by_name.items():
        sources = {record["source"].strip() for record in records}
        if len(sources) <= 1:
            continue
        rowids = [int(record["rowid"]) for record in records]
        warnings.append({
            "id": f"ambiguous-name-{rowids[0]}",
            "kind": "ambiguous_name",
            "view": "data",
            "rowids": rowids,
            "title": f"“{name}”查询时需要选择",
            "message": (
                f"名称“{name}”出现在 {len(sources)} 个不同来源中。"
                "用户精确查询这个名称时会看到选择列表。"
                "如果不希望用户选择，请调整名称，或者合并这些来源。"
            ),
        })

    redirect_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in redirects:
        rowid = int(record["rowid"])
        name = record["name"].strip()
        target = record["target"].strip()
        if name:
            redirect_by_name[name].append(record)

        if not name or not target:
            missing = "名称和目标" if not name and not target else ("名称" if not name else "目标")
            effect = "机器人无法使用这条重定向。"
            message = f"这一行缺少{missing}，{effect}请补齐{missing}，或者删除这一行。"
        elif target not in valid_names:
            effect = f"目标“{target}”在有效数据名称中不存在，机器人无法跳转到资料。"
            message = f"{effect}请把目标改成 data 表中已有的有效名称，或者删除这一行。"
        else:
            continue
        warnings.append({
            "id": f"invalid-redirect-{rowid}",
            "kind": "invalid_redirect",
            "view": "redirect",
            "rowids": [rowid],
            "title": f"重定向第 {rowid} 行无效",
            "message": message,
        })

    for name, records in redirect_by_name.items():
        if len(records) <= 1:
            continue
        rowids = [int(record["rowid"]) for record in records]
        warnings.append({
            "id": f"duplicate-redirect-{rowids[0]}",
            "kind": "duplicate_redirect",
            "view": "redirect",
            "rowids": rowids,
            "title": f"重定向名称“{name}”重复",
            "message": (
                f"名称“{name}”重复出现了 {len(records)} 次。"
                f"当前机器人只会使用最前面的第 {rowids[0]} 行，后续目标会被隐藏。"
                f"规范数据库后也只会保留第 {rowids[0]} 行。"
                "请只保留一个目标，或者修改重复名称。"
            ),
        })

    warnings.sort(key=lambda item: (item["view"], item["rowids"][0], item["kind"]))
    return {
        "stats": {
            "total_rows": len(data_rows),
            "valid_rows": len(valid_rows),
            "redirect_rows": len(redirects),
            "warning_count": len(warnings),
        },
        "warnings": warnings,
    }


def list_query_entries(
    db_path: Path,
    *,
    offset: int,
    limit: int,
    query: str | None,
    scope: str,
    rowids: list[int] | None,
) -> dict[str, Any]:
    """Return a semantic, server-filtered page from the data table."""
    if scope not in _SEARCH_SCOPES:
        raise QueryAuditFormatError(
            "搜索范围无效。可用范围为：全部、名称、英文、来源、内容。"
        )

    with closing(_connect(db_path)) as conn:
        columns = _require_columns(conn, "data", _DATA_REQUIRED, purpose="识别查询资料")
        select = _data_select(columns)
        expressions = {
            "name": _text_expr("名称"),
            "english": _text_expr("英文" if "英文" in columns else None),
            "source": _text_expr("来源" if "来源" in columns else None),
            "content": _text_expr("内容"),
        }
        clauses: list[str] = []
        params: list[Any] = []
        if rowids is not None:
            if not rowids:
                return {"records": [], "total": 0, "offset": offset, "limit": limit}
            placeholders = ",".join("?" for _ in rowids)
            clauses.append(f"rowid IN ({placeholders})")
            params.extend(rowids)
        term = (query or "").strip()
        if term:
            fields = expressions.values() if scope == "all" else (expressions[scope],)
            clauses.append(
                "(" + " OR ".join(f"{expr} LIKE ? ESCAPE '\\'" for expr in fields) + ")"
            )
            params.extend([_like_pattern(term)] * (4 if scope == "all" else 1))

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        total = int(conn.execute(f"SELECT COUNT(*) FROM data{where}", params).fetchone()[0])
        rows = conn.execute(
            f"SELECT {select} FROM data{where} ORDER BY rowid LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

    return {
        "records": [_normalise_row(row) for row in rows],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


def list_query_redirects(
    db_path: Path,
    *,
    offset: int,
    limit: int,
    query: str | None,
    rowids: list[int] | None,
) -> dict[str, Any]:
    """Return a semantic, server-filtered page from the optional redirect table."""
    with closing(_connect(db_path)) as conn:
        _require_columns(conn, "data", _DATA_REQUIRED, purpose="识别查询资料")
        if not _table_exists(conn, "redirect"):
            return {"records": [], "total": 0, "offset": offset, "limit": limit}
        _require_columns(
            conn, "redirect", _REDIRECT_REQUIRED, purpose="识别重定向资料"
        )

        valid_names = {
            str(row[0] or "").strip()
            for row in conn.execute(
                f"SELECT {_text_expr('名称')} FROM data "
                f"WHERE TRIM({_text_expr('名称')}) <> '' "
                f"AND TRIM({_text_expr('内容')}) <> ''"
            ).fetchall()
        }
        name_expr = _text_expr("名称")
        target_expr = _text_expr("重定向")
        clauses: list[str] = []
        params: list[Any] = []
        if rowids is not None:
            if not rowids:
                return {"records": [], "total": 0, "offset": offset, "limit": limit}
            placeholders = ",".join("?" for _ in rowids)
            clauses.append(f"rowid IN ({placeholders})")
            params.extend(rowids)
        term = (query or "").strip()
        if term:
            clauses.append(
                f"({name_expr} LIKE ? ESCAPE '\\' OR {target_expr} LIKE ? ESCAPE '\\')"
            )
            pattern = _like_pattern(term)
            params.extend((pattern, pattern))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        total = int(conn.execute(f"SELECT COUNT(*) FROM redirect{where}", params).fetchone()[0])
        rows = conn.execute(
            "SELECT rowid AS rowid, "
            f"{name_expr} AS name, {target_expr} AS target "
            f"FROM redirect{where} ORDER BY rowid LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

    records = [_normalise_row(row) for row in rows]
    for record in records:
        record["valid"] = bool(
            record["name"].strip()
            and record["target"].strip()
            and record["target"].strip() in valid_names
        )
    return {"records": records, "total": total, "offset": offset, "limit": limit}
