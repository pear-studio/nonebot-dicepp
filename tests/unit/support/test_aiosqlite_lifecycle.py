"""AiosqliteConnectionTracker 的无 SQLite 单元测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.support.aiosqlite_lifecycle import AiosqliteConnectionTracker


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False
        self._connection = object()

    async def close(self) -> None:
        self.closed = True
        self._connection = None


def _fake_aiosqlite_module() -> SimpleNamespace:
    return SimpleNamespace(Connection=FakeConnection, connect=lambda: FakeConnection())


@pytest.mark.asyncio
async def test_tracker_reports_only_connections_created_after_baseline():
    module = _fake_aiosqlite_module()
    tracker = AiosqliteConnectionTracker(module)
    tracker.install()
    try:
        module.connect()
        baseline = tracker.snapshot()
        leaked = module.connect()

        leaks = tracker.leaks_since(baseline)

        assert [tracked.connection for tracked in leaks] == [leaked]
        assert "test_aiosqlite_lifecycle.py" in leaks[0].origin

        cleanup_errors = await tracker.close_all(leaks)

        assert cleanup_errors == []
        assert leaked.closed is True
        assert tracker.leaks_since(baseline) == []
    finally:
        tracker.uninstall()


def test_tracker_ignores_connection_already_reclaimed_by_connect_failure():
    module = _fake_aiosqlite_module()
    tracker = AiosqliteConnectionTracker(module)
    tracker.install()
    try:
        baseline = tracker.snapshot()
        failed_connection = module.connect()
        failed_connection._connection = None

        assert tracker.leaks_since(baseline) == []
    finally:
        tracker.uninstall()
