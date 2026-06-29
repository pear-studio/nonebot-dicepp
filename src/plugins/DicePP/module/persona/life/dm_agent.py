"""
DM Agent — 世界观裁决者

负责生成客观生活事件（System Agent 角色）。
Phase 1: 一次 LLM 调用，收集 record_event，返回 EventGenerationResult。
"""
from typing import Any, Optional
import json
from utils.logger import logger
from ..data.models import DMState
from ..data.store import PersonaDataStore
from ..llm.router import LLMRouter
from .agent import Agent
from .types import AgentResult, EventGenerationResult

# 状态刻度定义（注入 DM system prompt）
_STATE_SCALE_PROMPT = """体力（0-100）影响角色能做什么：
  80-100 → 精力充沛，可承担消耗较大的活动
  60-79  → 精力正常，日常活动不受限制
  40-59  → 可以外出，但会自然回避高强度劳作
  20-39  → 精力有限，倾向低消耗室内活动，回避长途跋涉
  0-19   → 已无力消耗，应选择就近休息、静养、缓慢整理等恢复性行为

心情（0-100）：
  80-100 → 愉悦，对周围充满兴趣
  60-79  → 平稳，日常交流自然
  40-59  → 情绪低落
  20-39  → 心情糟糕，易烦躁消沉
  0-19   → 崩溃绝望

健康（0-100）：
  80-100 → 身体健康，行动自如
  60-79  → 无病痛，状态正常
  40-59  → 轻微不适
  20-39  → 生病中，行动受限
  0-19   → 重病缠身

单事件状态变化幅度 ≤ ±20"""


class DMAgent(Agent):
    """DM Agent — 世界观裁决者"""

    name = "DM"
    role = "世界观裁决者"
    state_model = DMState
    tools = ["roll_dice", "record_event", "read_events", "search_events"]

    def __init__(
        self,
        store: PersonaDataStore,
        router: LLMRouter,
        config=None,
        tool_registry=None,
    ):
        super().__init__(store, router, config, tool_registry=tool_registry)

    async def load_state(self) -> DMState:
        """从 store 加载 DM 工作状态"""
        return await self.store.get_dm_state()

    async def save_state(self, state: DMState) -> None:
        """持久化 DM 工作状态"""
        await self.store.update_dm_state(state)

    def build_system_prompt(self, state: DMState, context: dict) -> str:
        """构建 DM 系统提示词

        稳定部分：DM 身份 + 裁决规则 + 状态刻度
        动态部分：state.scratchpad（DM 备忘）+ context（角色信息/场景/状态）
        """
        character_name = context.get("character_name", "")
        character_description = context.get("character_description", "")
        world = context.get("world", "现代日常世界")
        scenario = context.get("scenario", "")
        state_text = context.get("state_text", "")
        slot_type = context.get("slot_type", "system")
        chain_depth = context.get("chain_depth", 0)

        dm_context = ""
        if state.scratchpad and state.scratchpad.strip():
            dm_context = f"\nDM 备忘:\n{state.scratchpad}\n"

        scenario_section = f"场景:\n{scenario}\n" if scenario else ""

        system_prompt = f"""你是世界观设定专家。基于以下信息生成一个生活事件。

角色:
{character_name} - {character_description or "普通人"}

世界观:
{world or "现代日常世界"}

{scenario_section}角色当前状态:
{state_text}
{dm_context}{_STATE_SCALE_PROMPT}
{self._slot_type_hint(slot_type)}
生成要求:
1. 以第三人称客观叙述描述发生了什么（不携带主观情绪）
2. 只记录可观察的行为和状态（动作、位置、物品、身体状态）
3. 不包含心理活动、情绪评价、内心独白
4. 不使用"觉得""认为""感到"等主观动词
5. description 自然叙事，不强制字数上限，但保持简洁
6. context_summary 为事件摘要，30-60字，仅包含关键事实（谁、在哪、做了什么、结果）
7. 符合世界观和场景设定{'，场景中的具体动作是参考而非约束' if chain_depth == 0 else '，场景中描述的当前动作是强制上下文，不要重复其中已经完成的事，只描述接下来新发生的事'}
8. 避免与今天已发生事件在具体内容上高度重复，优先描述不同的事
9. 同时给出该事件对角色体力/心情/健康的影响（delta，可选整数，范围-20~+20）

你必须通过调用 record_event 工具来输出结果。"""

        return system_prompt

    def _build_user_prompt(self, context: dict) -> str:
        """构建用户提示词"""
        diary_context = context.get("diary_context", "")
        events_context = context.get("events_context", "")
        intention_text = context.get("intention_text", "")
        now_str = context.get("now_str", "??:??")
        date_str = context.get("date_str", "")
        chain_depth = context.get("chain_depth", 0)

        if chain_depth == 0:
            task_hint = "请生成一个符合世界观的生活事件"
        else:
            task_hint = "请描述角色在当前场景中接下来做了什么"

        user_prompt = (
            f"当前日期: {date_str}\n当前时间: {now_str}"
            f"{intention_text}{diary_context}{events_context}"
            f"\n\n{task_hint}，并通过 record_event 工具记录:"
        )
        return user_prompt

    def _get_openai_tools(self) -> list:
        """返回 record_event 工具"""
        from ..tools.collecting import RECORD_EVENT_TOOL

        return [RECORD_EVENT_TOOL.to_openai_format()]

    @staticmethod
    def _slot_type_hint(slot_type: str) -> str:
        """根据槽位类型返回场景标注文本"""
        if slot_type == "wake_up":
            return "\n当前事件类型: wake_up（角色刚刚醒来）\n"
        elif slot_type == "good_night":
            return "\n当前事件类型: good_night（角色准备入睡）\n"
        return ""

    async def run(self, context: dict) -> AgentResult:
        """DM 生成生活事件"""
        self._cached_state = await self.load_state()
        try:
            scratchpad = context.get("_scratchpad")
            if scratchpad is not None and scratchpad != self._cached_state.scratchpad:
                self._cached_state.scratchpad = scratchpad
                await self.save_state(self._cached_state)

            # 提前构建 system_prompt 并缓存，避免在基类 run() 和 system_prompt_digest
            # 中重复构建（每次构建 ~50 行字符串拼接）
            self._cached_system_prompt = self.build_system_prompt(
                self._cached_state, context
            )

            result = await super().run(context)
            if not result.success:
                logger.warning("DM 事件生成失败: LLM 未调用工具")
                return AgentResult(
                    success=False,
                    data=EventGenerationResult(
                        description="我正在房间里休息。",
                        context_summary="在房间里休息",
                    ),
                    error="LLM 未调用工具",
                )

            collected = result.data
            if not collected or not isinstance(collected, list):
                return AgentResult(
                    success=False,
                    data=EventGenerationResult(
                        description="我正在房间里休息。",
                        context_summary="在房间里休息",
                    ),
                    error="LLM 返回空数据",
                )

            try:
                args = collected[0]
                description = str(args.get("description", "")).strip().strip('"').strip("'")
                if not description:
                    description = "我正在房间里休息。"

                duration_minutes = max(0, min(2880, int(args.get("duration_minutes", 0))))

                context_summary = (
                    str(args.get("context_summary", "")).strip().strip('"').strip("'")
                )
                if not context_summary:
                    context_summary = description[:60]

                def _parse_delta(val) -> Optional[int]:
                    if val is None:
                        return None
                    try:
                        return max(-20, min(20, int(val)))
                    except (TypeError, ValueError):
                        return None

                energy_delta = _parse_delta(args.get("energy_delta"))
                mood_delta = _parse_delta(args.get("mood_delta"))
                health_delta = _parse_delta(args.get("health_delta"))

                return AgentResult(
                    success=True,
                    data=EventGenerationResult(
                        description=description,
                        context_summary=context_summary,
                        duration_minutes=duration_minutes,
                        energy_delta=energy_delta,
                        mood_delta=mood_delta,
                        health_delta=health_delta,
                        raw_response=json.dumps(args, ensure_ascii=False),
                        system_prompt_digest=self._cached_system_prompt,
                    ),
                    raw_response=json.dumps(args, ensure_ascii=False),
                )

            except Exception as e:
                logger.error(f"DM 事件生成解析失败: {e}", exc_info=True)
                return AgentResult(
                    success=False,
                    data=EventGenerationResult(
                        description="我正在房间里休息。",
                        context_summary="在房间里休息",
                        duration_minutes=0,
                        energy_delta=0,
                        mood_delta=None,
                        health_delta=None,
                    ),
                    error=str(e),
                )
        finally:
            self._cached_state = None
            self._cached_system_prompt = None
