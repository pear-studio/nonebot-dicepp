from __future__ import annotations

import sqlite3
from pathlib import Path

from dicepp_data import INSTANCE_DB_ASSET, INSTANCE_SCHEMA, InstanceLayout
from .lifecycle import SchemaTarget, apply_schema_target, execute_many


INSTANCE_SCHEMA_SQL: list[str] = []


def create_instance_schema(conn: sqlite3.Connection) -> None:
    execute_many(conn, INSTANCE_SCHEMA_SQL)


INSTANCE_TARGET = SchemaTarget(
    name=INSTANCE_SCHEMA.name,
    latest_version=INSTANCE_SCHEMA.latest_version,
    create_latest_schema=create_instance_schema,
)


class DicePPDatabase:
    """Synchronous short-connection access to instance-level DicePP state."""

    def __init__(self, project_root: Path | InstanceLayout) -> None:
        layout = (
            project_root
            if isinstance(project_root, InstanceLayout)
            else InstanceLayout.from_root(project_root)
        )
        self.project_root = layout.root
        self.db_path = INSTANCE_DB_ASSET.resolve(layout)

    def ensure_schema(self) -> None:
        apply_schema_target(self.db_path, INSTANCE_TARGET)
