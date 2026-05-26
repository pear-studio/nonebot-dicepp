"""Tests for SegmentDispatcher.

Covers: lazy creation, idle timeout, wake on new segment, delay_before=0,
flush, shutdown logs, worker_max_per_run cap, target_key naming.
"""

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from plugins.DicePP.module.persona.chat.segment_dispatcher import (
    SegmentDispatcher,
    SegmentItem,
)


async def _wait_sends(mock_port, count: int, timeout: float = 3.0) -> None:
    """轮询等待 send 完成指定次数，避免固定 sleep 在 CI 下 flaky。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while mock_port.send.await_count < count:
        if asyncio.get_event_loop().time() > deadline:
            break
        await asyncio.sleep(0.01)


@pytest.fixture
def mock_port():
    return AsyncMock()


@pytest.fixture
def dispatcher(mock_port):
    return SegmentDispatcher(
        message_port=mock_port,
        idle_seconds=1,
        max_per_run=3,
    )


class TestTargetKey:
    def test_group_key(self):
        assert SegmentDispatcher.target_key("u1", "g123") == "group:g123"

    def test_private_key(self):
        assert SegmentDispatcher.target_key("u1", "") == "user:u1"


class TestLazyCreation:
    @pytest.mark.asyncio
    async def test_first_segment_creates_worker(self, dispatcher, mock_port):
        dispatcher.notify("group:1", SegmentItem("hello", 0, "u1", "g1"))
        await asyncio.sleep(0.05)
        mock_port.send.assert_awaited_once_with("u1", "g1", "hello", skip_history_record=True)

    @pytest.mark.asyncio
    async def test_second_segment_reuses_worker(self, dispatcher, mock_port):
        dispatcher.notify("group:1", SegmentItem("a", 0, "u1", "g1"))
        dispatcher.notify("group:1", SegmentItem("b", 0, "u1", "g1"))
        await _wait_sends(mock_port, 2)
        assert mock_port.send.await_count == 2


class TestIdleTimeout:
    @pytest.mark.asyncio
    async def test_worker_exits_after_idle(self, dispatcher):
        dispatcher.notify("group:1", SegmentItem("hi", 0, "u1", "g1"))
        await asyncio.sleep(0.05)
        # Worker should still exist briefly after send
        assert "group:1" in dispatcher._workers
        # After idle timeout it should be gone
        await asyncio.sleep(1.2)
        assert "group:1" not in dispatcher._workers
        assert "group:1" not in dispatcher._queues


class TestDelayBefore:
    @pytest.mark.asyncio
    async def test_zero_delay_sends_immediately(self, dispatcher, mock_port):
        dispatcher.notify("group:1", SegmentItem("now", 0, "u1", "g1"))
        await asyncio.sleep(0.05)
        mock_port.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_positive_delay_waits(self, dispatcher, mock_port):
        dispatcher.notify("group:1", SegmentItem("later", 0.3, "u1", "g1"))
        await asyncio.sleep(0.05)
        # Not sent yet
        mock_port.send.assert_not_awaited()
        await asyncio.sleep(0.35)
        mock_port.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_new_segment_wakes_sleeping_worker(self, dispatcher, mock_port):
        dispatcher.notify("group:1", SegmentItem("a", 0.5, "u1", "g1"))
        await asyncio.sleep(0.05)
        # Enqueue second segment while first is sleeping
        dispatcher.notify("group:1", SegmentItem("b", 0, "u1", "g1"))
        await asyncio.sleep(0.6)
        # Both should be sent; order preserved
        calls = mock_port.send.await_args_list
        assert len(calls) == 2
        assert calls[0][0][2] == "a"
        assert calls[1][0][2] == "b"


class TestFlush:
    @pytest.mark.asyncio
    async def test_flush_drops_pending(self, dispatcher):
        # Directly populate queue to verify flush removes pending segments
        queue = asyncio.Queue()
        dispatcher._queues["group:1"] = queue
        queue.put_nowait(SegmentItem("a", 0, "u1", "g1"))
        queue.put_nowait(SegmentItem("b", 0, "u1", "g1"))
        await dispatcher.flush("group:1")
        assert queue.empty()

    @pytest.mark.asyncio
    async def test_flush_on_empty_queue_is_safe(self, dispatcher):
        await dispatcher.flush("group:1")


class TestShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_logs_pending(self, dispatcher):
        from unittest.mock import patch
        queue = asyncio.Queue()
        dispatcher._queues["group:1"] = queue
        queue.put_nowait(SegmentItem("a", 0, "u1", "g1"))
        queue.put_nowait(SegmentItem("b", 0, "u1", "g1"))
        with patch("plugins.DicePP.module.persona.chat.segment_dispatcher.logger") as mock_logger:
            await dispatcher.shutdown()
        mock_logger.warning.assert_called_once()
        assert "segments lost on shutdown" in mock_logger.warning.call_args[0][0]

    @pytest.mark.asyncio
    async def test_shutdown_no_warning_when_empty(self, dispatcher):
        from unittest.mock import patch
        dispatcher.notify("group:1", SegmentItem("a", 0, "u1", "g1"))
        await asyncio.sleep(0.1)
        with patch("plugins.DicePP.module.persona.chat.segment_dispatcher.logger") as mock_logger:
            await dispatcher.shutdown()
        assert not mock_logger.warning.called


class TestMaxPerRun:
    @pytest.mark.asyncio
    async def test_worker_rotates_after_cap(self, dispatcher, mock_port):
        # max_per_run = 3; worker 达到上限后退出，需新的 notify() 触发重建
        for i in range(3):
            dispatcher.notify("group:1", SegmentItem(str(i), 0, "u1", "g1"))
        await _wait_sends(mock_port, 3)
        calls = mock_port.send.await_args_list
        assert len(calls) == 3

        for i in range(3, 5):
            dispatcher.notify("group:1", SegmentItem(str(i), 0, "u1", "g1"))
        await _wait_sends(mock_port, 5)
        calls = mock_port.send.await_args_list
        assert len(calls) == 5
        contents = [c[0][2] for c in calls]
        assert contents == ["0", "1", "2", "3", "4"]


class TestSendFailure:
    @pytest.mark.asyncio
    async def test_failure_does_not_kill_worker(self, dispatcher, mock_port):
        mock_port.send.side_effect = [Exception("boom"), None]
        dispatcher.notify("group:1", SegmentItem("fail", 0, "u1", "g1"))
        dispatcher.notify("group:1", SegmentItem("ok", 0, "u1", "g1"))
        await _wait_sends(mock_port, 2)
        assert mock_port.send.await_count == 2


class TestWorkerExitRace:
    """B-260519: worker 退出竞态导致消息丢失的修复验证。"""

    @pytest.mark.asyncio
    async def test_max_per_run_remaining_segments_not_lost(
        self, dispatcher, mock_port
    ):
        """worker 因 max_per_run=3 退出后，queue 中剩余 2 条应由新 worker 接管。"""
        for i in range(5):
            dispatcher.notify("group:1", SegmentItem(str(i), 0, "u1", "g1"))
        await _wait_sends(mock_port, 5)
        calls = mock_port.send.await_args_list
        contents = [c[0][2] for c in calls]
        assert contents == ["0", "1", "2", "3", "4"], f"Got: {contents}"

    @pytest.mark.asyncio
    async def test_teardown_race_segment_not_lost(
        self, dispatcher, mock_port
    ):
        """模拟 notify 在 worker finally 清理期间注入 segment 的竞态时序。"""
        # 用 dict 子类在 workers.pop 后注入 segment，复现竞态窗口
        class _TrackingDict(dict):
            def pop(self, key, *args, **kwargs):
                result = super().pop(key, *args, **kwargs)
                dispatcher.notify(
                    key, SegmentItem("injected", 0, "u1", "g1")
                )
                return result

        dispatcher._workers = _TrackingDict(dispatcher._workers)

        dispatcher.notify("group:1", SegmentItem("first", 0, "u1", "g1"))
        await asyncio.sleep(1.3)  # 等 worker 超时退出

        # 等注入的 segment 被新 worker 处理完
        await asyncio.sleep(0.15)
        calls = mock_port.send.await_args_list
        contents = [c[0][2] for c in calls]
        assert "injected" in contents, f"Segment lost! Got: {contents}"

        # 清理，避免 event loop 关闭时残留 task 导致 warning
        await dispatcher.shutdown()
