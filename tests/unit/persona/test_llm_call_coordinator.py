"""
单元测试: LLMCallCoordinator
"""
import pytest
import asyncio
from unittest.mock import AsyncMock

from plugins.DicePP.module.persona.proactive.llm_call_coordinator import (
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

    @pytest.mark.asyncio
    async def test_on_exhausted_exception_does_not_crash(self, coordinator):
        async def bad_on_exhausted(last_exception=None):
            raise RuntimeError("boom")

        call_count = 0

        async def inner_failing_call_fn(messages):
            nonlocal call_count
            call_count += 1
            # 直接设置内部状态以精确控制 _run_loop 的重试路径，
            # 而非测试并发 buffered 行为本身。
            if call_count <= 2:
                coordinator._has_buffered["user:1"] = True
            raise Exception("fail")

        call_fn = AsyncMock(side_effect=inner_failing_call_fn)
        # 不应抛出异常
        result = await coordinator.submit(
            "user:1", "msg", call_fn, on_exhausted=bad_on_exhausted
        )
        assert result.status == "failed"
        assert result.value is None
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_max_iterations_prevents_infinite_loop(self, coordinator):
        """MAX_ITERATIONS=5 防止用户刷屏导致无限循环"""
        started_events = [asyncio.Event() for _ in range(5)]
        barriers = [asyncio.Event() for _ in range(4)]
        call_count = 0

        async def call_fn(messages):
            nonlocal call_count
            call_count += 1
            idx = call_count - 1
            started_events[idx].set()
            if idx < 4:
                await barriers[idx].wait()
            return f"result_{call_count}"

        first_task = asyncio.create_task(
            coordinator.submit(
                "user:1", "msg", call_fn, continue_on_buffered=True
            )
        )
        await started_events[0].wait()

        # 每次 call_fn 开始后，提交新的 submit 来设置 buffered
        for i in range(4):
            next_task = asyncio.create_task(
                coordinator.submit(
                    "user:1", f"msg_{i}", AsyncMock(return_value="ignored")
                )
            )
            await next_task
            assert coordinator._has_buffered.get("user:1") is True
            barriers[i].set()
            if i < 3:
                await started_events[i + 1].wait()

        result = await first_task
        assert call_count == 5
        assert result.status == "success"
        assert result.value == "result_5"

    @pytest.mark.asyncio
    async def test_pending_submit_during_last_attempt_dropped(self, coordinator):
        """最后一次尝试期间到达的 pending submit，标记 buffered 但不会被处理"""
        started1 = asyncio.Event()
        started2 = asyncio.Event()
        started3 = asyncio.Event()
        barrier1 = asyncio.Event()
        barrier2 = asyncio.Event()
        barrier3 = asyncio.Event()
        call_count = 0

        async def inner_failing_call_fn(messages):
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

        on_exhausted_called = False

        async def on_exhausted_fn(last_exception=None):
            nonlocal on_exhausted_called
            on_exhausted_called = True

        first_task = asyncio.create_task(
            coordinator.submit(
                "user:1", "msg1", inner_failing_call_fn, on_exhausted=on_exhausted_fn
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

        # 第三次 call_fn 已经开始，此时再提交第四个 submit
        # 它会被 buffered，但由于 failures 即将达到 max_failures，不会被处理
        fourth_task = asyncio.create_task(
            coordinator.submit(
                "user:1", "msg4", AsyncMock(return_value="ignored")
            )
        )
        fourth_result = await fourth_task
        assert fourth_result.status == "buffered"
        assert coordinator._has_buffered.get("user:1") is True

        barrier3.set()
        result = await first_task
        assert result.status == "failed"
        assert result.value is None
        assert call_count == 3
        assert on_exhausted_called is True
        # buffered 在最后一次失败后被 pop（此时 has_buffered=True 但 failures >= max_failures，不继续）
        assert coordinator._has_buffered.get("user:1") is None

    @pytest.mark.asyncio
    async def test_on_exhausted_only_when_never_succeeded(self, coordinator):
        """had_success=True 时即使最终失败也不触发 on_exhausted"""
        started1 = asyncio.Event()
        barrier1 = asyncio.Event()
        call_count = 0
        on_exhausted_called = False

        async def call_fn(messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                started1.set()
                await barrier1.wait()
                return "first_success"
            raise Exception("fail")

        async def on_exhausted_fn():
            nonlocal on_exhausted_called
            on_exhausted_called = True

        first_task = asyncio.create_task(
            coordinator.submit(
                "user:1", "msg1", call_fn, continue_on_buffered=True, on_exhausted=on_exhausted_fn
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
        result = await first_task
        assert call_count == 2
        assert result.status == "failed"
        assert result.value is None
        assert on_exhausted_called is False  # 因为 had_success=True

    @pytest.mark.asyncio
    async def test_different_targets_run_concurrently(self, coordinator):
        """不同 target_key 之间应并行执行"""
        order = []
        barrier1 = asyncio.Event()
        barrier2 = asyncio.Event()
        started1 = asyncio.Event()
        started2 = asyncio.Event()

        async def slow1(messages):
            order.append("start_1")
            started1.set()
            await barrier1.wait()
            order.append("end_1")
            return "r1"

        async def slow2(messages):
            order.append("start_2")
            started2.set()
            await barrier2.wait()
            order.append("end_2")
            return "r2"

        t1 = asyncio.create_task(coordinator.submit("user:1", "msg1", slow1))
        t2 = asyncio.create_task(coordinator.submit("user:2", "msg2", slow2))
        await started1.wait()
        await started2.wait()

        assert "start_1" in order
        assert "start_2" in order
        # 两者都开始执行了，说明不同 target 之间是并行的

        barrier1.set()
        barrier2.set()
        r1 = await t1
        r2 = await t2
        assert r1.status == "success"
        assert r1.value == "r1"
        assert r2.status == "success"
        assert r2.value == "r2"

    @pytest.mark.asyncio
    async def test_iteration_exhaustion_does_not_call_on_exhausted_when_had_success(self, coordinator):
        """超过 MAX_ITERATIONS 且 had_success=True 时不应调用 on_exhausted"""
        call_count = 0
        on_exhausted_called = False

        async def call_fn(messages):
            nonlocal call_count
            call_count += 1
            coordinator._has_buffered["user:1"] = True
            return f"result_{call_count}"

        async def on_exhausted(last_exception=None):
            nonlocal on_exhausted_called
            on_exhausted_called = True
            return "exhausted_fallback"

        result = await coordinator.submit(
            "user:1", "msg", call_fn, continue_on_buffered=True, on_exhausted=on_exhausted
        )
        assert call_count == 5
        assert on_exhausted_called is False
        # 超过 max_iterations 后仍返回最后一次 call_fn 的结果（last_result_sent=False）
        assert result.status == "success"
        assert result.value == "result_5"

    @pytest.mark.asyncio
    async def test_on_result_exception_treated_as_sent_to_avoid_duplicate(self, coordinator):
        """on_result 抛异常时应将 last_result_sent 置 True，
        避免 max_iterations 强制退出时重复投递（保守策略）。"""
        call_count = 0

        async def call_fn(messages):
            nonlocal call_count
            call_count += 1
            coordinator._has_buffered["user:dup"] = True
            return f"result_{call_count}"

        async def on_result(value):
            raise RuntimeError(f"投递失败 for {value}")

        async def on_exhausted(last_exception=None):
            return None

        result = await coordinator.submit(
            "user:dup",
            "msg",
            call_fn,
            continue_on_buffered=True,
            on_exhausted=on_exhausted,
            on_result=on_result,
        )
        # on_result 始终抛异常，但保守策略下视为已发送，因此最终强制退出时返回 None
        # （SubmitResult: result is None → status="failed", value=None，避免重复投递）
        assert call_count == 5
        assert result.status == "failed"
        assert result.value is None
