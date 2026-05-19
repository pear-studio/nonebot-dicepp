"""
单元测试: LLMCallCoordinator
"""
import pytest
import asyncio
from unittest.mock import AsyncMock

from plugins.DicePP.module.persona.llm.coordinator import (
    LLMCallCoordinator,
    SubmitResult,
)


class TestLLMCallCoordinatorBasics:
    """测试基础行为"""

    @pytest.fixture
    def coordinator(self):
        return LLMCallCoordinator()

    @pytest.mark.asyncio
    async def test_single_successful_submit_returns_result(self, coordinator):
        call_fn = AsyncMock(return_value="hello")
        result = await coordinator.submit("user:1", "msg", call_fn)
        assert result.status == "success"
        assert result.value == "hello"
        assert call_fn.call_count == 1

    @pytest.mark.asyncio
    async def test_second_submit_while_executing_queues_and_merges(self, coordinator):
        barrier = asyncio.Event()
        execution_started = asyncio.Event()
        second_done = asyncio.Event()
        second_result = None

        async def slow_call_fn(messages):
            execution_started.set()
            await barrier.wait()
            return "first"

        async def second_submit():
            nonlocal second_result
            second_result = await coordinator.submit(
                "user:1", "msg2", AsyncMock(return_value="second")
            )
            second_done.set()

        first_task = asyncio.create_task(
            coordinator.submit("user:1", "msg1", slow_call_fn)
        )
        await execution_started.wait()

        second_task = asyncio.create_task(second_submit())
        await second_done.wait()

        # 第二个 submit 应该发现 executing=True，标记 buffered
        assert second_result.status == "buffered"
        assert coordinator._has_buffered.get("user:1") is True

        barrier.set()
        first_result = await first_task
        assert first_result.status == "success"
        assert first_result.value == "first"

    @pytest.mark.asyncio
    async def test_continue_on_buffered_true_loops_on_buffered(self, coordinator):
        """continue_on_buffered=True: 成功后发现 buffered，继续循环"""
        barrier = asyncio.Event()
        execution_started = asyncio.Event()
        second_done = asyncio.Event()
        second_result = None
        call_count = 0

        async def slow_call_fn(messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                execution_started.set()
                await barrier.wait()
            return f"result_{call_count}"

        async def second_submit():
            nonlocal second_result
            second_result = await coordinator.submit(
                "user:1", "msg2", AsyncMock(return_value="ignored")
            )
            second_done.set()

        first_task = asyncio.create_task(
            coordinator.submit(
                "user:1", "msg1", slow_call_fn, continue_on_buffered=True
            )
        )
        await execution_started.wait()

        second_task = asyncio.create_task(second_submit())
        await second_done.wait()

        # 第二个 submit 应该被 buffered
        assert second_result.status == "buffered"
        assert coordinator._has_buffered.get("user:1") is True

        barrier.set()
        result = await first_task
        assert call_count == 2
        assert result.status == "success"
        assert result.value == "result_2"

    @pytest.mark.asyncio
    async def test_continue_on_buffered_false_exits_on_success(self, coordinator):
        """continue_on_buffered=False: 成功后直接退出，buffered 在 finally 中清理"""
        barrier = asyncio.Event()
        execution_started = asyncio.Event()

        async def slow_call_fn(messages):
            execution_started.set()
            await barrier.wait()
            return "result_1"

        first_task = asyncio.create_task(
            coordinator.submit(
                "user:1", "msg1", slow_call_fn, continue_on_buffered=False
            )
        )
        await execution_started.wait()

        second_task = asyncio.create_task(
            coordinator.submit(
                "user:1", "msg2", AsyncMock(return_value="ignored")
            )
        )
        second_result = await second_task
        assert second_result.status == "buffered"
        assert coordinator._has_buffered.get("user:1") is True

        barrier.set()
        result = await first_task
        assert result.status == "success"
        assert result.value == "result_1"
        # finally 统一清理 _has_buffered
        assert coordinator._has_buffered.get("user:1") is None

    @pytest.mark.asyncio
    async def test_three_consecutive_failures_trigger_on_exhausted(self, coordinator):
        """连续失败 3 次触发 on_exhausted（需 buffered 消息驱动重试）"""
        on_exhausted_called = False
        started1 = asyncio.Event()
        started2 = asyncio.Event()
        started3 = asyncio.Event()
        barrier1 = asyncio.Event()
        barrier2 = asyncio.Event()
        barrier3 = asyncio.Event()

        async def on_exhausted_fn(last_exception=None):
            nonlocal on_exhausted_called
            on_exhausted_called = True

        call_count = 0

        async def failing_call_fn(messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                started1.set()
                await barrier1.wait()
            elif call_count == 2:
                started2.set()
                await barrier2.wait()
            elif call_count == 3:
                started3.set()
                await barrier3.wait()
            raise Exception("fail")

        first_task = asyncio.create_task(
            coordinator.submit(
                "user:1", "msg1", failing_call_fn, on_exhausted=on_exhausted_fn
            )
        )
        await started1.wait()

        second_task = asyncio.create_task(
            coordinator.submit(
                "user:1", "msg2", AsyncMock(return_value="ignored")
            )
        )
        await second_task

        barrier1.set()
        await started2.wait()

        third_task = asyncio.create_task(
            coordinator.submit(
                "user:1", "msg3", AsyncMock(return_value="ignored")
            )
        )
        await third_task

        barrier2.set()
        await started3.wait()

        barrier3.set()
        result = await first_task
        assert result.status == "failed"
        assert result.value is None
        assert call_count == 3
        assert on_exhausted_called is True

    @pytest.mark.asyncio
    async def test_success_resets_failure_counter(self, coordinator):
        started1 = asyncio.Event()
        started2 = asyncio.Event()
        barrier1 = asyncio.Event()
        barrier2 = asyncio.Event()
        call_count = 0

        async def inner_call_fn(messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                started1.set()
                await barrier1.wait()
                raise Exception("fail")
            if call_count == 2:
                started2.set()
                await barrier2.wait()
                raise Exception("fail")
            return "success"

        first_task = asyncio.create_task(
            coordinator.submit("user:1", "msg1", inner_call_fn)
        )
        await started1.wait()

        second_task = asyncio.create_task(
            coordinator.submit(
                "user:1", "msg2", AsyncMock(return_value="ignored")
            )
        )
        await second_task

        barrier1.set()
        await started2.wait()

        third_task = asyncio.create_task(
            coordinator.submit(
                "user:1", "msg3", AsyncMock(return_value="ignored")
            )
        )
        await third_task

        barrier2.set()
        result = await first_task
        assert call_count == 3
        assert result.status == "success"
        assert result.value == "success"

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self, coordinator):
        call_fn = AsyncMock(side_effect=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):
            await coordinator.submit("user:1", "msg", call_fn)
        # executing 标记已清理
        assert coordinator._executing.get("user:1") is None

    @pytest.mark.asyncio
    async def test_cancelled_error_clears_buffered(self, coordinator):
        """取消后 finally 统一清理 buffered 和 executing"""
        call_fn = AsyncMock(side_effect=asyncio.CancelledError())
        coordinator._has_buffered["user:1"] = True
        with pytest.raises(asyncio.CancelledError):
            await coordinator.submit("user:1", "msg", call_fn)
        # finally 统一清理，防止内存泄漏
        assert coordinator._has_buffered.get("user:1") is None
        assert coordinator._executing.get("user:1") is None

    @pytest.mark.asyncio
    async def test_share_success_then_chat_starts_fresh(self, coordinator):
        """share 成功后 finally 清理状态；下次 chat submit 启动新循环"""
        barrier = asyncio.Event()
        execution_started = asyncio.Event()

        async def slow_call_fn(messages):
            execution_started.set()
            await barrier.wait()
            return "share_msg"

        first_task = asyncio.create_task(
            coordinator.submit(
                "user:1", None, slow_call_fn, continue_on_buffered=False
            )
        )
        await execution_started.wait()

        second_task = asyncio.create_task(
            coordinator.submit(
                "user:1", None, AsyncMock(return_value="ignored")
            )
        )
        second_result = await second_task
        assert second_result.status == "buffered"
        assert coordinator._has_buffered.get("user:1") is True

        barrier.set()
        result = await first_task
        assert result.status == "success"
        assert result.value == "share_msg"
        # finally 统一清理 _has_buffered
        assert coordinator._has_buffered.get("user:1") is None

        # 下一次 chat submit 启动新循环，正常执行
        async def chat_call_fn(messages):
            return "chat_reply"

        result2 = await coordinator.submit(
            "user:1", "msg", chat_call_fn, continue_on_buffered=True
        )
        assert result2.status == "success"
        assert result2.value == "chat_reply"
        assert coordinator._has_buffered.get("user:1") is None

    @pytest.mark.asyncio
    async def test_multiple_concurrent_pending_submits_coalesced(self, coordinator):
        """多个并发的 pending submit 只应产生一个 buffered 标记"""
        barrier = asyncio.Event()
        execution_started = asyncio.Event()

        async def slow_call_fn(messages):
            execution_started.set()
            await barrier.wait()
            return "first"

        first_task = asyncio.create_task(
            coordinator.submit("user:1", "msg1", slow_call_fn)
        )
        await execution_started.wait()

        results = []
        for i in range(5):
            results.append(
                await coordinator.submit(
                    "user:1", f"msg_{i}", AsyncMock(return_value="x")
                )
            )

        assert all(r.status == "buffered" for r in results)
        # 多个并发 submit 的 message 应被收集到 pending_messages
        assert len(coordinator._pending_messages.get("user:1", [])) == 5

        barrier.set()
        await first_task
