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
    ):
        super().__init__(store, router, config)

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
        state_text = context.get("state_text", "")
        slot_type = context.get("slot_type", "system")

        system_prompt = f"""你是 TRPG 主持人（DM），负责根据故事脉络和时间推进场景。

角色:
{character_name} - {character_description or "普通人"}

世界观:
{world or "现代日常世界"}

角色当前状态:
{state_text}

{_STATE_SCALE_PROMPT}

{_D20_RULING_PROMPT}
{self._slot_type_hint(slot_type)}
核心原则:
你是世界的叙述者，不是角色的操控者。

你的职责:
1. 根据故事发展和当前时间，描述角色周围正在发生的事
   - 环境变化（天气、光线、气味、声响）
   - NPC 的行为和对话
   - 之前事件的自然延续和后果浮现
   - 日常节奏中的新变化或异常现象
2. 阅读角色的发言，判断需要你做什么
   - 角色做了有风险/难度的行动 → 暗骰裁决（不展示 DC 和掷骰结果），叙述结果
   - 角色在观察/回忆/嗅闻/辨别某事物 → 可直接补充角色应知的合理细节（知识、感知）
   - 角色只是表达感受或做日常事务 → 可自然收束（设置 want_to_end=true），也可引入推动故事的新信息
3. 自然收束场景
   - 当前场景已到自然停顿点时 → 设置 want_to_end=true 提议结束
   - 收到角色的结束提议且你也同意时 → 调用 say 并设置 want_to_end=true
   - 如果你有重要的新信息要补充，可以继续 say（会覆盖结束提议）

不要做的事:
- 不要替角色做决定：不写角色的自主行动（走向哪、拿起什么、说什么话、做什么事）
- 不要包含心理活动、情绪评价、内心独白
- 不使用"觉得""认为""感到"等主观动词来描述角色
- 不要跳时间，除非角色明确表示进行长时间重复劳动
- 避免与今天已发生事件在具体内容上高度重复

叙述要求:
1. 第三人称客观叙述
2. context_summary 为事件摘要，不超过 60 字
3. 给出该事件对角色体力/心情/健康的影响（delta，可选，-20~+20）
4. 必须通过调用 say 工具输出。同意对方的结束提议时，调用 say 并设置 want_to_end=true。"""

        return system_prompt

    def _build_user_prompt(self, context: dict) -> str:
        """构建用户提示词

        depth == 0: 呈现角色周围的情境（不替角色行动）
        depth >= 1: 裁决/补充角色的发言
        """
        diary_context = context.get("diary_context", "")
        events_context = context.get("events_context", "")
        now_str = context.get("now_str", "??:??")
        date_str = context.get("date_str", "")
        chain_depth = context.get("chain_depth", 0)
        follow_up_text = context.get("follow_up_text", "")
        init_scenario_text = context.get("init_scenario_text", "")
        char_want_to_end = context.get("char_want_to_end", False)

        # 附加场景上下文（首次启动或自发事件路径注入）
        init_section = f"\n\n【场景】\n{init_scenario_text}" if init_scenario_text else ""

        if chain_depth == 0:
            task_hint = (
                "请根据以上故事脉络、时间和角色状态，描述角色此刻周围正在发生的事。"
                "只描述外部世界的变化——不要替角色做决定。"
            )
        else:
            task_hint = (
                f"角色说/做了：{follow_up_text}\n\n"
                f"请阅读角色的发言：\n"
                f"- 如果有需要裁决的行动（风险/难度），暗骰裁决后叙述结果（不展示DC和骰值）\n"
                f"- 如果角色在观察/回忆/辨别，可直接补充合理细节\n"
                f"- 如果角色只是日常表达、没有需要你介入的内容，可以自然收束（设置 want_to_end=true）\n"
                f"- 如果角色已提议结束且你也同意，调用 say 并设置 want_to_end=true"
            )

        user_prompt = (
            f"当前日期: {date_str}\n当前时间: {now_str}"
            f"{init_section}"
            f"{diary_context}{events_context}"
            f"\n\n{task_hint}"
        )

        # 注入 want_to_end 信号提示
        if char_want_to_end:
            user_prompt += (
                "\n\n[提示] 角色认为当前场景可以收束了。"
                "如果你也同意，调用 say 并设置 want_to_end=true。"
                "如果你还有需要补充的信息，正常 say 即可（会覆盖结束提议）。"
            )

        return user_prompt

    def _get_openai_tools(self) -> list:
        """返回 say 工具（DM 版本 description）"""
        from ..tools.collecting import SAY_TOOL_DM

        return [
            SAY_TOOL_DM.to_openai_format(),
        ]

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

    async def build_run_spec(self, context: dict) -> "AgentRunSpec":
        """T6: 直接构建 AgentRunSpec — say 作为 OutputSpec，普通工具进 ToolKit。"""
        from ..agent.runtime_types import (
            AgentRunSpec, LoopLimits, ToolKit, OutputSpec,
        )
        from ..tools.collecting import SayArgs, SAY_TOOL_DM
        from ..tools.roll_dice import build_roll_dice_tool
        from ..tools.read_events import build_read_events_tool
        from ..tools.search_events import build_search_events_tool
        from ..tools.story_deck import build_search_story_deck_tool

        system_prompt = (
            self._cached_system_prompt
            if self._cached_system_prompt is not None
            else self.build_system_prompt(None, context)
        )
        user_prompt = self._build_user_prompt(context)

        # T6: 直接构建 ToolKit
        timezone = getattr(self.config, "timezone", "Asia/Shanghai") if self.config else "Asia/Shanghai"
        toolkit = ToolKit(tools={
            "roll_dice": build_roll_dice_tool(),
            "read_events": build_read_events_tool(self.store, timezone),
            "search_events": build_search_events_tool(self.store),
            "search_story_deck": build_search_story_deck_tool(self.store),
        })

        # say 作为 OutputSpec，结束语义由 say.want_to_end 表达
        output_spec = OutputSpec(
            name="say",
            description=SAY_TOOL_DM.description,
            args_schema=SayArgs,
        )

        return AgentRunSpec(
            system_prompt=system_prompt,
            user_input=user_prompt,
            tools=toolkit,
            output=output_spec,
            selection=self._get_selection_policy(),
            limits=LoopLimits(max_rounds=self._max_rounds),
            run_tag="dm_event",
        )

    async def interpret_result(
        self, result: "ConversationRunResult", context: dict
    ) -> AgentResult:
        """T4: 从 output_arguments 读取 say 结构化输出，不再从 messages 反解析。"""
        args = result.output_arguments

        # 优先从 output_arguments 获取 say 内容
        if args is not None:
            if "content" in args:
                return self._build_event_result(args)
            # output_arguments 存在但缺少 content：退化为默认描述
            # （模型调用了 say 但参数不全，或调用了其他工具体现在 output 中）
            return self._build_event_result({})

        # LLM 未调用工具（output_arguments 为 None）
        logger.warning("DM 事件生成失败: LLM 未调用工具")
        return AgentResult(
            success=False,
            data=EventGenerationResult(
                description="我正在房间里休息。",
                context_summary="在房间里休息",
            ),
            error="LLM 未调用工具",
        )

    def _build_event_result(self, args: dict) -> AgentResult:
        """从 say 结构化参数构建 EventGenerationResult。"""
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

        return AgentResult(
            success=True,
            data=EventGenerationResult(
                description=description,
                context_summary=context_summary,
                duration_minutes=duration_minutes,
                energy_delta=_parse_delta(args.get("energy_delta")),
                mood_delta=_parse_delta(args.get("mood_delta")),
                health_delta=_parse_delta(args.get("health_delta")),
                want_to_end=bool(args.get("want_to_end", False)),
                raw_response=json.dumps(args, ensure_ascii=False),
                system_prompt_digest=self._cached_system_prompt,
            ),
            raw_response=json.dumps(args, ensure_ascii=False),
        )

    async def run(
        self, context: dict, *, interaction_id: str,
    ) -> AgentResult:
        """DM 生成生活事件（通过 say 工具）

        chain_depth==0 时：先注入 story_deck 叙事条目，再执行正常事件生成。
        通过 build_run_spec() + interpret_result() 模板方法，走 AgentRunSpec 新路径。
        """
        chain_depth = context.get("chain_depth", 0)

        # 提前构建 system_prompt 并缓存
        self._cached_system_prompt = self.build_system_prompt(None, context)

        # chain_depth==0 时：注入 story_deck 条目
        if chain_depth == 0:
            try:
                injection_text = await self._build_story_deck_injection(context)
                if injection_text:
                    conv = await self._ensure_conversation(
                        context, system_prompt_override=self._cached_system_prompt,
                    )
                    n, c = await conv.fetch_notifications()
                    conv.apply_notifications(n, c)
                    conv.add_message("user", injection_text)
                    logger.debug(f"DM story_deck 注入: {len(injection_text)} 字")
            except Exception:
                logger.warning("story_deck 注入失败，继续正常事件生成", exc_info=True)

        try:
            return await super().run(context, interaction_id=interaction_id)
        finally:
            self._cached_state = None
            self._cached_system_prompt = None
