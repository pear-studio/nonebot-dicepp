import aiosqlite
from datetime import datetime
from typing import Any, Dict, Generic, List, Optional, Type, TypeVar, Sequence

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class Repository(Generic[T]):
    def __init__(
        self,
        db: aiosqlite.Connection,
        model_class: Type[T],
        table_name: str,
        key_fields: List[str],
    ):
        self._db = db
        self._model_class = model_class
        self._table_name = table_name
        self._key_fields = key_fields

    async def _ensure_table(self) -> None:
        key_cols = ", ".join([f"{k} TEXT" for k in self._key_fields])
        await self._db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._table_name} (
                {key_cols},
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY ({", ".join(self._key_fields)})
            )
            """
        )
        await self._db.commit()

    async def get(self, *keys: str) -> Optional[T]:
        if len(keys) != len(self._key_fields):
            raise ValueError(
                f"Expected {len(self._key_fields)} keys, got {len(keys)}"
            )

        where_clause = " AND ".join([f"{field} = ?" for field in self._key_fields])
        cursor = await self._db.execute(
            f"SELECT data FROM {self._table_name} WHERE {where_clause}",
            keys,
        )
        row = await cursor.fetchone()

        if row is None:
            return None

        return self._model_class.model_validate_json(row[0])

    async def save(self, item: T) -> None:
        key_values = [getattr(item, field) for field in self._key_fields]
        data_json = item.model_dump_json()
        updated_at = datetime.now().isoformat()

        columns = ", ".join(self._key_fields)
        placeholders = ", ".join(["?"] * len(self._key_fields))
        await self._db.execute(
            f"""
            INSERT INTO {self._table_name} ({columns}, data, updated_at)
            VALUES ({placeholders}, ?, ?)
            ON CONFLICT({columns}) DO UPDATE SET
                data = excluded.data,
                updated_at = excluded.updated_at
            """,
            (*key_values, data_json, updated_at),
        )
        await self._db.commit()

    async def delete(self, *keys: str) -> bool:
        if len(keys) != len(self._key_fields):
            raise ValueError(
                f"Expected {len(self._key_fields)} keys, got {len(keys)}"
            )

        where_clause = " AND ".join([f"{field} = ?" for field in self._key_fields])
        cursor = await self._db.execute(
            f"DELETE FROM {self._table_name} WHERE {where_clause}",
            keys,
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def list_all(self) -> List[T]:
        cursor = await self._db.execute(
            f"SELECT data FROM {self._table_name}"
        )
        rows = await cursor.fetchall()
        return [self._model_class.model_validate_json(row[0]) for row in rows]

    async def get_keys(self, user_id: str, group_id: str) -> List[str]:
        cursor = await self._db.execute(
            f"SELECT DISTINCT name FROM {self._table_name} WHERE user_id = ? AND group_id = ?",
            (user_id, group_id),
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def list_by(self, **filters: str) -> List[T]:
        if not filters:
            return await self.list_all()

        where_clauses = []
        params = []
        for field, value in filters.items():
            where_clauses.append(f"{field} = ?")
            params.append(value)

        where_clause = " AND ".join(where_clauses)
        cursor = await self._db.execute(
            f"SELECT data FROM {self._table_name} WHERE {where_clause}",
            params,
        )
        rows = await cursor.fetchall()
        return [self._model_class.model_validate_json(row[0]) for row in rows]

    async def list_key_values_by(self, key_field: str, **filters: str) -> List[str]:
        """
        列出满足过滤条件的所有记录中，指定 key 字段的值列表。
        常用于枚举群内用户的角色卡 ID 等场景。

        例如：list_key_values_by("user_id", group_id="12345")
        """
        where_clauses = []
        params: List[Any] = []
        for field, value in filters.items():
            where_clauses.append(f"{field} = ?")
            params.append(value)

        if where_clauses:
            where_clause = "WHERE " + " AND ".join(where_clauses)
        else:
            where_clause = ""

        cursor = await self._db.execute(
            f"SELECT DISTINCT {key_field} FROM {self._table_name} {where_clause}",
            params,
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]
