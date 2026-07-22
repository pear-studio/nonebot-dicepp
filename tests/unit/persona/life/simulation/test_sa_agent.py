"""
单元测试: SAAgent — build_run_spec / interpret_result / run() 入口

T5: SA 通过 Agent 基类 AgentRunSpec 新路径执行，
不再直接 new AgentRuntime 或构造 AgentRunRequest。
"""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from module.persona.life.sa_agent import SAAgent
from module.persona.life.types import AgentResult
from module.persona.data.models import SAState


# ── 辅助: 创建 mock ConversationRunResult ──────────────────────


def _make_conv_result(*, completion_kind="completed", output_arguments=None,
                      output_call_index=0, final_text="", final_reason="output_collected"):
    from module.persona.life.conversation import ConversationRunResult
    return ConversationRunResult(
        final_text=final_text,
        final_reason=final_reason,
        completion_kind=completion_kind,
        output_arguments=output_arguments,
        output_call_index=output_call_index,
        run_id="test-run",
        interaction_id="test-interaction",
    )


class TestSAAgent:
    """测试 SAAgent — 走 Agent 基类 run() 新路径"""

    @pytest.fixture
    def mock_store(self):
        store = MagicMock()
        store.get_sa_state = AsyncMock(return_value=SAState())
        store.update_sa_state = AsyncMock()
        store.get_story_deck_count = AsyncMock(return_value=0)
        store.list_story_deck_entries = AsyncMock(return_value=[])
        store.search_story_deck = AsyncMock(return_value=[])
        store.get_story_deck_entry = AsyncMock(return_value=None)
        store.get_linked_entries = AsyncMock(return_value=[])
        store.get_daily_events = AsyncMock(return_value=[])
        # Conversation store
        store.put = AsyncMock(return_value="conv-1")
        store.get = AsyncMock(return_value=None)
        store.delete = AsyncMock()
        return store

    @pytest.fixture
    def mock_router(self):
        return MagicMock()

    @pytest.fixture
    def sa_agent(self, mock_store, mock_router):
        return SAAgent(store=mock_store, router=mock_router)

    @pytest.fixture
    def sa_context(self):
        return {
            "character_name": "测试角色",
            "character_description": "一个冒险者",
            "world": "奇幻世界",
            "diary_text": "今天去了市场。",
            "events_text": "在市场买到了稀有草药。",
            "story_deck_is_empty": True,
        }

    @pytest.mark.asyncio
    async def test_run_uses_conversation_run(self, sa_agent, sa_context):
        """T5: SA run() 走 Conversation.run() 新入口（不再直接 new AgentRuntime）"""
        mock_conv = MagicMock()
        mock_conv.run = AsyncMock(return_value=_make_conv_result(
            output_arguments={"summary": "规划完成", "changed": True},
        ))

        with patch.object(sa_agent, "_ensure_conversation", AsyncMock(return_value=mock_conv)):
            result = await sa_agent.run(sa_context, interaction_id="test-id")

        assert result.success is True
        assert isinstance(result.data, SAState)
        # 验证 Conversation.run 被调用
        mock_conv.run.assert_called_once()
        # 验证 state 被保存
        sa_agent.store.update_sa_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_finish_plan_success(self, sa_agent, sa_context):
        """finish_plan 成功路径：output_arguments 含 summary 和 changed"""
        mock_conv = MagicMock()
        mock_conv.run = AsyncMock(return_value=_make_conv_result(
            output_arguments={"summary": "创建了初始条目", "changed": True},
        ))

        with patch.object(sa_agent, "_ensure_conversation", AsyncMock(return_value=mock_conv)):
            result = await sa_agent.run(sa_context, interaction_id="test-id")

        assert result.success is True
        assert result.raw_response == "创建了初始条目"
        sa_agent.store.update_sa_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_finish_plan_limit_reached(self, sa_agent, sa_context):
        """未调用 finish_plan 时 run 失败（limit_reached）"""
        mock_conv = MagicMock()
        mock_conv.run = AsyncMock(return_value=_make_conv_result(
            completion_kind="limit_reached",
            output_arguments=None,
            final_reason="max_rounds",
        ))

        with patch.object(sa_agent, "_ensure_conversation", AsyncMock(return_value=mock_conv)):
            result = await sa_agent.run(sa_context, interaction_id="test-id")

        assert result.success is False
        # state 仍被保存（edit_fronts 中途修改可能已生效）
        sa_agent.store.update_sa_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_finish_plan_unchanged(self, sa_agent, sa_context):
        """即使无需修改，也应调用 finish_plan(changed=False)"""
        mock_conv = MagicMock()
        mock_conv.run = AsyncMock(return_value=_make_conv_result(
            output_arguments={"summary": "无需调整", "changed": False},
        ))

        with patch.object(sa_agent, "_ensure_conversation", AsyncMock(return_value=mock_conv)):
            result = await sa_agent.run(sa_context, interaction_id="test-id")

        assert result.success is True
        assert isinstance(result.data, SAState)

    @pytest.mark.asyncio
    async def test_preserves_fronts_from_tools(self, sa_agent, sa_context):
        """验证 tool 修改的 fronts_dicts 被正确写回 SAState"""
        from module.persona.data.models import Front, Thread
        existing_front = Front(
            name="主线",
            type="campaign",
            threads=[Thread(name="探索", direction="探索旧城区",
                           milestones=["发现线索"], outcome="揭开秘密", related=[])],
        )
        sa_agent.store.get_sa_state = AsyncMock(return_value=SAState(fronts=[existing_front]))

        mock_conv = MagicMock()
        mock_conv.run = AsyncMock(return_value=_make_conv_result(
            output_arguments={"summary": "规划完成", "changed": True},
        ))

        with patch.object(sa_agent, "_ensure_conversation", AsyncMock(return_value=mock_conv)):
            result = await sa_agent.run(sa_context, interaction_id="test-id")

        assert result.success is True
        assert len(result.data.fronts) == 1
        assert result.data.fronts[0].name == "主线"
        assert len(result.data.fronts[0].threads) == 1

    @pytest.mark.asyncio
    async def test_empty_fronts(self, sa_agent, sa_context):
        """fronts 为空时正常保存 state"""
        mock_conv = MagicMock()
        mock_conv.run = AsyncMock(return_value=_make_conv_result(
            output_arguments={"summary": "无需调整", "changed": False},
        ))

        with patch.object(sa_agent, "_ensure_conversation", AsyncMock(return_value=mock_conv)):
            result = await sa_agent.run(sa_context, interaction_id="test-id")

        assert result.success is True
        sa_agent.store.update_sa_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_exception_handling(self, sa_agent, sa_context):
        """build_run_spec 异常时返回 success=False"""
        with patch.object(sa_agent, "build_run_spec",
                          AsyncMock(side_effect=RuntimeError("LLM 调用失败"))):
            result = await sa_agent.run(sa_context, interaction_id="test-id")

        assert result.success is False
        assert "SA 执行异常" in result.error or "LLM 调用失败" in result.error or "执行异常" in result.error

    @pytest.mark.asyncio
    async def test_build_run_spec_returns_agent_run_spec(self, sa_agent, sa_context):
        """build_run_spec 返回 AgentRunSpec（含 finish_plan OutputSpec）"""
        from module.persona.agent.runtime_types import AgentRunSpec

        spec = await sa_agent.build_run_spec(sa_context)
        assert isinstance(spec, AgentRunSpec)
        assert spec.output is not None
        assert spec.output.name == "finish_plan"
        assert spec.output.description == "提交本次叙事规划结果，并结束本次规划。"
        assert "finish_plan" not in spec.tools.tools  # OutputSpec 不在 ToolKit 里
        assert len(spec.tools.tools) >= 5  # 5 个 SA 工具


class TestSABuildUserPrompt:
    """测试 SAAgent._build_user_prompt()"""

    @pytest.fixture
    def mock_store(self):
        store = MagicMock()
        store.get_sa_state = AsyncMock(return_value=SAState())
        store.update_sa_state = AsyncMock()
        store.get_story_deck_count = AsyncMock(return_value=0)
        store.list_story_deck_entries = AsyncMock(return_value=[])
        store.search_story_deck = AsyncMock(return_value=[])
        store.get_story_deck_entry = AsyncMock(return_value=None)
        store.get_linked_entries = AsyncMock(return_value=[])
        store.get_daily_events = AsyncMock(return_value=[])
        return store

    @pytest.fixture
    def mock_router(self):
        return MagicMock()

    @pytest.fixture
    def sa_agent(self, mock_store, mock_router):
        return SAAgent(store=mock_store, router=mock_router)

    def test_prompt_contains_front_rules(self, sa_agent):
        """_build_user_prompt 应包含 _FRONT_RULES"""
        context = {
            "character_name": "测试", "character_description": "描述",
            "world": "现代",
            "diary_text": "", "events_text": "",
            "story_deck_is_empty": False,
        }
        prompt = sa_agent._build_user_prompt(context, [])
        assert "Front 是写给你自己的叙事规划草稿" in prompt
        assert "Milestone 写法" in prompt

    def test_prompt_bootstrap_when_empty(self, sa_agent):
        """fronts 为空 + story_deck_is_empty=True 时包含 bootstrap 引导"""
        context = {
            "character_name": "测试", "character_description": "描述",
            "world": "现代",
            "diary_text": "", "events_text": "",
            "story_deck_is_empty": True,
        }
        prompt = sa_agent._build_user_prompt(context, [])
        assert "还没有 fronts 和 story_deck 条目" in prompt
        assert "请根据角色设定创建初始的 campaign front" in prompt

    def test_prompt_no_bootstrap_when_has_fronts(self, sa_agent):
        """fronts 非空时不包含 bootstrap 引导"""
        context = {
            "character_name": "测试", "character_description": "描述",
            "world": "现代",
            "diary_text": "", "events_text": "",
            "story_deck_is_empty": True,
        }
        prompt = sa_agent._build_user_prompt(
            context, [{"name": "主线", "type": "campaign", "threads": []}],
        )
        assert "还没有 fronts 和 story_deck 条目" not in prompt

    def test_prompt_no_bootstrap_when_story_deck_not_empty(self, sa_agent):
        """story_deck 非空时不包含 bootstrap 引导（即使 fronts 为空）"""
        context = {
            "character_name": "测试", "character_description": "描述",
            "world": "现代",
            "diary_text": "", "events_text": "",
            "story_deck_is_empty": False,
        }
        prompt = sa_agent._build_user_prompt(context, [])
        assert "还没有 fronts 和 story_deck 条目" not in prompt


# ── R10: SA 自清理 — finally 块确保 Conversation 销毁 ────────


class TestSASelfCleanup:
    """R10: SAAgent.run() finally 块确保 Conversation 销毁。

    SA 没有 registry（不使用会话聊天），Conversation 由内存 _conversation 持有。
    run() 的 finally 块调用 compact_conversation()，不论成功/异常均清除。
    """

    @pytest.fixture
    def store(self):
        s = MagicMock()
        s.get_sa_state = AsyncMock(return_value=SAState())
        s.update_sa_state = AsyncMock()
        s.get_story_deck_count = AsyncMock(return_value=0)
        return s

    @pytest.fixture
    def router(self):
        return MagicMock()

    @pytest.fixture
    def agent(self, store, router):
        return SAAgent(store=store, router=router)

    @staticmethod
    def _spec():
        from module.persona.agent.runtime_types import (
            AgentRunSpec, ToolKit, LoopLimits,
        )
        from module.persona.llm.selection import SUMMARIZE
        return AgentRunSpec(
            system_prompt="test", user_input="test", tools=ToolKit(),
            output=None, selection=SUMMARIZE, limits=LoopLimits(max_rounds=5),
        )

    @pytest.mark.asyncio
    async def test_sa_run_cleans_up_on_success(self, agent):
        """R10(a): SA run 成功后 _conversation 已清空。"""
        mock_conv = MagicMock()
        mock_conv.run = AsyncMock(return_value=_make_conv_result(
            output_arguments={"summary": "完成", "changed": True},
        ))
        agent._conversation = mock_conv
        agent._system_prompt = "test prompt"

        agent.build_run_spec = AsyncMock(return_value=self._spec())

        async def mock_ensure(ctx, system_prompt_override=None):
            return agent._conversation

        agent._ensure_conversation = mock_ensure
        agent.interpret_result = AsyncMock(
            return_value=AgentResult(success=True, data={}),
        )

        result = await agent.run({}, interaction_id="r10_a")

        assert result.success
        # finally 块已清理
        assert agent._conversation is None, "finally 应清空 _conversation"
        assert agent._system_prompt is None, "finally 应清空 _system_prompt"

    @pytest.mark.asyncio
    async def test_sa_run_cleans_up_on_exception(self, agent):
        """R10(b): conv.run 抛异常后 finally 仍清空 _conversation。

        模拟 conv.run 抛出异常（如 LLM 调用失败），验证 finally 块仍执行清理。
        """
        mock_conv = MagicMock()
        mock_conv.run = AsyncMock(side_effect=RuntimeError("LLM 失败"))
        agent._conversation = mock_conv
        agent._system_prompt = "test prompt"

        agent.build_run_spec = AsyncMock(return_value=self._spec())

        async def mock_ensure(ctx, system_prompt_override=None):
            return agent._conversation

        agent._ensure_conversation = mock_ensure
        agent.interpret_result = AsyncMock(
            return_value=AgentResult(success=False, data=None, error="不应走到这里"),
        )

        result = await agent.run({}, interaction_id="r10_b")

        # 异常被 Agent.run() 的 except 捕获并返回失败结果
        assert not result.success
        # finally 块仍应清空
        assert agent._conversation is None, "finally 应清空 _conversation"
        assert agent._system_prompt is None, "finally 应清空 _system_prompt"

    @pytest.mark.asyncio
    async def test_sa_two_runs_create_new_conversation(self, agent):
        """R10(c): 连续两次 SA run，第二次创建新 Conversation（不复用旧的）。

        每次 run 的 finally 销毁旧 conv；第二次 _ensure_conversation
        因 _conversation 为 None 创建新实例。
        """
        spec = self._spec()

        conv1 = MagicMock()
        conv1.run = AsyncMock(return_value=_make_conv_result(
            output_arguments={"summary": "first", "changed": False},
        ))
        conv2 = MagicMock()
        conv2.run = AsyncMock(return_value=_make_conv_result(
            output_arguments={"summary": "second", "changed": False},
        ))

        call_count = 0

        async def mock_ensure(ctx, system_prompt_override=None):
            nonlocal call_count
            call_count += 1
            conv = conv1 if call_count == 1 else conv2
            agent._conversation = conv
            return conv

        agent.build_run_spec = AsyncMock(return_value=spec)
        agent._ensure_conversation = mock_ensure
        agent.interpret_result = AsyncMock(
            return_value=AgentResult(success=True, data={}),
        )

        # 第一次 run
        r1 = await agent.run({}, interaction_id="r10_c_1")
        assert r1.success
        assert agent._conversation is None, "第一次 run 后应清空"

        # 第二次 run
        r2 = await agent.run({}, interaction_id="r10_c_2")
        assert r2.success
        assert agent._conversation is None, "第二次 run 后应清空"

        # 两次使用了不同的 Conversation 对象（非复用）
        assert conv1 is not conv2, "两次应使用不同 Conversation 对象"
        assert conv1.run.call_count == 1
        assert conv2.run.call_count == 1
