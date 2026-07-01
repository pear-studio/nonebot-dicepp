"""
DM Agent — 世界观裁决者

负责生成客观生活事件（System Agent 角色）。
Phase 2: 通过 `say` 工具与角色对话，DM 裁决角色行动，D20 判定规则。
Story Deck: chain_depth==0 时自动注入匹配的叙事条目。
"""
from typing import Any, Optional
import json
from utils.logger import logger
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

# D20 裁决规则（注入 DM system prompt）
_D20_RULING_PROMPT = """D20 判定规则 — 当角色采取一个有风险或有难度系数的行动时，使用 roll_dice 工具判定：

DC 5（简单）：d20 ≥ 5 成功
DC 10（一般）：d20 ≥ 10 成功
DC 15（困难）：d20 ≥ 15 成功
DC 20（极高）：d20 ≥ 20 成功

大成功：d20 = 20（完美结果，额外的正面效果）
大失败：d20 = 1（灾难性结果，额外的负面效果）
失败：d20 < DC
成功：d20 ≥ DC

根据角色当前状态、行动描述和场景上下文判断 DC 值。
你可以自主决定是否需要判定——日常事务不需要判定，只有有风险、有难度或被干扰的情况才需要。"""

# Story Deck 注入前缀
_STORY_DECK_INJECTION_PREFIX = "[故事提示 (story_deck)]"


class DMAgent(Agent):
    """DM Agent — 世界观裁决者"""

    name = "DM"
    role = "世界观裁决者"
    state_model = None  # DM 不再需要持久状态（DMState 表已删除）
    tools = ["roll_dice", "say", "read_events", "search_events"]

    def __init__(
        self,
        store: PersonaDataStore,
        router: LLMRouter,
        config=None,
        tool_registry=None,
    ):
        super().__init__(store, router, config, tool_registry=tool_registry)

    def build_system_prompt(self, state: None, context: dict) -> str:
        """构建 DM 系统提示词

        稳定部分：DM 身份 + 裁决规则 + D20 判定 + 状态刻度
        动态部分：context（角色信息/场景/状态）
        system_prompt 在 Conversation 生命周期内只构建一次（保持前缀稳定）。
        state 参数为 None（DM 不再持有持久状态）。
        """
        character_name = context.get("character_name", "")
        character_description = context.get("character_description", "")
        world = context.get("world", "现代日常世界")
        scenario = context.get("scenario", "")
        state_text = context.get("state_text", "")
        slot_type = context.get("slot_type", "system")

        scenario_section = f"场景:\n{scenario}\n" if scenario else ""

        system_prompt = f"""你是 TRPG 主持人（DM），负责裁决角色的行动并叙述结果。

角色:
{character_name} - {character_description or "普通人"}

世界观:
{world or "现代日常世界"}

{scenario_section}角色当前状态:
{state_text}

{_STATE_SCALE_PROMPT}

{_D20_RULING_PROMPT}
{self._slot_type_hint(slot_type)}
叙述要求:
1. 以第三人称客观叙述描述发生了什么（不携带主观情绪）
2. 只记录可观察的行为和状态（动作、位置、物品、身体状态）
3. 不包含心理活动、情绪评价、内心独白
4. 不使用"觉得""认为""感到"等主观动词
5. 内容自然叙事，不强制字数上限，但保持简洁
6. context_summary 为事件摘要，不超过60字，仅包含关键事实（谁、在哪、做了什么、结果）
7. 符合世界观和场景设定，场景中的具体动作是参考而非约束
8. 避免与今天已发生事件在具体内容上高度重复，优先描述不同的事
9. 同时给出该事件对角色体力/心情/健康的影响（delta，可选整数，范围-20~+20）

你必须通过调用 say 工具来输出结果。"""

        return system_prompt

    def _build_user_prompt(self, context: dict) -> str:
        """构建用户提示词

        depth == 0: 场景生成（首次呈现场景）
        depth >= 1: 裁决角色企图（包含角色的 follow_up_text）
        """
        diary_context = context.get("diary_context", "")
        events_context = context.get("events_context", "")
        now_str = context.get("now_str", "??:??")
        date_str = context.get("date_str", "")
        chain_depth = context.get("chain_depth", 0)
        follow_up_text = context.get("follow_up_text", "")

        if chain_depth == 0:
            task_hint = "请生成一个符合世界观的生活事件"
        else:
            task_hint = (
                f"角色想要：{follow_up_text}\n\n"
                f"请评估这个行动的难度，必要时调用 roll_dice 判定，通过 say 叙述结果。"
            )

        user_prompt = (
            f"当前日期: {date_str}\n当前时间: {now_str}"
            f"{diary_context}{events_context}"
            f"\n\n{task_hint}"
        )
        return user_prompt

    def _get_openai_tools(self) -> list:
        """返回 say 工具（DM 版本 description）"""
        from ..tools.collecting import SAY_TOOL_DM

        return [SAY_TOOL_DM.to_openai_format()]

    def _build_extra_registry(self) -> Any:
        """构建只读工具注册表，额外注册 search_story_deck"""
        registry = super()._build_extra_registry()
        if registry is None:
            from ..agent.tool_executor import ToolRegistry as NewTR
            registry = NewTR()
        # 注册 search_story_deck（DM 只读查询 story_deck）
        try:
            from ..tools.story_deck import register_search_story_deck
            register_search_story_deck(registry, self.store)
        except Exception:
            logger.warning("search_story_deck 注册失败，DM 叙事条目查询将不可用", exc_info=True)
        return registry

    @staticmethod
    def _slot_type_hint(slot_type: str) -> str:
        """根据槽位类型返回场景标注文本"""
        if slot_type == "wake_up":
            return "\n当前事件类型: wake_up（角色刚刚醒来）\n"
        elif slot_type == "good_night":
            return "\n当前事件类型: good_night（角色准备入睡）\n"
        return ""

    # ── Story Deck 注入逻辑 ────────────────────────────────────

    async def _build_story_deck_injection(
        self, context: dict
    ) -> Optional[str]:
        """构建 story_deck 注入文本。

        仅在 chain_depth==0 时调用。匹配 story_deck entries 到当前上下文，
        排序、去重、裁剪后返回注入文本。

        Returns:
            注入文本或 None（无匹配条目时）
        """
        chain_depth = context.get("chain_depth", 0)
        if chain_depth != 0:
            return None

        # 构建匹配文本
        follow_up_text = context.get("follow_up_text", "")
        events_context = context.get("events_context", "")
        match_text = f"{follow_up_text}\n{events_context}"

        if not match_text.strip():
            return None

        # 获取所有条目
        all_entries = await self.store.list_story_deck_entries(limit=200)
        if not all_entries:
            return None

        # 匹配：entry.key 在匹配文本中做子串匹配
        matched = []
        for entry in all_entries:
            if entry.key and entry.key in match_text:
                matched.append(entry)

        if not matched:
            return None

        # 排序：plot > entity > detail
        type_priority = {"plot": 0, "entity": 1, "detail": 2}
        matched.sort(key=lambda e: type_priority.get(e.type, 99))

        # 去重：通过 Conversation 公共方法查询已注入的 key（不直接访问 _messages）
        injected_keys: set[str] = set()
        if self._conversation is not None:
            injected_keys = self._conversation.get_keys_by_message_prefix(_STORY_DECK_INJECTION_PREFIX)

        # 裁剪：≤ max_injection
        max_injection = getattr(self.config, "story_deck_max_injection", 3) if self.config else 3
        selected = []
        for entry in matched:
            if entry.key in injected_keys:
                continue
            selected.append(entry)
            if len(selected) >= max_injection:
                break

        if not selected:
            return None

        # 格式化注入文本
        lines = [_STORY_DECK_INJECTION_PREFIX]
        for entry in selected:
            content_preview = entry.content
            from ..tools.story_deck import format_injection_line
            lines.append(format_injection_line(entry.key, entry.type, content_preview))

        return "\n".join(lines)

    # ── 核心执行 ──────────────────────────────────────────────

    async def run(self, context: dict) -> AgentResult:
        """DM 生成生活事件（通过 say 工具）

        chain_depth==0 时：先注入 story_deck 叙事条目，再执行正常事件生成。
        """
        chain_depth = context.get("chain_depth", 0)

        # 提前构建 system_prompt 并缓存，避免在基类 run() 和 system_prompt_digest
        # 中重复构建（每次构建 ~50 行字符串拼接）
        self._cached_system_prompt = self.build_system_prompt(None, context)

        # chain_depth==0 时：注入 story_deck 条目
        # 契约假设（依赖基类 Agent 行为）：
        #   (1) _ensure_conversation 在 _conversation 已设置时幂等 no-op
        #   (2) super().run() 中再次调用 _ensure_conversation 不覆盖已注入消息
        #   (3) _cached_system_prompt 在两次调用间不变
        # 若上述任一假设被破坏，改为直接调用 _run_with_conv 替代 super().run()
        if chain_depth == 0:
            try:
                injection_text = await self._build_story_deck_injection(context)
                if injection_text:
                    # 确保 Conversation 存在
                    conv = await self._ensure_conversation(context, system_prompt_override=self._cached_system_prompt)
                    await conv.pull_notifications()
                    conv.add_user(injection_text)
                    logger.debug(f"DM story_deck 注入: {len(injection_text)} 字")
            except Exception:
                logger.warning("story_deck 注入失败，继续正常事件生成", exc_info=True)

        try:
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
                # 从 SayArgs 字段构建 EventGenerationResult
                description = str(args.get("content", "")).strip().strip('"').strip("'")
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
