"""Dashboard-local query database inspection and normalization."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from typing import Any

from dicepp_data import (
    QUERY_DATA_FIELDS,
    QUERY_DATA_OPTIONAL_FIELDS,
    QUERY_DATA_REQUIRED_FIELDS,
    QUERY_REDIRECT_FIELDS,
    normalize_query_database,
)


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({_quote(table)})")}


def _text_expr(name: str | None) -> str:
    return "''" if name is None else f"COALESCE(CAST({_quote(name)} AS TEXT), '')"


def read_query_rows(source: Path) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    with closing(sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "data" not in tables:
            raise ValueError("数据库缺少 data 表，无法规范查询数据")
        data_columns = _columns(connection, "data")
        missing = [name for name in QUERY_DATA_REQUIRED_FIELDS if name not in data_columns]
        if missing:
            raise ValueError(f"data 表缺少必需列：{'、'.join(missing)}")
        optional = {name: name if name in data_columns else None for name in (*QUERY_DATA_OPTIONAL_FIELDS, "分类", "标签")}
        rows = [
            dict(row)
            for row in connection.execute(
                "SELECT rowid, "
                f"{_text_expr('名称')} AS name, {_text_expr(optional['英文'])} AS english, "
                f"{_text_expr(optional['来源'])} AS source, {_text_expr(optional['分类'])} AS category, "
                f"{_text_expr(optional['标签'])} AS tag, {_text_expr('内容')} AS content FROM data ORDER BY rowid"
            ).fetchall()
        ]
        redirects: list[dict[str, object]] = []
        if "redirect" in tables:
            redirect_columns = _columns(connection, "redirect")
            missing = [name for name in QUERY_REDIRECT_FIELDS if name not in redirect_columns]
            if missing:
                raise ValueError(f"redirect 表缺少必需列：{'、'.join(missing)}")
            redirects = [
                dict(row)
                for row in connection.execute(
                    f"SELECT rowid, {_text_expr('名称')} AS alias, {_text_expr('重定向')} AS target FROM redirect ORDER BY rowid"
                ).fetchall()
            ]
    return rows, redirects


def normalization_report(source: Path):
    rows, redirects = read_query_rows(source)
    return normalize_query_database(rows, redirects)


def report_detail(report) -> dict[str, Any]:
    issues = [asdict(issue) for issue in report.issues[:2000]]
    return {
        "counts": asdict(report.counts),
        "impact_counts": {
            "deletion": sum(issue.impact == "deletion" for issue in report.issues),
            "behavior_change": sum(issue.impact == "behavior_change" for issue in report.issues),
        },
        "issues": issues,
        "issues_omitted": max(0, len(report.issues) - len(issues)),
    }


def write_normalized_database(source: Path, report) -> None:
    with closing(sqlite3.connect(source)) as connection:
        with connection:
            connection.execute("DROP TABLE IF EXISTS redirect")
            connection.execute("DROP TABLE IF EXISTS data")
            connection.execute("CREATE TABLE data (名称 TEXT, 英文 TEXT, 来源 TEXT, 内容 TEXT)")
            connection.execute("CREATE TABLE redirect (名称 TEXT, 重定向 TEXT)")
            connection.executemany(
                "INSERT INTO data VALUES (?, ?, ?, ?)",
                [(row.name, row.english, row.source, row.content) for row in report.rows],
            )
            connection.executemany(
                "INSERT INTO redirect VALUES (?, ?)",
                [(row.alias, row.target) for row in report.redirects],
            )


__all__ = ["normalization_report", "read_query_rows", "report_detail", "write_normalized_database"]
