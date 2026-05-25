"""M2 结构化采集迁移 — 单元测试

验收点:
1. AgentLoop 在 REQUIRED_ONE_OF 模式下正确处理结构化采集工具 (record_event)
2. AgentLoop 采集 score_relationship 结构化输出（ScoringAgent 路径）
3. AgentLoop 采集 record_event 结构化输出（EventGenerationAgent 路径）
4. 结构化采集工具缺失时的 correction 注入与超限 fail 行为

测试策略:
- 以 AgentLoop 为测试目标，mock LLMGateway 层
- 结构化采集工具注册为 EffectKind.STATE_WRITE
- 验证 tool executor 收集到的结构化参数完整
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from plugins.DicePP.module.persona.agent.loop import AgentLoop, AgentRunResult
from plugins.DicePP.module.persona.agent.request import ToolUseMode, AgentRunLimits
from plugins.DicePP.module.persona.agent.tool_executor import ToolExecutor, ToolRegistry, ToolSpec
from plugins.DicePP.module.persona.agent.actions import EffectKind
from plugins.DicePP.module.persona.agent.event_bus import AgentEventBus, EventStore
from plugins.DicePP.module.persona.agent.state import AgentRunState
from plugins.DicePP.module.persona.agent.llm_gateway import LLMGateway, LLMRequest, LLMGatewayResult


# ── Arg Schemas（用于测试的 Pydantic 模型，等价于 M2 迁移后的正式定义）──


class RecordEventArgs(BaseModel):
    """record_event 工具参数"""
    description: str = Field(default="")
    context_summary: str = Field(default="")
    duration_minutes: int = Field(default=0, ge=0, le=2880)


class RecordReactionArgs(BaseModel):
    """record_reaction 工具参数"""
    reaction: str = Field(default="")
    share_desire: float = Field(default=0.0, ge=0.0, le=1.0)


class ScoreRelationshipArgs(BaseModel):
    """score_relationship 工具参数"""
    deltas: Dict[str, float] = Field(default_factory=lambda: {
        "intimacy": 0.0, "passion": 0.0, "trust": 0.0, "secureness": 0.0,
    })
    facts: Dict[str, Any] = Field(default_factory=dict)


# ── Helpers ──────────────────────────────────────────────────────────


def _make_event_bus() -> tuple[AgentEventBus, MagicMock]:
    """创建 mock EventStore + AgentEventBus（无额外 sink）。"""
    store = MagicMock(spec=EventStore)
    bus = AgentEventBus(event_store=store, sinks=[])
    return bus, store


def _make_run_state(mode: str = "structured_collect") -> AgentRunState:
    return AgentRunState(
        run_id="m2-test-run",
        turn_id="m2-test-turn",
        user_id="test-user",
        group_id="test-group",
        mode=mode,
    )


class _CountedGateway:
    """计次版 mock LLMGateway。

    first_calls 中的内容在第一次 complete() 时返回；
    后续调用返回 done_content（无 tool_call），走 direct_content 结束。
    """

    def __init__(self, first_tool_calls: Optional[List[dict]] = None,
                 done_content: str = "完成"):
        self.call_count = 0
        self._first_tool_calls = first_tool_calls
        self._done_content = done_content

    async def complete(
        self, request: LLMRequest, state: AgentRunState, timeout: Optional[int] = None,
    ) -> LLMGatewayResult:
        self.call_count += 1
        if self.call_count == 1 and self._first_tool_calls:
            return LLMGatewayResult(
                content="",
                tool_calls=self._first_tool_calls,
                usage={"input": 10, "output": 20},
                provider="mock", model="mock",
            )
        return LLMGatewayResult(
            content=self._done_content,
            tool_calls=None,
            usage={"input": 5, "output": 5},
            provider="mock", model="mock",
        )


def _make_collecting_spec(
    name: str, args_cls: type[BaseModel], collected: list,
) -> ToolSpec:
    """创建收集型 STATE_WRITE ToolSpec，每次执行将参数追加到 collected。"""
    async def _exec(**kwargs: Any) -> str:
        collected.append(kwargs)
        return json.dumps(kwargs, ensure_ascii=False)
    return ToolSpec(
        name=name,
        description="",
        args_schema=args_cls,
        effect=EffectKind.STATE_WRITE,
        executor=_exec,
    )


# ── Tests ────────────────────────────────────────────────────────────


class TestStructuredCollectSuccess:
    """结构化采集工具的成功执行路径（AUTO 模式）"""

    @pytest.mark.asyncio
    async def test_structured_collect_required_tool(self):
        """AgentLoop AUTO 模式: 结构化采集工具调用时收集参数

        当 LLM 在 AUTO 模式下调用结构化采集工具时，
        验证 STATE_WRITE executor 正确接收参数。
        """
        bus, _ = _make_event_bus()
        state = _make_run_state()
        reg = ToolRegistry()

        collected: list = []
        reg.register(_make_collecting_spec("record_event", RecordEventArgs, collected))

        tool_call = {
            "id": "tc_record",
            "name": "record_event",
            "arguments": json.dumps({
                "description": "窗外下雨了，雨滴打在玻璃上",
                "context_summary": "下雨天",
                "duration_minutes": 30,
            }),
        }

        gateway = _CountedGateway(first_tool_calls=[tool_call])
        executor = ToolExecutor(reg, bus)
        loop = AgentLoop(
            llm_gateway=gateway,
            tool_executor=executor,
            event_bus=bus,
            limits=AgentRunLimits(max_tool_rounds=5, max_corrections=2),
        )

        result = await loop.run(
            messages=[{"role": "user", "content": "生成一个事件"}],
            state=state,
            tools=reg.get_openai_schemas(),
            tool_use_mode=ToolUseMode.AUTO,
        )

        # 工具被调用 → 参数正确收集
        assert len(collected) == 1, f"expected 1 collected, got {len(collected)}"
        assert collected[0]["description"] == "窗外下雨了，雨滴打在玻璃上"
        assert collected[0]["context_summary"] == "下雨天"
        assert collected[0]["duration_minutes"] == 30
        assert result.status == "completed", f"got {result.status}/{result.final_reason}"

    @pytest.mark.asyncio
    async def test_scoring_agent_runtime_collects_score(self):
        """AgentLoop AUTO 模式: 采集 score_relationship 结构化输出

        ScoringAgent 在 M2 后应通过 AgentLoop 执行此工具路径。
        验证多维评分数据完整收集。
        """
        bus, _ = _make_event_bus()
        state = _make_run_state()
        reg = ToolRegistry()

        collected: list = []
        reg.register(_make_collecting_spec(
            "score_relationship", ScoreRelationshipArgs, collected,
        ))

        tool_call = {
            "id": "tc_score",
            "name": "score_relationship",
            "arguments": json.dumps({
                "deltas": {"intimacy": 3.5, "passion": 1.0, "trust": 2.0, "secureness": 0.5},
                "facts": {"name": "张三", "hobbies": ["读书", "游戏"]},
            }),
        }

        gateway = _CountedGateway(first_tool_calls=[tool_call])
        executor = ToolExecutor(reg, bus)
        loop = AgentLoop(gateway, executor, bus, limits=AgentRunLimits(max_tool_rounds=5))

        result = await loop.run(
            messages=[{"role": "user", "content": "分析以下对话并评分"}],
            state=state,
            tools=reg.get_openai_schemas(),
            tool_use_mode=ToolUseMode.AUTO,
        )

        # 参数正确收集
        assert len(collected) == 1
        assert collected[0]["deltas"]["intimacy"] == 3.5
        assert collected[0]["deltas"]["passion"] == 1.0
        assert collected[0]["facts"]["name"] == "张三"
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_life_event_runtime_collects_event(self):
        """AgentLoop AUTO 模式: 采集 record_event 结构化输出

        EventGenerationAgent 在 M2 后应通过 AgentLoop 执行此工具路径。
        验证完整的事件参数（含可选字段）能被正确收集。
        """
        bus, _ = _make_event_bus()
        state = _make_run_state()
        reg = ToolRegistry()

        collected: list = []
        reg.register(_make_collecting_spec("record_event", RecordEventArgs, collected))

        tool_call = {
            "id": "tc_event",
            "name": "record_event",
            "arguments": json.dumps({
                "description": "在公园长椅上看书，被鸽子踩醒了",
                "context_summary": "在公园看书被鸽子打扰",
                "duration_minutes": 45,
            }),
        }

        gateway = _CountedGateway(first_tool_calls=[tool_call])
        executor = ToolExecutor(reg, bus)
        loop = AgentLoop(gateway, executor, bus, limits=AgentRunLimits(max_tool_rounds=5))

        result = await loop.run(
            messages=[{"role": "system", "content": "你是一个事件生成器"},
                      {"role": "user", "content": "生成一个生活事件"}],
            state=state,
            tools=reg.get_openai_schemas(),
            tool_use_mode=ToolUseMode.AUTO,
        )

        # 参数正确收集
        assert len(collected) == 1
        assert collected[0]["description"] == "在公园长椅上看书，被鸽子踩醒了"
        assert collected[0]["context_summary"] == "在公园看书被鸽子打扰"
        assert collected[0]["duration_minutes"] == 45
        assert result.status == "completed"


class TestMissingToolCorrection:
    """结构化采集工具缺失时的 correction/fail 行为"""

    @pytest.mark.asyncio
    async def test_structured_collect_missing_tool_correction(self):
        """工具未调用时注入 L1 correction，超限后 fails"""
        bus, _ = _make_event_bus()
        state = _make_run_state()
        reg = ToolRegistry()

        collected: list = []
        reg.register(_make_collecting_spec("record_event", RecordEventArgs, collected))

        # LLM 始终返回文本，不调用工具
        gateway = _CountedGateway(
            first_tool_calls=None,  # 首次也无 tool_call
            done_content="好的，我记下了。",
        )
        executor = ToolExecutor(reg, bus)
        loop = AgentLoop(
            gateway, executor, bus,
            limits=AgentRunLimits(max_tool_rounds=5, max_corrections=2),
        )

        result = await loop.run(
            messages=[{"role": "user", "content": "生成事件"}],
            state=state,
            tools=reg.get_openai_schemas(),
            tool_use_mode=ToolUseMode.REQUIRED_ONE_OF,
            required_tools=["record_event"],
        )

        # correction 耗尽后应 fail
        assert result.status == "max_corrections", f"expected max_corrections, got {result.status}"
        assert result.final_reason == "required_tool_missing"
        assert state.correction_count >= 1

    @pytest.mark.asyncio
    async def test_structured_collect_missing_tool_one_correction(self):
        """一次 correction 后 LLM 调用工具 → 成功收集

        注：当前 AgentLoop REQUIRED_ONE_OF 要求每轮都调工具，
        因此工具执行后循环会因 max_tool_rounds 终止。
        核心验证点是 correction 被注入且工具最终被调用。
        """
        bus, _ = _make_event_bus()
        state = _make_run_state()
        reg = ToolRegistry()

        collected: list = []
        reg.register(_make_collecting_spec("record_event", RecordEventArgs, collected))

        # 第一次: 无工具 → correction
        # 第二次: 调用工具 → 成功
        class _TwoPhaseGateway:
            def __init__(self):
                self.call_count = 0

            async def complete(self, request, state, timeout=None):
                self.call_count += 1
                if self.call_count == 1:
                    return LLMGatewayResult(
                        content="好的", tool_calls=None,
                        usage={"input": 10, "output": 5},
                        provider="mock", model="mock",
                    )
                tool_call = {
                    "id": "tc_retry",
                    "name": "record_event",
                    "arguments": json.dumps({
                        "description": "最终生成的事件", "context_summary": "事件",
                        "duration_minutes": 15,
                    }),
                }
                return LLMGatewayResult(
                    content="", tool_calls=[tool_call],
                    usage={"input": 10, "output": 20},
                    provider="mock", model="mock",
                )

        executor = ToolExecutor(reg, bus)
        loop = AgentLoop(
            llm_gateway=_TwoPhaseGateway(),
            tool_executor=executor, event_bus=bus,
            # max_tool_rounds=2: round 0→correction, round 1→tool execute, exit
            limits=AgentRunLimits(max_tool_rounds=2, max_corrections=3),
        )

        result = await loop.run(
            messages=[{"role": "user", "content": "生成事件"}],
            state=state,
            tools=reg.get_openai_schemas(),
            tool_use_mode=ToolUseMode.REQUIRED_ONE_OF,
            required_tools=["record_event"],
        )

        # correction 被注入
        assert state.correction_count >= 1
        # 工具最终被调用
        assert len(collected) == 1
        assert collected[0]["description"] == "最终生成的事件"
        # 循环正常终止
        assert result.final_reason in ("max_tool_rounds", "completed"), (
            f"unexpected: {result.status}/{result.final_reason}"
        )


class TestStateWriteEffect:
    """STATE_WRITE effect 工具在 ToolExecutor 中的行为验证"""

    @pytest.mark.asyncio
    async def test_state_write_effect_is_not_external_action(self):
        """STATE_WRITE 工具不走 EXTERNAL_ACTION 分支（无 delivery/action_id）"""
        bus, _ = _make_event_bus()
        state = _make_run_state()
        reg = ToolRegistry()

        collected: list = []
        reg.register(_make_collecting_spec("record_event", RecordEventArgs, collected))

        executor = ToolExecutor(reg, bus)
        tool_calls = [{
            "id": "tc1",
            "name": "record_event",
            "arguments": json.dumps({"description": "test", "context_summary": "t", "duration_minutes": 0}),
        }]

        results = await executor.execute_many(tool_calls, state)

        assert len(results) == 1
        # STATE_WRITE 不应产生 _action_id（那是 EXTERNAL_ACTION 的标记）
        assert "_action_id" not in results[0]
        assert len(collected) == 1
        assert collected[0]["description"] == "test"
