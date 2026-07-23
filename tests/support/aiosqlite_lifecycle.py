"""测试期间追踪 aiosqlite 连接的显式关闭。"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
import traceback
from typing import Any


@dataclass(frozen=True)
class TrackedConnection:
    """尚未显式关闭的测试连接及其创建位置。"""

    connection: Any
    origin: str


class AiosqliteConnectionTracker:
    """包装 ``aiosqlite.connect`` / ``Connection.close`` 追踪资源所有权。

    连接在测试结束前必须调用 ``close``；仅靠对象析构会让后台线程在随后某条
    测试中才暴露异常，无法归因。
    """

    def __init__(self, aiosqlite_module: Any) -> None:
        self._aiosqlite = aiosqlite_module
        self._connections: dict[int, TrackedConnection] = {}
        self._original_connect: Any = None
        self._original_close: Any = None

    def install(self) -> None:
        """安装追踪包装；同一 tracker 不可重复安装。"""
        if self._original_connect is not None:
            raise RuntimeError("aiosqlite 连接追踪器已安装")

        self._original_connect = self._aiosqlite.connect
        self._original_close = self._aiosqlite.Connection.close

        def tracked_connect(*args: Any, **kwargs: Any) -> Any:
            connection = self._original_connect(*args, **kwargs)
            self._connections[id(connection)] = TrackedConnection(
                connection=connection,
                origin=self._test_callsite(),
            )
            return connection

        async def tracked_close(connection: Any, *args: Any, **kwargs: Any) -> Any:
            result = await self._original_close(connection, *args, **kwargs)
            self._connections.pop(id(connection), None)
            return result

        self._aiosqlite.connect = tracked_connect
        self._aiosqlite.Connection.close = tracked_close

    def uninstall(self) -> None:
        """恢复原始 aiosqlite 方法。"""
        if self._original_connect is None:
            return

        self._aiosqlite.connect = self._original_connect
        self._aiosqlite.Connection.close = self._original_close
        self._original_connect = None
        self._original_close = None

    def snapshot(self) -> frozenset[int]:
        """返回当前已打开连接的稳定快照。"""
        self._discard_closed_connections()
        return frozenset(self._connections)

    def leaks_since(self, baseline: Collection[int]) -> list[TrackedConnection]:
        """返回在基线之后创建且尚未关闭的连接。"""
        self._discard_closed_connections()
        return [
            tracked
            for connection_id, tracked in self._connections.items()
            if connection_id not in baseline
        ]

    async def close_all(self, connections: Collection[TrackedConnection]) -> list[BaseException]:
        """尽力关闭泄漏连接，返回关闭时额外发生的异常。"""
        errors: list[BaseException] = []
        for tracked in connections:
            if id(tracked.connection) not in self._connections:
                continue
            try:
                await tracked.connection.close()
            except BaseException as error:
                errors.append(error)
        return errors

    def _discard_closed_connections(self) -> None:
        """移除 aiosqlite 自己已回收的失败连接。"""
        for connection_id, tracked in list(self._connections.items()):
            if getattr(tracked.connection, "_connection", object()) is None:
                self._connections.pop(connection_id, None)

    @staticmethod
    def format_leaks(connections: Collection[TrackedConnection]) -> str:
        """构造能直接定位创建点的 pytest 失败信息。"""
        locations = "\n".join(f"- {tracked.origin}" for tracked in connections)
        return (
            "检测到未显式关闭的 aiosqlite 连接。"
            "请使用 async with、fixture teardown 或 finally 调用 close()：\n"
            f"{locations}"
        )

    @staticmethod
    def _test_callsite() -> str:
        for frame in reversed(traceback.extract_stack(limit=20)[:-1]):
            filename = frame.filename.replace("\\", "/")
            if filename.endswith("/tests/support/aiosqlite_lifecycle.py"):
                continue
            if "/tests/" in filename:
                return f"{filename}:{frame.lineno}"
        frame = traceback.extract_stack(limit=2)[0]
        return f"{frame.filename}:{frame.lineno}"
