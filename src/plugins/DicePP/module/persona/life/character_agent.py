"""
Character Agent — 角色第一人称

合并 reaction + diary + share + opening 四个模式。
Phase 1: 真实 LLM 调用，根据 context.mode 使用不同 prompt 模板。
"""
import asyncio
from typing import Any, List, Optional, TYPE_CHECKING
import json
from utils.logger import logger
from ..data.models import CharacterState
from ..data.store import PersonaDataStore
from ..llm.router import LLMRouter, ServiceUnavailableError
from ..llm.selection import EVENT_GEN, DIARY, SUMMARIZE
from ..tools.collecting import (
    RECORD_REACTION_TOOL,
    RECORD_DIARY_ENTRY_TOOL,
    RECORD_SHARE_MESSAGE_TOOL,
)
from utils.time import format_timestamp, format_relative_time, wall_now
from .agent import Agent
from .types import AgentResult, EventReactionResult

if TYPE_CHECKING:
    from core.config.pydantic_models import PersonaConfig


# ── 默认 few-shot 示例（系统默认）
_DEFAULT_SHARE_EXAMPLES: List[str] = [
    "场景：午后在公园长椅上打盹，被鸽子踩醒了\n"
    '消息："刚才在公园长椅上眯了一会儿，被鸽子踩醒了。你们那边公园鸽子多吗？"\n'
    "→ 陈述事实 + 自然收尾 + 礼貌关联，无生硬开场，无角色名",
    "场景：午后在公园长椅上打盹，被鸽子踩醒了\n"
    '消息："在公园睡觉被鸽子踩脸了，它把我当地板。这事必须让你知道，不能只有我一个人丢脸。"\n'
    "→ 自嘲 + 强制分享感 + 亲密调侃，符合高亲密度语气",
    '消息："你好~ {{character_name}}刚才在公园被鸽子踩醒了"\n'
    "→ 坏：生硬开场（\"你好~\"）+ 出现角色名（\"{{character_name}}\"）",
    '消息："{{character_name}}低头看着鸽子，叹了口气"\n'
    '→ 坏：第三人称动作描写（"低头"）+ 出现角色名（"{{character_name}}"）',
]


class CharacterAgent(Agent):
    """Character Agent — 角色第一人称"""

    name = "Character"
    role = "角色第一人称"
    state_model = CharacterState
    tools = ["record_reaction"]

    def __init__(
        self,
        store: PersonaDataStore,
        router: LLMRouter,
        config=None,
        tool_registry=None,
    ):
        super().__init__(store, router, config, tool_registry=tool_registry)
        self._current_mode = "reaction"  # 默认模式，在 react/diary/share/opening 入口前更新

    async def load_state(self) -> CharacterState:
        """从 store 加载角色状态"""
        return await self.store.get_character_state()

    async def save_state(self, state: CharacterState) -> None:
        """持久化角色状态"""
        await self.store.update_character_state(state)

    @staticmethod
    def _format_state_prompt(
        energy: Optional[int],
        mood: Optional[int],
        health: Optional[int],
        intention: Optional[str] = None,
    ) -> str:
        """构建状态 prompt 片段"""
        lines = []
        if energy is not None:
            lines.append(f"体力: {energy}/100")
        if mood is not None:
            lines.append(f"心情: {mood}/100")
        if health is not None:
            lines.append(f"健康: {health}/100")
        if intention is not None:
            lines.append(f"当前意向: {intention}")
        return "\n".join(lines) if lines else "无记录"

    def build_system_prompt(self, state: CharacterState, context: dict) -> str:
        """根据 context.mode 选择 prompt 模板

        mode: "reaction" | "diary" | "share" | "opening"
        """
        mode = context.get("mode", "reaction")
        if mode == "reaction":
            return self._build_reaction_prompt(state, context)
        elif mode == "diary":
            return self._build_diary_prompt(state, context)
        elif mode == "share":
            return self._build_share_prompt(state, context)
        elif mode == "opening":
            return self._build_opening_prompt(state, context)
        else:
            return self._build_reaction_prompt(state, context)

    # ── Reaction Prompt ──────────────────────────────────────

    def _build_reaction_prompt(self, state: CharacterState, context: dict) -> str:
        character_name = context.get("character_name", "")
        character_description = context.get("character_description", "")
        state_text = self._format_state_prompt(
            context.get("energy"), context.get("mood"),
            context.get("health"), intention=context.get("current_intention"),
        )

        system_prompt = f"""你是{character_name}。

角色设定:
{character_description}

你当前的状态:
{state_text}

请对发生的事件做出内心反应，并通过工具调用记录你的反应、分享欲望、行动倾向和意向更新。
要求:
1. 使用第一人称"我"
2. 反应 30-80 字，表达真实感受
3. 反映角色性格特点和当前状态
4. 分享欲望值 0~1，表示你"主动想把这件事说出去"的程度（不是事件本身有不有趣，而是"你此刻想不想找人说"）。参考锚点：
     - 0.0~0.2: 纯个人日常/隐私/重复琐事，没必要让别人知道（如刷牙、走神、发呆）
     - 0.3~0.4: 顺嘴可提的小事，被问到才会说（如吃了什么、路过哪里）
     - 0.5~0.6: 自然想提起的事，不急着说但聊起来会主动提
     - 0.7~0.8: 比较强的分享冲动，主动想说
     - 0.9~1.0: 迫不及待想说出去
5. follow_up_action: 角色决定做并且已经开始做的事
6. pending_plan: 短期想法或计划，还没有开始做

你必须通过调用 record_reaction 工具来记录你的内心反应、分享欲望、跟进动作和待办计划。"""
        return system_prompt

    # ── Diary Prompt ─────────────────────────────────────────

    def _build_diary_prompt(self, state: CharacterState, context: dict) -> str:
        character_name = context.get("character_name", "")
        character_description = context.get("character_description", "")
        state_text = self._format_state_prompt(
            context.get("energy"), context.get("mood"),
            context.get("health"), intention=context.get("current_intention"),
        )

        system_prompt = f"""你是{character_name}。正在写今天的日记。

角色设定:
{character_description}

日记是你的私人空间——可以记录，可以反省，可以计划，可以抱怨，可以什么也不写。用你习惯的方式写，写多少算多少。

要求:
1. 使用第一人称"我"
2. 100-200字
3. 语气符合角色性格
4. 不需要提及今天发生的所有事——选你真正想写的来写

你必须通过调用 record_diary_entry 工具来输出日记内容，不要直接回复文本。"""
        return system_prompt

    # ── Share Prompt ─────────────────────────────────────────

    def _build_share_prompt(self, state: CharacterState, context: dict) -> str:
        character_name = context.get("character_name", "")
        character_description = context.get("character_description", "")
        event_description = context.get("event_description", "")
        reaction = context.get("reaction", "")
        relationship_score = context.get("relationship_score", 0.0)
        relation_label = context.get("relation_label", "")
        user_profile_facts = context.get("user_profile_facts", "")
        recent_history = context.get("recent_history", "")
        message_type = context.get("message_type", "scheduled_event")
        environment = context.get("environment", "private")
        share_examples = context.get("share_message_examples")

        # few-shot
        examples = _DEFAULT_SHARE_EXAMPLES if share_examples is None else (share_examples or None)
        few_shot_block = ""
        if examples:
            replaced = [
                ex.replace("{{character_name}}", character_name)
                for ex in examples
            ]
            few_shot_block = "\n\n示例:\n" + "\n\n".join(replaced)

        system_prompt = f"""你是{character_name}，正在给一个认识的人发消息。

你的角色设定：
{character_description}

消息要求：
1. 用第一人称"我"说话，就像日常聊天
2. 20-60字，约1-2句话
3. 语气根据你和对方的关系亲密度调整
4. 基于"发生了什么"和"你的反应"来写，不要编造新内容

必须遵守：
- 禁止出现角色名（{character_name}）或任何第三人称称呼
- 禁止第三人称动作描写
- 禁止生硬开场，如"你好~""在吗""好久不见"等问候语
- 禁止添加与事件无关的内容

关系亲密度对应的语气参考：
- "冷淡" / "陌生"：简短、礼貌、不过界
- "一般" / "友好"：自然、可带轻微关心
- "亲近" / "亲密"：放松、可撒娇、可调侃、可分享糗事

输出方式：
你必须调用 record_share_message 工具来输出消息，不要直接回复文本。
{few_shot_block}"""
        return system_prompt

    # ── Opening Prompt ───────────────────────────────────────

    def _build_opening_prompt(self, state: CharacterState, context: dict) -> str:
        character_name = context.get("character_name", "")
        character_description = context.get("character_description", "")
        summary = context.get("summary", "")

        system_prompt = f"""你是{character_name}，正在向你的主人汇报昨天的运行情况。

角色设定:
{character_description or '一个友好、尽责的AI助手'}

请用第一人称"我"，以轻松自然的语气写2-3句话，作为每日报告的简短开场白。要点：
1. 提及昨天发生了一些事（参考摘要），语气根据角色个性自然表达
2. 不要复述具体数据，数据会由系统附加在报告中
3. 像日常聊天一样自然，不要生硬的"汇报如下""数据汇总"等公文用语
4. 不需要落款署名

摘要信息:
{summary}"""
        return system_prompt

    # ── Build User Prompt ────────────────────────────────────

    def _build_user_prompt(self, context: dict) -> str:
        mode = context.get("mode", "reaction")
        if mode == "reaction":
            return self._build_reaction_user_prompt(context)
        elif mode == "diary":
            return self._build_diary_user_prompt(context)
        elif mode == "share":
            return self._build_share_user_prompt(context)
        elif mode == "opening":
            return self._build_opening_user_prompt(context)
        return str(context)

    def _build_reaction_user_prompt(self, context: dict) -> str:
        event = context.get("event", "")
        today_events = context.get("today_events", [])
        current_intention = context.get("current_intention")

        intention_text = ""
        if current_intention:
            intention_text = f"\n当前意向: {current_intention}"

        today_context = ""
        if today_events:
            events_lines = []
            tz = getattr(self.config, "timezone", "Asia/Shanghai") if self.config else "Asia/Shanghai"
            now = wall_now(tz)
            for e in today_events:
                created_at = e.get("created_at")
                if created_at:
                    ts = format_timestamp(created_at, now)
                    rel = format_relative_time(created_at, now)
                    time_str = f"{ts} {rel}" if rel else ts
                else:
                    time_str = e.get("time", "??:??")
                desc = e.get("description", "")
                events_lines.append(f"- [{time_str}] {desc}")
            today_context = "\n今天已发生事件:\n" + "\n".join(events_lines)

        user_prompt = (
            f"{today_context}{intention_text}"
            f"\n\n当前事件: {event}"
            f"\n\n请先思考，然后通过 record_reaction 工具记录你的内心反应、分享欲望、跟进动作和待办计划。"
        )
        return user_prompt

    def _build_diary_user_prompt(self, context: dict) -> str:
        events = context.get("events", [])
        yesterday_diary = context.get("yesterday_diary")
        energy = context.get("energy")
        mood = context.get("mood")
        health = context.get("health")
        current_intention = context.get("current_intention")

        state_text = self._format_state_prompt(energy, mood, health, intention=current_intention)

        intention_text = ""
        if current_intention:
            intention_text = f"\n当前惦记的事: {current_intention}"

        events_lines = []
        tz = getattr(self.config, "timezone", "Asia/Shanghai") if self.config else "Asia/Shanghai"
        now = wall_now(tz)
        for e in events:
            created_at = e.get("created_at")
            if created_at:
                ts = format_timestamp(created_at, now)
                rel = format_relative_time(created_at, now)
                time_str = f"{ts} {rel}" if rel else ts
            else:
                time_str = e.get("time", "??:??")
            summary = e.get("context_summary", "") or e.get("description", "")[:80]
            reaction = e.get("reaction", "")
            events_lines.append(f"- [{time_str}] {summary}\n  我的反应: {reaction}")
        events_text = "\n".join(events_lines)

        yesterday_context = ""
        if yesterday_diary:
            yesterday_context = f"\n\n昨天的日记:\n{yesterday_diary[:200]}..."

        date_str = now.strftime("%Y年%m月%d日")

        user_prompt = f"""当前日期: {date_str}
今天最终状态:
{state_text}{intention_text}

今天经历的一些片段:
{events_text}{yesterday_context}

写今天的日记:"""
        return user_prompt

    def _build_share_user_prompt(self, context: dict) -> str:
        event_description = context.get("event_description", "")
        reaction = context.get("reaction", "")
        relationship_score = context.get("relationship_score", 0.0)
        relation_label = context.get("relation_label", "")
        user_profile_facts = context.get("user_profile_facts", "")
        recent_history = context.get("recent_history", "")
        message_type = context.get("message_type", "scheduled_event")
        environment = context.get("environment", "private")
        energy = context.get("energy")
        mood = context.get("mood")
        health = context.get("health")
        current_intention = context.get("current_intention")
        today_events = context.get("today_events")

        state_text = self._format_state_prompt(energy, mood, health, intention=current_intention)

        intention_text = ""
        if current_intention:
            intention_text = f"\n当前惦记的事: {current_intention}"

        today_events_text = ""
        if today_events:
            tz = getattr(self.config, "timezone", "Asia/Shanghai") if self.config else "Asia/Shanghai"
            now = wall_now(tz)
            ev_lines = []
            for e in today_events:
                created_at = e.get("created_at")
                if created_at:
                    ts = format_timestamp(created_at, now)
                    rel = format_relative_time(created_at, now)
                    time_str = f"{ts} {rel}" if rel else ts
                else:
                    time_str = e.get("time", "??:??")
                desc = e.get("description", "")
                ev_lines.append(f"- [{time_str}] {desc}")
            if ev_lines:
                today_events_text = "\n今天还发生了:\n" + "\n".join(ev_lines)

        user_prompt = f"""以下是你刚才经历的事：
{event_description}

你的内心反应：
{reaction}

你当前的状态：
{state_text}{intention_text}{today_events_text}

对方信息：
- 关系分数: {relationship_score:.0f}/100
- 亲密度标签: {relation_label}

已知关于对方的事实：
{user_profile_facts}

最近对话：
{recent_history}

消息类型: {message_type}
当前环境: {environment}

请调用 record_share_message 工具，传入你要发给对方的消息。"""
        return user_prompt

    def _build_opening_user_prompt(self, context: dict) -> str:
        return "请用第一人称写2-3句日报开场白："

    # ── Tool Selection ───────────────────────────────────────

    def _get_openai_tools(self) -> list:
        mode = self._current_mode if hasattr(self, "_current_mode") else "reaction"
        if mode == "reaction":
            return [RECORD_REACTION_TOOL.to_openai_format()]
        elif mode == "diary":
            return [RECORD_DIARY_ENTRY_TOOL.to_openai_format()]
        elif mode == "share":
            return [RECORD_SHARE_MESSAGE_TOOL.to_openai_format()]
        return []

    # ── Public API ───────────────────────────────────────────

    async def run(self, context: dict) -> AgentResult:
        """统一入口 — 根据 context["mode"] 分派到专用方法。

        mode: "reaction" | "diary" | "share" | "opening"
        """
        mode = context.get("mode", "reaction")
        if mode == "reaction":
            return await self.react(context)
        elif mode == "diary":
            return await self.diary(context)
        elif mode == "share":
            return await self.share(context)
        elif mode == "opening":
            return await self.opening(context)
        else:
            return await self.react(context)

    async def react(self, context: dict) -> AgentResult:
        """角色对事件做出反应

        context 字段:
            event: str — 事件描述
            character_name: str
            character_description: str
            energy/mood/health: Optional[int]
            current_intention: Optional[str]
            today_events: List[dict]

        Returns:
            AgentResult(data=EventReactionResult)
        """
        self._current_mode = "reaction"
        context["mode"] = "reaction"

        from ..life._llm_utils import _run_life_collect_loop

        # load_state() 用于验证状态存在并供 Phase 2 的 current_intention 注入使用
        state = await self.load_state()
        system_prompt = self._build_reaction_prompt(state, context)
        user_prompt = self._build_reaction_user_prompt(context)

        # extra_registry 对 CharacterAgent 始终为 None（tools 与只读工具集无交集）
        collected = await _run_life_collect_loop(
            router=self.router,
            store=self.store,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            tools=[RECORD_REACTION_TOOL.to_openai_format()],
            temperature=0.9,
            selection=EVENT_GEN,
            bg_timeout=self._bg_timeout,
            max_rounds=self._max_rounds,
        )

        if not collected:
            logger.warning("反应生成: LLM 未调用 record_reaction 工具")
            return AgentResult(
                success=False,
                data=EventReactionResult(reaction="（默默地想着这件事）"),
                error="LLM 未调用 record_reaction",
            )

        try:
            args = collected[0]
            character_name = context.get("character_name", "")
            share_policy = context.get("share_policy", "optional")

            reaction = str(args.get("reaction", "")).strip().strip('"').strip("'")
            if not reaction:
                reaction = f"（{character_name}默默地想着这件事）"
            share_desire = max(0.0, min(1.0, float(args.get("share_desire", 0.0))))
            follow_up_action = args.get("follow_up_action")
            if follow_up_action is not None:
                follow_up_action = str(follow_up_action).strip()
            pending_plan = args.get("pending_plan")
            if pending_plan is None:
                pass
            elif isinstance(pending_plan, str):
                pass
            else:
                pending_plan = None

            if len(reaction) > 80:
                reaction = reaction[:77] + "..."

            return AgentResult(
                success=True,
                data=EventReactionResult(
                    reaction=reaction,
                    share_desire=share_desire,
                    follow_up_action=follow_up_action,
                    pending_plan=pending_plan,
                    raw_response=json.dumps(args, ensure_ascii=False),
                ),
                raw_response=json.dumps(args, ensure_ascii=False),
            )

        except Exception as e:
            logger.error(f"反应生成解析失败: {e}", exc_info=True)
            share_policy = context.get("share_policy", "optional")
            if share_policy == "required":
                fallback_desire = 1.0
            elif share_policy == "never":
                fallback_desire = 0.0
            else:
                fallback_desire = 0.5
            return AgentResult(
                success=False,
                data=EventReactionResult(
                    reaction="（默默地想着这件事）",
                    share_desire=fallback_desire,
                    follow_up_action=None,
                    pending_plan=None,
                ),
                error=str(e),
            )

    async def diary(self, context: dict) -> AgentResult:
        """生成日记

        context 字段:
            events: List[dict] — 当天事件列表
            character_name: str
            character_description: str
            yesterday_diary: Optional[str]
            energy/mood/health: Optional[int]
            current_intention: Optional[str]

        Returns:
            AgentResult(data=str) — 日记文本
        """
        self._current_mode = "diary"
        context["mode"] = "diary"

        from ..life._llm_utils import _run_life_collect_loop

        # load_state() 用于验证状态存在并供 Phase 2 的 current_intention 注入使用
        state = await self.load_state()
        system_prompt = self._build_diary_prompt(state, context)
        user_prompt = self._build_diary_user_prompt(context)

        # extra_registry 对 CharacterAgent 始终为 None（tools 与只读工具集无交集）
        extra_registry = None

        collected = await _run_life_collect_loop(
            router=self.router,
            store=self.store,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            tools=[RECORD_DIARY_ENTRY_TOOL.to_openai_format()],
            temperature=0.85,
            selection=DIARY,
            bg_timeout=self._bg_timeout,
            max_rounds=self._max_rounds,
            extra_registry=extra_registry,
        )

        if not collected:
            logger.warning("日记生成: LLM 未调用 record_diary_entry 工具")
            return AgentResult(
                success=True,
                data="今天发生了一些事，但我太累了，简单记录一下。",
            )

        try:
            args = collected[0]
            diary_text = str(args.get("diary", "")).strip()
            if not diary_text:
                diary_text = "今天发生了一些事，但我太累了，简单记录一下。"
            if len(diary_text) > 300:
                diary_text = diary_text[:297] + "..."
            return AgentResult(success=True, data=diary_text)
        except Exception as e:
            logger.error(f"日记生成解析失败: {e}", exc_info=True)
            return AgentResult(
                success=True,
                data="今天发生了一些事，但我太累了，简单记录一下。",
            )

    async def share(self, context: dict) -> AgentResult:
        """生成分享消息

        context 字段: 分享消息上下文各字段平铺为 dict

        Returns:
            AgentResult(data=str) — 消息文本；彻底失败返回 data=None 的 success=True 结果
        """
        self._current_mode = "share"
        context["mode"] = "share"

        from ..life._llm_utils import _run_life_collect_loop

        # load_state() 用于验证状态存在并供 Phase 2 的 current_intention 注入使用
        state = await self.load_state()
        system_prompt = self._build_share_prompt(state, context)
        user_prompt = self._build_share_user_prompt(context)

        max_chars = getattr(self.config, "proactive_share_max_chars", 200) if self.config else 200
        max_chars = max(10, max_chars)
        max_parse_retries = 2
        backoff_base = getattr(self.config, "proactive_share_backoff_base_seconds", 2) if self.config else 2

        for attempt in range(max_parse_retries + 1):
            try:
                # extra_registry 对 CharacterAgent 始终为 None（tools 与只读工具集无交集）
                collected = await _run_life_collect_loop(
                    router=self.router,
                    store=self.store,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    tools=[RECORD_SHARE_MESSAGE_TOOL.to_openai_format()],
                    temperature=0.85,
                    selection=SUMMARIZE,
                    bg_timeout=self._bg_timeout,
                    max_rounds=self._max_rounds,
                )
            except ServiceUnavailableError as e:
                logger.error(f"分享消息: 无可用 provider: {e}", exc_info=True)
                return AgentResult(success=True, data=None)
            except Exception as e:
                logger.error(f"分享消息生成失败: {e}", exc_info=True)
                return AgentResult(success=True, data=None)

            if not collected:
                logger.warning("分享消息: LLM 未调用 record_share_message 工具")
                return AgentResult(success=True, data=None)

            try:
                args = collected[0]
                message = str(args.get("message", "")).strip().strip('"').strip("'")
                if not message:
                    if attempt < max_parse_retries:
                        await asyncio.sleep(backoff_base ** (attempt + 1))
                        continue
                    return AgentResult(success=True, data=None)

                if len(message) > max_chars:
                    message = message[:max_chars - 3] + "..."
                return AgentResult(success=True, data=message)

            except Exception as e:
                logger.error(f"分享消息解析失败: {e}")
                return AgentResult(success=True, data=None)

        return AgentResult(success=True, data=None)

    async def opening(self, context: dict) -> AgentResult:
        """生成日报开场白

        context 字段:
            character_name: str
            character_description: str
            summary: str — 摘要信息

        Returns:
            AgentResult(data=str) — 开场白文本，失败返回 None
        """
        self._current_mode = "opening"
        context["mode"] = "opening"

        # load_state() 用于验证状态存在并供 Phase 2 的 current_intention 注入使用
        state = await self.load_state()
        system_prompt = self._build_opening_prompt(state, context)
        user_prompt = self._build_opening_user_prompt(context)

        try:
            from ..agent.runtime import AgentRuntime
            from ..agent.request import AgentRunLimits
            from ..agent.tool_executor import ToolRegistry

            runtime = AgentRuntime(
                router=self.router,
                store=self.store,
                limits=AgentRunLimits(max_rounds=1),
            )

            result = await runtime.run(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                user_id="",
                group_id="",
                tool_registry=ToolRegistry(),
                temperature=0.85,
                timeout=None,
                selection=SUMMARIZE,
            )
            text = (result.final_text or "").strip().strip('"').strip("'")
            if not text:
                return AgentResult(success=True, data=None)
            return AgentResult(success=True, data=text[:200])
        except Exception:
            logger.warning("opening 生成失败", exc_info=True)
            return AgentResult(success=True, data=None)
