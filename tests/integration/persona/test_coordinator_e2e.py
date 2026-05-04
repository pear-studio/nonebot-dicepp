"""
Coordinator 端到端验证

直接实例化 PersonaOrchestrator + Mock LLM，
验证 chat/share 路径在 coordinator 下的串行化、合并重试、失败回退行为。
"""

import pytest
import asyncio
import tempfile
import os
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, MagicMock, patch

from plugins.DicePP.module.persona.orchestrator import PersonaOrchestrator
from plugins.DicePP.module.persona.data.models import RelationshipState
from plugins.DicePP.module.persona.proactive.models import ShareTarget
from plugins.DicePP.module.persona.proactive.scheduler import ProactiveScheduler, ProactiveConfig

# 复用 test_orchestrator_chat.py 的 fixture 和辅助函数
from tests.integration.persona.test_orchestrator_chat import (
    _build_orchestrator_with_mock_llm,
    _default_persona_config,
    _make_mock_bot,
    temp_db,
)

pytestmark = pytest.mark.skip(reason="TODO: 重构任务三 — 适配 ChatSession / LifeSimulator / MessagePort 新架构后重写集成测试")


def _make_openai_response(content: str):
    """创建可被 LLM router 解析的 mock response 对象"""
    response = Mock()
    response.choices = [Mock()]
    response.choices[0].message = Mock()
    response.choices[0].message.content = content
    response.choices[0].message.tool_calls = None
    response.usage = Mock()
    response.usage.prompt_tokens = 10
    response.usage.completion_tokens = 5
    response.usage.prompt_tokens_details = None
    return response


@pytest.fixture
async def orch_and_mock(temp_db, monkeypatch):
    """返回已初始化的 orchestrator 和 mock LLM client"""
    orch, mock_client = await _build_orchestrator_with_mock_llm(temp_db, monkeypatch)
    return orch, mock_client


async def _seed_chat_history(orch: PersonaOrchestrator, user_id: str, group_id: str):
    """预写入对话历史，避免触发 first_mes"""
    await orch.data_store.add_message(user_id, group_id, "user", "之前的消息")
    await orch.data_store.add_message(user_id, group_id, "assistant", "之前的回复")


class TestCoordinatorChatMerge:
    """4.3 同一用户连续两条消息 → 合并为一次 LLM 调用序列"""

    @pytest.mark.asyncio
    async def test_two_rapid_messages_single_reply(self, orch_and_mock):
        """第一次 chat 执行期间，第二次 submit 被 buffered，
        第一次成功后触发第二次 call_fn（合并两条消息），最终只返回一条回复。"""
        orch, mock_client = orch_and_mock

        await _seed_chat_history(orch, "u1", "")

        call_count = 0
        llm_started = asyncio.Event()
        second_may_proceed = asyncio.Event()

        async def slow_llm(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                llm_started.set()
                await second_may_proceed.wait()
            return _make_openai_response(f"reply_{call_count}")

        mock_client.chat.completions.create = AsyncMock(side_effect=slow_llm)

        results = []

        async def first_chat():
            r = await orch.chat("u1", "", "你好", nickname="User")
            results.append(("first", r))

        async def second_chat():
            await llm_started.wait()
            r = await orch.chat("u1", "", "在吗", nickname="User")
            results.append(("second", r))
            second_may_proceed.set()

        await asyncio.gather(first_chat(), second_chat())

        first_results = [r for name, r in results if name == "first"]
        second_results = [r for name, r in results if name == "second"]

        # 第一次 chat 返回第二次 call_fn 的结果（continue_on_buffered=True）
        assert first_results[-1] == "reply_2"
        # 第二次 chat 作为 pending caller 返回 None
        assert second_results[0] is None
        # LLM 共调用 2 次
        assert call_count == 2


class TestCoordinatorChatFailExhausted:
    """4.5 强制 LLM 连续失败 3 次 → 返回 fallback"""

    @pytest.mark.asyncio
    async def test_three_failures_delivers_fallback(self, orch_and_mock):
        """Mock LLM 始终抛异常，通过自然触发 3 次 submit 让 coordinator
        经历 3 次 call_fn 失败后触发 on_exhausted，返回 fallback 文案。"""
        orch, mock_client = orch_and_mock

        await _seed_chat_history(orch, "u1", "")

        fail_count = 0
        call_started = [asyncio.Event(), asyncio.Event()]

        async def always_fail(*args, **kwargs):
            nonlocal fail_count
            idx = fail_count
            fail_count += 1
            if idx < 2:
                call_started[idx].set()
            await asyncio.sleep(0.05)
            raise RuntimeError("LLM down")

        mock_client.chat.completions.create = AsyncMock(side_effect=always_fail)

        results = []

        async def chat_1():
            r = await orch.chat("u1", "", "msg1", nickname="User")
            results.append(("first", r))

        async def chat_2():
            await call_started[0].wait()
            await asyncio.sleep(0.01)
            r = await orch.chat("u1", "", "msg2", nickname="User")
            results.append(("second", r))

        async def chat_3():
            await call_started[1].wait()
            await asyncio.sleep(0.01)
            r = await orch.chat("u1", "", "msg3", nickname="User")
            results.append(("third", r))

        await asyncio.gather(chat_1(), chat_2(), chat_3())

        first_result = [r for name, r in results if name == "first"][0]
        # on_exhausted 返回 fallback
        assert first_result == "LLM服务暂时不可用，请稍后再试"
        # 连续失败 3 次
        assert fail_count == 3

        # pending callers 返回 None（buffered）
        assert all(r is None for name, r in results if name != "first")

        # 验证 fallback 已持久化到数据库
        msgs = await orch.data_store.get_recent_messages("u1", "", limit=5)
        assert any(m.content == "LLM服务暂时不可用，请稍后再试" for m in msgs)


class TestCoordinatorShareSilentDiscard:
    """4.7 share 失败 3 次 → 静默丢弃，不向用户报错"""

    @pytest.mark.asyncio
    async def test_share_failure_three_times_silently_discarded(self, orch_and_mock):
        """Scheduler share 路径使用 continue_on_buffered=False 且无 on_exhausted，
        通过自然触发 3 次 submit 让 coordinator 经历 3 次失败后返回 None，上层静默丢弃。

        由于 _build_and_generate_share_message 被 mock 为始终抛异常，
        3 个并发的 miss submit 驱动 coordinator 重试 3 次后放弃。
        """
        orch, _ = orch_and_mock

        orch.scheduler.coordinator = orch.coordinator

        call_count = 0
        call_started = [asyncio.Event(), asyncio.Event()]

        async def always_fail(*args, **kwargs):
            nonlocal call_count
            idx = call_count
            call_count += 1
            if idx < 2:
                call_started[idx].set()
            await asyncio.sleep(0.05)
            raise RuntimeError("share generation down")

        with patch.object(
            orch.scheduler,
            "_build_and_generate_share_message",
            side_effect=always_fail,
        ):
            target = ShareTarget(
                user_id="u1",
                group_id="",
                priority=50,
                score=50.0,
                policy="normal",
            )

            results = []

            async def miss_1():
                r = await orch.scheduler._create_miss_you_message(
                    target, "event_desc", "event_reaction"
                )
                results.append(("first", r))

            async def miss_2():
                await call_started[0].wait()
                await asyncio.sleep(0.01)
                r = await orch.scheduler._create_miss_you_message(
                    target, "event_desc", "event_reaction"
                )
                results.append(("second", r))

            async def miss_3():
                await call_started[1].wait()
                await asyncio.sleep(0.01)
                r = await orch.scheduler._create_miss_you_message(
                    target, "event_desc", "event_reaction"
                )
                results.append(("third", r))

            await asyncio.gather(miss_1(), miss_2(), miss_3())

        # 静默丢弃：第一个 caller 失败返回 None；pending callers 返回 buffered marker
        for name, r in results:
            assert r is None or r == {"__coordinator_buffered": True}
        # coordinator 级失败 3 次
        assert call_count == 3


class TestCoordinatorChatSuccessThenBufferedRetry:
    """4.6 第一次成功 + buffered 新消息 → 第二次合并重试成功"""

    @pytest.mark.asyncio
    async def test_success_then_buffered_retry_succeeds(self, orch_and_mock):
        """第一次 call_fn 成功，但期间有新消息到达（自然触发 buffered），
        coordinator 再次调用 call_fn，第二次也成功，返回第二次的结果。"""
        orch, mock_client = orch_and_mock

        await _seed_chat_history(orch, "u1", "")

        call_count = 0
        first_started = asyncio.Event()

        async def succeed_twice(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                first_started.set()
                await asyncio.sleep(0.05)
            return _make_openai_response(f"reply_{call_count}")

        mock_client.chat.completions.create = AsyncMock(side_effect=succeed_twice)

        results = []

        async def first_chat():
            r = await orch.chat("u1", "", "你好", nickname="User")
            results.append(("first", r))

        async def second_chat():
            await first_started.wait()
            await asyncio.sleep(0.01)
            r = await orch.chat("u1", "", "在吗", nickname="User")
            results.append(("second", r))

        await asyncio.gather(first_chat(), second_chat())

        first_results = [r for name, r in results if name == "first"]
        second_results = [r for name, r in results if name == "second"]

        # 返回第二次的结果
        assert first_results[-1] == "reply_2"
        # 第二次 chat 作为 pending caller 返回 None
        assert second_results[0] is None
        # 共调用 2 次
        assert call_count == 2


class TestCoordinatorChatAndShareSerialization:
    """4.4 chat 执行期间 share 到达同一 target → share 排队等待"""

    @pytest.mark.asyncio
    async def test_chat_blocks_share_on_same_target(self, orch_and_mock):
        """chat 正在执行时，scheduler share 提交到同一 user key，
        share 的 submit 发现 executing=True，标记 buffered 后返回 None。

        为避免 event_agent 内部重试耗时过长，直接 mock _build_and_generate_share_message
        使其快速返回，只验证 coordinator 的串行化行为。
        """
        orch, mock_client = orch_and_mock
        orch.scheduler.coordinator = orch.coordinator

        await _seed_chat_history(orch, "u1", "")

        chat_started = asyncio.Event()
        chat_may_finish = asyncio.Event()
        share_submitted = asyncio.Event()

        async def slow_chat_llm(*args, **kwargs):
            chat_started.set()
            await chat_may_finish.wait()
            return _make_openai_response("chat_reply")

        mock_client.chat.completions.create = AsyncMock(side_effect=slow_chat_llm)

        # 拦截 _create_miss_you_message 以确认 share 已调用 submit
        original_create_miss = orch.scheduler._create_miss_you_message

        async def patched_create_miss(*args, **kwargs):
            share_submitted.set()
            return await original_create_miss(*args, **kwargs)

        # mock share 生成使其立即返回，跳过 event_agent 内部重试
        async def fast_share(*args, **kwargs):
            return {"user_id": "u1", "group_id": "", "content": "share_msg", "type": "miss_you"}

        with patch.object(orch.scheduler, "_build_and_generate_share_message", side_effect=fast_share):
            with patch.object(orch.scheduler, "_create_miss_you_message", side_effect=patched_create_miss):
                chat_result = None
                share_result = None

                async def do_chat():
                    nonlocal chat_result
                    chat_result = await orch.chat("u1", "", "你好", nickname="User")

                async def do_share():
                    nonlocal share_result
                    await chat_started.wait()
                    target = ShareTarget(
                        user_id="u1",
                        group_id="",
                        priority=50,
                        score=50.0,
                        policy="normal",
                    )
                    share_result = await orch.scheduler._create_miss_you_message(
                        target, "event", "reaction"
                    )

                chat_task = asyncio.create_task(do_chat())
                share_task = asyncio.create_task(do_share())

                # 等待 share 调用 submit
                await share_submitted.wait()
                # 让步一次，让 coordinator.submit() 内部 lock 逻辑执行完毕
                await asyncio.sleep(0)

                # chat 正在执行中
                assert orch.coordinator._executing.get("user:u1") is True
                # share 已被标记为 buffered（因为 executing=True，submit 直接返回 buffered）
                assert orch.coordinator._has_buffered.get("user:u1") is True

                # 释放 chat
                chat_may_finish.set()
                await chat_task
                await share_task

        # chat 成功返回
        assert chat_result == "chat_reply"
        # share 作为 pending caller 返回 buffered marker（continue_on_buffered=False 不消费 buffered）
        assert share_result is None or share_result == {"__coordinator_buffered": True}
        # _last_proactive_time 未被更新，证明 miss_call_fn 未执行（被 buffered 跳过）
        assert "user:u1" not in orch.scheduler._last_proactive_time


class TestCoordinatorGroupChat:
    """4.8 群聊路径 coordinator 串行化"""

    @pytest.mark.asyncio
    async def test_group_chat_rapid_messages_single_reply(self, orch_and_mock):
        """群聊中连续两条消息 → 合并为一次 LLM 调用，且不重复写入私聊 user 消息表。"""
        orch, mock_client = orch_and_mock

        await _seed_chat_history(orch, "u1", "g1")

        call_count = 0
        llm_started = asyncio.Event()
        second_may_proceed = asyncio.Event()

        async def slow_llm(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                llm_started.set()
                await second_may_proceed.wait()
            return _make_openai_response(f"reply_{call_count}")

        mock_client.chat.completions.create = AsyncMock(side_effect=slow_llm)

        results = []

        async def first_chat():
            r = await orch.chat("u1", "g1", "你好", nickname="User")
            results.append(("first", r))

        async def second_chat():
            await llm_started.wait()
            r = await orch.chat("u1", "g1", "在吗", nickname="User")
            results.append(("second", r))
            second_may_proceed.set()

        await asyncio.gather(first_chat(), second_chat())

        first_results = [r for name, r in results if name == "first"]
        second_results = [r for name, r in results if name == "second"]

        assert first_results[-1] == "reply_2"
        assert second_results[0] is None
        assert call_count == 2

        # 验证没有向私聊 user 消息表写入群聊消息
        private_msgs = await orch.data_store.get_recent_messages("u1", "", limit=5)
        assert not any(m.content in ("你好", "在吗") for m in private_msgs)


class TestCoordinatorToolsEnabled:
    """4.9 tools_enabled=True 路径串行化"""

    @pytest.mark.asyncio
    async def test_tools_path_rapid_messages_single_reply(self, orch_and_mock):
        """tools 路径下连续两条消息 → 合并为一次 LLM 调用序列。"""
        orch, _ = orch_and_mock

        await _seed_chat_history(orch, "u1", "")

        original_tools_enabled = orch.config.tools_enabled
        orch.config.tools_enabled = True

        call_count = 0
        tool_started = asyncio.Event()
        second_may_proceed = asyncio.Event()

        async def mock_chat_with_tools(user_id, group_id, messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                tool_started.set()
                await second_may_proceed.wait()
            return f"tools_reply_{call_count}"

        with patch.object(orch, "_chat_with_tools", side_effect=mock_chat_with_tools):
            results = []

            async def first_chat():
                r = await orch.chat("u1", "", "你好", nickname="User")
                results.append(("first", r))

            async def second_chat():
                await tool_started.wait()
                r = await orch.chat("u1", "", "在吗", nickname="User")
                results.append(("second", r))
                second_may_proceed.set()

            await asyncio.gather(first_chat(), second_chat())

            first_results = [r for name, r in results if name == "first"]
            second_results = [r for name, r in results if name == "second"]

            assert first_results[-1] == "tools_reply_2"
            assert second_results[0] is None
            assert call_count == 2

        orch.config.tools_enabled = original_tools_enabled
