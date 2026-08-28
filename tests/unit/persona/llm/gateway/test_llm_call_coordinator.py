"""
单元测试: LLMCallCoordinator

涵盖基础行为、并发控制、失败重试、耗尽/取消/边界场景。
合并自: test_llm_call_coordinator.py + test_llm_call_coordinator_exhaustion.py
"""
import pytest
import asyncio
from unittest.mock import AsyncMock

from plugins.DicePP.module.persona.llm.coordinator import (
    LLMCallCoordinator,
    SubmitResult,
)


@pytest.fixture
def coordinator():
    return LLMCallCoordinator()


class TestLLMCallCoordinatorBasics:
    """测试基础行为"""

    @pytest.mark.asyncio
    async def test_single_successful_submit_returns_result(self, coordinator):
        call_fn = AsyncMock(return_value="hello")
        result = await coordinator.submit("user:1", "msg", call_fn)
        assert result.status == "success"
        assert result.value == "hello"
        call_fn.assert_awaited_once_with(["msg"])

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
    async def test_buffered_submits_are_processed_in_a_follow_up_round(self, coordinator):
        """成功后发现 buffered 时，下一轮处理待处理消息。"""
        barrier = asyncio.Event()
        execution_started = asyncio.Event()
        second_done = asyncio.Event()
        second_result = None
        attempts = []

        async def slow_call_fn(messages):
            attempts.append(messages)
            if len(attempts) == 1:
                execution_started.set()
                await barrier.wait()
            return f"result_{len(attempts)}"

        async def second_submit():
            nonlocal second_result
            second_result = await coordinator.submit(
                "user:1", "msg2", AsyncMock(return_value="ignored")
            )
            second_done.set()

        first_task = asyncio.create_task(
            coordinator.submit("user:1", "msg1", slow_call_fn)
        )
        await execution_started.wait()

        second_task = asyncio.create_task(second_submit())
        await second_done.wait()

        # 第二个 submit 应该被 buffered
        assert second_result.status == "buffered"
        assert coordinator._has_buffered.get("user:1") is True

        barrier.set()
        result = await first_task
        assert len(attempts) == 2
        assert attempts == [["msg1"], ["msg2"]]
        assert result.status == "success"
        assert result.value == "result_2"

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

        attempts = []

        async def failing_call_fn(messages):
            attempts.append(messages)
            if len(attempts) == 1:
                started1.set()
                await barrier1.wait()
            elif len(attempts) == 2:
                started2.set()
                await barrier2.wait()
            elif len(attempts) == 3:
                started3.set()
                await barrier3.wait()
            raise Exception("temporarily unavailable")

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
        assert attempts == [["msg1"], ["msg2"], ["msg3"]]
        assert on_exhausted_called is True

    @pytest.mark.asyncio
    async def test_success_resets_failure_counter(self, coordinator):
        started1 = asyncio.Event()
        started2 = asyncio.Event()
        barrier1 = asyncio.Event()
        barrier2 = asyncio.Event()
        attempts = []

        async def inner_call_fn(messages):
            attempts.append(messages)
            if len(attempts) == 1:
                started1.set()
                await barrier1.wait()
                raise Exception("temporarily unavailable")
            if len(attempts) == 2:
                started2.set()
                await barrier2.wait()
                raise Exception("temporarily unavailable")
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
        assert attempts == [["msg1"], ["msg2"], ["msg3"]]
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


class TestLLMCallCoordinatorExhaustion:
    """耗尽/取消/边界测试"""

    @pytest.mark.asyncio
    async def test_on_exhausted_exception_does_not_crash(self, coordinator):
        async def bad_on_exhausted(last_exception=None):
            raise RuntimeError("boom")

        attempts = []

        async def inner_failing_call_fn(messages):
            attempts.append(messages)
            # 直接设置内部状态以精确控制 _run_loop 的重试路径，
            # 而非测试并发 buffered 行为本身。
            if len(attempts) <= 2:
                coordinator._has_buffered["user:1"] = True
            raise Exception("temporarily unavailable")

        call_fn = AsyncMock(side_effect=inner_failing_call_fn)
        # 不应抛出异常
        result = await coordinator.submit(
            "user:1", "msg", call_fn, on_exhausted=bad_on_exhausted
        )
        assert result.status == "failed"
        assert result.value is None
        assert attempts == [["msg"], [], []]

    @pytest.mark.asyncio
    async def test_max_iterations_prevents_infinite_loop(self, coordinator):
        """MAX_ITERATIONS=5 防止用户刷屏导致无限循环"""
        started_events = [asyncio.Event() for _ in range(5)]
        barriers = [asyncio.Event() for _ in range(4)]
        attempts = []

        async def call_fn(messages):
            attempts.append(messages)
            idx = len(attempts) - 1
            started_events[idx].set()
            if idx < 4:
                await barriers[idx].wait()
            return f"result_{len(attempts)}"

        first_task = asyncio.create_task(coordinator.submit("user:1", "msg", call_fn))
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
        assert len(attempts) == 5
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
        attempts = []

        async def inner_failing_call_fn(messages):
            attempts.append(messages)
            if len(attempts) == 1:
                started1.set()
                await barrier1.wait()
            elif len(attempts) == 2:
                started2.set()
                await barrier2.wait()
            elif len(attempts) == 3:
                started3.set()
                await barrier3.wait()
            raise Exception("temporarily unavailable")

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
        assert attempts == [["msg1"], ["msg2"], ["msg3"]]
        assert on_exhausted_called is True
        # buffered 在最后一次失败后被 pop（此时 has_buffered=True 但 failures >= max_failures，不继续）
        assert coordinator._has_buffered.get("user:1") is None

    @pytest.mark.asyncio
    async def test_on_exhausted_only_when_never_succeeded(self, coordinator):
        """had_success=True 时即使最终失败也不触发 on_exhausted"""
        started1 = asyncio.Event()
        barrier1 = asyncio.Event()
        attempts = []
        on_exhausted_called = False

        async def call_fn(messages):
            attempts.append(messages)
            if len(attempts) == 1:
                started1.set()
                await barrier1.wait()
                return "first_success"
            raise Exception("temporarily unavailable")

        async def on_exhausted_fn():
            nonlocal on_exhausted_called
            on_exhausted_called = True

        first_task = asyncio.create_task(
            coordinator.submit(
                "user:1", "msg1", call_fn, on_exhausted=on_exhausted_fn
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
        assert attempts == [["msg1"], ["msg2"]]
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
    async def test_pending_messages_preserved_across_submits(self):
        """驱动任务退出后缓冲消息保留，下一次 submit 追加而非覆盖。"""
        # 此测试验证 pending 消息追加的独立价值。
        coordinator = LLMCallCoordinator()
        seen_messages = []

        # 模拟执行中：直接设置 _executing
        key = "user:1"
        coordinator._executing[key] = True

        # chat 提交 → buffered
        chat_a = await coordinator.submit(
            key, "msg_A", AsyncMock(return_value="rA")
        )
        assert chat_a.status == "buffered"
        assert coordinator._pending_messages.get(key) == ["msg_A"]

        # 模拟驱动任务退出：清除 _executing 和 _has_buffered，
        # 但保留 _pending_messages（finally 块逻辑）
        coordinator._executing.pop(key, None)
        coordinator._has_buffered.pop(key, None)

        # 验证缓冲消息残留
        assert coordinator._pending_messages.get(key) == ["msg_A"]

        # 新 chat submit：应追加 msg_B，而非覆盖 msg_A
        async def chat_fn(messages):
            seen_messages.extend(messages)
            return "reply"

        chat_b = await coordinator.submit(key, "msg_B", chat_fn)
        assert chat_b.status == "success"
        assert chat_b.value == "reply"
        assert "msg_A" in seen_messages
        assert "msg_B" in seen_messages
        assert len(seen_messages) == 2

    @pytest.mark.asyncio
    async def test_iteration_exhaustion_does_not_call_on_exhausted_when_had_success(self, coordinator):
        """超过 MAX_ITERATIONS 且 had_success=True 时不应调用 on_exhausted"""
        attempts = []
        on_exhausted_called = False

        async def call_fn(messages):
            attempts.append(messages)
            coordinator._has_buffered["user:1"] = True
            return f"result_{len(attempts)}"

        async def on_exhausted(last_exception=None):
            nonlocal on_exhausted_called
            on_exhausted_called = True
            return "exhausted_fallback"

        result = await coordinator.submit(
            "user:1", "msg", call_fn, on_exhausted=on_exhausted
        )
        assert len(attempts) == 5
        assert on_exhausted_called is False
        # 超过 max_iterations 后仍返回最后一次 call_fn 的结果（last_result_sent=False）
        assert result.status == "success"
        assert result.value == "result_5"

    @pytest.mark.asyncio
    async def test_on_result_exception_treated_as_sent_to_avoid_duplicate(self, coordinator):
        """on_result 抛异常时应将 last_result_sent 置 True，
        避免 max_iterations 强制退出时重复投递（保守策略）。"""
        attempts = []

        async def call_fn(messages):
            attempts.append(messages)
            coordinator._has_buffered["user:dup"] = True
            return f"result_{len(attempts)}"

        async def on_result(value):
            raise RuntimeError(f"投递失败 for {value}")

        async def on_exhausted(last_exception=None):
            return None

        result = await coordinator.submit(
            "user:dup",
            "msg",
            call_fn,
            on_exhausted=on_exhausted,
            on_result=on_result,
        )
        # on_result 始终抛异常，但保守策略下视为已发送，因此最终强制退出时返回 None
        # （SubmitResult: result is None → status="failed", value=None，避免重复投递）
        assert len(attempts) == 5
        assert result.status == "failed"
        assert result.value is None
