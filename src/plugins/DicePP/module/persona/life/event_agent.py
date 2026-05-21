"""
事件生成 Agent

System Agent: 生成客观生活事件
Character Agent: 生成角色对事件的反应
"""
import asyncio
from dataclasses import dataclass
from typing import List, Literal, Optional
from datetime import datetime
import json
from nonebot.log import logger
from ..llm.router import LLMRouter, ServiceUnavailableError
from ..llm.selection import SelectionPolicy
from ..tools.registry import ToolRegistry, ToolDomain
from ..tools.collecting import RECORD_EVENT_TOOL, RECORD_REACTION_TOOL, RECORD_DIARY_ENTRY_TOOL, RECORD_SHARE_MESSAGE_TOOL
from ..tools.context import ToolContext
from ..wall_clock import format_timestamp, format_relative_time
from typing import TYPE_CHECKING

# keep in sync with PersonaConfig.background_llm_timeout_seconds default
_DEFAULT_BG_TIMEOUT = 90

if TYPE_CHECKING:
    from core.config.pydantic_models import PersonaConfig

@dataclass
class EventGenerationResult:
    description: str = ""
    context_summary: str = ""  # 用于聊天上下文注入的简短摘要
    duration_minutes: int = 0
    energy_delta: Optional[int] = None
    mood_delta: Optional[int] = None
    health_delta: Optional[int] = None
    raw_response: str = ""  # LLM 原始工具调用参数 JSON
    system_prompt_digest: str = ""  # 生成时使用的 system_prompt


@dataclass
class EventReactionResult:
    reaction: str = ""
    share_desire: float = 0.0
    follow_up_action: Optional[str] = None  # None=无后续行动, 非空字符串=续写, 空字符串=不续写
    pending_plan: Optional[str] = None  # None=保持, ""=清空, 非空=更新
    raw_response: str = ""  # LLM 原始工具调用参数 JSON


@dataclass
class ShareMessageContext:
    """分享消息生成上下文"""

    event_description: str = ""
    reaction: str = ""
    character_name: str = ""
    character_description: str = ""
    target_user_id: str = ""
    relationship_score: float = 0.0
    warmth_label: str = ""
    user_profile_facts: str = ""
    recent_history: str = ""
    message_type: Literal["scheduled_event", "miss_you", "random_event"] = "scheduled_event"
    environment: Literal["private", "group"] = "private"
    share_message_examples: Optional[List[str]] = None
    # 结构化状态与上下文
    energy: Optional[int] = None
    mood: Optional[int] = None
    health: Optional[int] = None
    today_events: Optional[List[dict]] = None
    current_intention: Optional[str] = None


class EventContext:
    """事件生成上下文"""

    def __init__(
        self,
        character_name: str,
        character_description: str,
        world: str,
        scenario: str,
        recent_diaries: List[str],
        today_events: List[dict],
        permanent_state: str = "",
        current_time: Optional[datetime] = None,
        energy: Optional[int] = None,
        mood: Optional[int] = None,
        health: Optional[int] = None,
        current_intention: Optional[str] = None,
        intention_created_at: Optional[datetime] = None,
    ):
        self.character_name = character_name
        self.character_description = character_description
        self.world = world
        self.scenario = scenario
        self.recent_diaries = recent_diaries
        self.today_events = today_events
        self.permanent_state = permanent_state
        self.current_time = current_time
        self.energy = energy
        self.mood = mood
        self.health = health
        self.current_intention = current_intention
        self.intention_created_at = intention_created_at


class EventGenerationAgent:
    """事件生成 Agent - 使用辅助模型"""

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

    def __init__(
        self,
        llm_router: LLMRouter,
        tool_registry: ToolRegistry,
        config: Optional["PersonaConfig"] = None,
    ):
        self.llm_router = llm_router
        self.tool_registry = tool_registry
        self.config = config
        self._bg_timeout = (
            getattr(config, "background_llm_timeout_seconds", _DEFAULT_BG_TIMEOUT)
            if config
            else _DEFAULT_BG_TIMEOUT
        )
        self._max_tool_rounds = (
            getattr(config, "background_llm_max_tool_rounds", 1)
            if config
            else 1
        )

    @staticmethod
    def _format_state_prompt(energy: Optional[int], mood: Optional[int],
                             health: Optional[int], intention: Optional[str] = None) -> str:
        """构建状态 prompt 片段，供各生成方法复用。"""
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

    # 状态刻度定义（注入 System Agent prompt）
    _STATE_SCALE_PROMPT = """状态刻度（0-100）：
- 80-100: 极佳（精力充沛、心情愉悦、身体健康）
- 60-79: 良好（略有疲惫、情绪平稳、无病痛）
- 40-59: 一般（明显疲倦、情绪低落、轻微不适）
- 20-39: 较差（精疲力竭、心情糟糕、生病中）
- 0-19: 极差（虚弱无力、崩溃绝望、重病缠身）

状态变化幅度参考：
- ±1-5: 轻微变化（日常琐事）
- ±6-10: 明显变化（值得关注的事件）
- ±11-20: 显著变化（重大事件，极少超过20）"""

    async def _run_life_collect_loop(
        self, messages: list, tools: list, temperature: float, selection: SelectionPolicy,
    ) -> list:
        collected: list = []
        tool_ctx = ToolContext(collected_args=collected)
        await self.llm_router.run_via_loop(
            messages=messages, tools=tools, temperature=temperature,
            timeout=self._bg_timeout, tool_registry=self.tool_registry,
            tool_domains=[ToolDomain.LIFE], tool_ctx=tool_ctx,
            selection=selection, max_tool_rounds=self._max_tool_rounds,
        )
        return collected

    async def generate_event_result(self, context: EventContext) -> EventGenerationResult:
        """
        System Agent: 通过 Function Calling 强制产出结构化事件数据。
        """
        # 构建状态信息
        state_text = self._format_state_prompt(
            context.energy, context.mood, context.health, intention=None
        )

        intention_text = ""
        if context.current_intention:
            intention_text = f"\n当前意向: {context.current_intention}"
            if context.intention_created_at:
                intention_text += f"（始于 {context.intention_created_at.strftime('%H:%M')}）"

        system_prompt = f"""你是世界观设定专家。基于以下信息生成一个生活事件。

角色:
{context.character_name} - {context.character_description or "普通人"}

世界观:
{context.world or "现代日常世界"}

场景:
{context.scenario or "日常生活"}

角色当前状态:
{state_text}
{self._STATE_SCALE_PROMPT}

生成要求:
1. 以第三人称客观叙述描述发生了什么（不携带主观情绪）
2. 只记录可观察的行为和状态（动作、位置、物品、身体状态）
3. 不包含心理活动、情绪评价、内心独白
4. 不使用"觉得""认为""感到"等主观动词
5. description 自然叙事，不强制字数上限，但保持简洁
6. context_summary 为事件摘要，30-60字，仅包含关键事实（谁、在哪、做了什么、结果）
7. 符合世界观和场景设定，但场景中的具体动作是参考而非约束
8. 避免与今天已发生事件在具体内容上高度重复，优先描述不同的事
9. 同时给出该事件对角色体力/心情/健康的影响（delta，可选整数，范围-20~+20）

你必须通过调用 record_event 工具来输出结果。"""

        diary_context = ""
        if context.recent_diaries:
            diary_context = "\n最近日记:\n" + "\n".join(
                f"- {d[:100]}..." if len(d) > 100 else f"- {d}"
                for d in context.recent_diaries[-3:]
            )

        events_context = ""
        if context.today_events:
            events_lines = []
            for e in context.today_events[-5:]:
                created_at = e.get("created_at")
                if created_at and context.current_time:
                    ts = format_timestamp(created_at, context.current_time)
                    rel = format_relative_time(created_at, context.current_time)
                    time_str = f"{ts} {rel}" if rel else ts
                else:
                    time_str = e.get("time", "??:??")
                desc = e.get("description", "")
                events_lines.append(f"- [{time_str}] {desc}")
            events_context = (
                "\n\n今天已经做过的事：\n"
                + "\n".join(events_lines)
                + "\n\n角色的一天还在继续。请生成一件不同的事——"
                "可以外出到其他场景、与其他角色互动、遭遇突发小意外、做不同的日常琐事，或只是休息。"
                "同一场景内也可以有各种不同的行为、互动或细节，不必拘泥于上述记录。"
            )

        now_str = format_timestamp(context.current_time, context.current_time) if context.current_time else "??:??"
        user_prompt = f"当前时间: {now_str}{intention_text}{diary_context}{events_context}\n\n请生成一个符合世界观的生活事件，并通过 record_event 工具记录:"

        logger.debug("[prompt:system_event]\n{}", system_prompt)
        logger.debug("[prompt:user_event]\n{}", user_prompt)

        tools = [RECORD_EVENT_TOOL.to_openai_format()]

        try:
            collected = await self._run_life_collect_loop(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                tools=tools,
                temperature=0.9,
                selection=SelectionPolicy.EVENT_GEN,
            )

            if not collected:
                logger.warning("事件生成: LLM 未调用 record_event 工具")
                raise ValueError("LLM 未调用 record_event")

            args = collected[0]
            description = str(args.get("description", "")).strip().strip('"').strip("'")
            if not description:
                description = "我正在房间里休息。"
            duration_minutes = max(0, min(2880, int(args.get("duration_minutes", 0))))

            context_summary = str(args.get("context_summary", "")).strip().strip('"').strip("'")
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

            logger.debug(
                f"生成事件: {description[:50]}..., summary={context_summary[:50]}..., "
                f"duration={duration_minutes}, deltas=({energy_delta}, {mood_delta}, {health_delta})"
            )
            return EventGenerationResult(
                description=description,
                context_summary=context_summary,
                duration_minutes=duration_minutes,
                energy_delta=energy_delta,
                mood_delta=mood_delta,
                health_delta=health_delta,
                raw_response=json.dumps(args, ensure_ascii=False),
                system_prompt_digest=system_prompt,
            )

        except Exception as e:
            logger.error(f"事件生成失败: {e}", exc_info=True)
            fallback_args = {
                "description": "我正在房间里休息。",
                "context_summary": "在房间里休息",
                "duration_minutes": 0,
                "energy_delta": 0,
                "mood_delta": None,
                "health_delta": None,
                "raw_response": '{"description":"我正在房间里休息。","duration_minutes":0,"energy_delta":0,"mood_delta":null,"health_delta":null}',
                "system_prompt_digest": "[fallback]",
            }
            return EventGenerationResult(**fallback_args)

    async def generate_event_reaction(
        self,
        event: str,
        character_name: str,
        character_description: str,
        share_policy: str = "optional",
        today_events: Optional[List[dict]] = None,
        energy: Optional[int] = None,
        mood: Optional[int] = None,
        health: Optional[int] = None,
        current_intention: Optional[str] = None,
    ) -> EventReactionResult:
        """
        Character Agent: 通过 Function Calling 同时产出内心反应、分享欲望、
        跟进动作（follow_up_action）和待办计划（pending_plan）。
        """
        # 构建状态信息
        state_text = self._format_state_prompt(energy, mood, health, intention=current_intention)

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
   - 0.5~0.6: 自然想提起的事，不急着说但聊起来会主动提（如小心情、新发现、生活里的小变化、想吐槽或感慨的事）
   - 0.7~0.8: 比较强的分享冲动，主动想说（如做了某个决定想说出来、明显的情绪波动想找人说、忍不住想分享的小成就）
   - 0.9~1.0: 迫不及待想说出去（如突然涌起强烈情绪、完成期待已久的事的成就感、特别想立刻聊的兴奋念头）
   注意：重复的日常动作即便有内容也应给低分；评分依据是"分享价值"，不是"事件戏剧性"。
5. follow_up_action: 根据当前情况，角色决定做并且已经开始做的事。如果有，填写具体描述（如"开始整理房间""出门去买东西"），这会触发事件-反应链的续写。如果没有则填 null
6. pending_plan: 角色产生的短期想法或计划，但还没有开始做（如"下午想去看电影""明天要去邮局"）。填写后会被记录到角色状态中供后续事件参考，但不会立即触发续写。如果没有则填 null（保持当前备忘）；如果想放弃当前备忘则填空字符串"""""

        intention_text = ""
        if current_intention:
            intention_text = f"\n当前意向: {current_intention}"

        today_context = ""
        if today_events:
            events_lines = []
            now = datetime.now()
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

        user_prompt = f"{today_context}{intention_text}\n\n当前事件: {event}\n\n请先思考，然后通过 record_reaction 工具记录你的内心反应、分享欲望、跟进动作和待办计划。"

        logger.debug("[prompt:system_reaction]\n{}", system_prompt)
        logger.debug("[prompt:user_reaction]\n{}", user_prompt)

        tools = [RECORD_REACTION_TOOL.to_openai_format()]

        try:
            collected = await self._run_life_collect_loop(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                tools=tools,
                temperature=0.9,
                selection=SelectionPolicy.EVENT_GEN,
            )

            if not collected:
                logger.warning("反应生成: LLM 未调用 record_reaction 工具")
                raise ValueError("LLM 未调用 record_reaction")

            args = collected[0]
            reaction = str(args.get("reaction", "")).strip().strip('"').strip("'")
            if not reaction:
                reaction = f"（{character_name}默默地想着这件事）"
            share_desire = max(0.0, min(1.0, float(args.get("share_desire", 0.0))))
            follow_up_action = args.get("follow_up_action")
            if follow_up_action is not None:
                follow_up_action = str(follow_up_action).strip()
            pending_plan = args.get("pending_plan")
            # pending_plan 可能是 null/None、空字符串、或非空字符串
            if pending_plan is None:
                pass  # 保持 None（保持当前备忘）
            elif isinstance(pending_plan, str):
                pass  # 保持字符串值（含空字符串=清空备忘）
            else:
                pending_plan = None  # 非字符串/非 None 值（如 0、False）统一视为 None

            if len(reaction) > 80:
                reaction = reaction[:77] + "..."

            logger.debug(
                f"生成反应: {reaction}, share_desire={share_desire}, "
                f"follow_up={follow_up_action!r}, pending_plan={pending_plan!r}"
            )
            return EventReactionResult(
                reaction=reaction,
                share_desire=share_desire,
                follow_up_action=follow_up_action,
                pending_plan=pending_plan,
                raw_response=json.dumps(args, ensure_ascii=False),
            )

        except Exception as e:
            logger.error(f"反应生成失败: {e}", exc_info=True)
            if share_policy == "required":
                fallback_desire = 1.0
            elif share_policy == "never":
                fallback_desire = 0.0
            else:
                fallback_desire = 0.5
            return EventReactionResult(
                reaction=f"（{character_name}默默地想着这件事）",
                share_desire=fallback_desire,
                follow_up_action=None,
                pending_plan=None,
            )

    async def generate_diary(
        self,
        events: List[dict],
        character_name: str,
        character_description: str,
        yesterday_diary: Optional[str] = None,
        energy: Optional[int] = None,
        mood: Optional[int] = None,
        health: Optional[int] = None,
        current_intention: Optional[str] = None,
    ) -> str:
        """
        生成日记总结

        Args:
            events: 当天的所有事件和反应
            character_name: 角色名
            character_description: 角色描述
            yesterday_diary: 昨天的日记（可选）
            energy: 当天最终体力（可选）
            mood: 当天最终心情（可选）
            health: 当天最终健康（可选）
            current_intention: 当前意向（可选）

        Returns:
            日记内容 (100-300 字)
        """
        # 构建状态信息
        state_text = self._format_state_prompt(energy, mood, health, intention=current_intention)

        intention_text = ""
        if current_intention:
            intention_text = f"\n当前惦记的事: {current_intention}"

        system_prompt = f"""你是{character_name}，正在写今天的日记。

角色设定:
{character_description}

请根据今天发生的事情写一篇日记。
要求:
1. 使用第一人称"我"
2. 100-300字，日记格式
3. 自然地提及今天的事件和感受
4. 语气符合角色性格
5. 可以包含对未来的期待或反思

注意：事件描述是第三人称客观记录，反应是角色第一人称自述。请将两者统一转换为日记口吻。

你必须通过调用 record_diary_entry 工具来输出日记内容，不要直接回复文本。"""

        # 构建事件上下文（带时间戳）
        events_lines = []
        now = datetime.now()
        for e in events:
            created_at = e.get("created_at")
            if created_at:
                ts = format_timestamp(created_at, now)
                rel = format_relative_time(created_at, now)
                time_str = f"{ts} {rel}" if rel else ts
            else:
                time_str = e.get("time", "??:??")
            desc = e.get("description", "")
            reaction = e.get("reaction", "")
            events_lines.append(f"- [{time_str}] {desc}\n  我的反应: {reaction}")
        events_text = "\n".join(events_lines)

        yesterday_context = ""
        if yesterday_diary:
            yesterday_context = f"\n\n昨天的日记:\n{yesterday_diary[:200]}..."

        user_prompt = f"""今天最终状态:
{state_text}{intention_text}

今天发生的事情:
{events_text}{yesterday_context}

请写一篇日记总结今天:"""

        logger.debug("[prompt:system_diary]\n{}", system_prompt)
        logger.debug("[prompt:user_diary]\n{}", user_prompt)

        tools = [RECORD_DIARY_ENTRY_TOOL.to_openai_format()]

        try:
            collected = await self._run_life_collect_loop(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                tools=tools,
                temperature=0.85,
                selection=SelectionPolicy.DIARY,
            )

            if not collected:
                logger.warning("日记生成: LLM 未调用 record_diary_entry 工具")
                return "今天发生了一些事，但我太累了，简单记录一下。"

            args = collected[0]
            diary = str(args.get("diary", "")).strip()

            if not diary:
                return "今天发生了一些事，但我太累了，简单记录一下。"

            if len(diary) > 300:
                diary = diary[:297] + "..."

            logger.info(f"生成日记: {len(diary)} 字")
            return diary

        except Exception as e:
            logger.error(f"日记生成失败: {e}", exc_info=True)
            return f"今天发生了一些事，但我太累了，简单记录一下。"

    async def generate_share_message(self, context: ShareMessageContext) -> Optional[str]:
        """
        为指定目标生成个性化分享消息。

        使用 AUXILIARY tier 模型，通过 generate() 工具路径 +
        CollectExecutor 收集结果。client.generate() 内建 L1 纠正注入和 API
        级重试（3 次指数退避）。超时由 background_llm_timeout_seconds 控制。
        JSON 解析失败或空消息时最多额外重试 2 次（指数退避）。
        彻底失败返回 None（调用方应静默丢弃）。

        Args:
            context: 分享消息生成上下文

        Returns:
            生成的消息文本，失败返回 None
        """
        # 处理 few-shot 示例
        examples: Optional[List[str]] = None
        if context.share_message_examples is None:
            examples = list(self._DEFAULT_SHARE_EXAMPLES)
        elif context.share_message_examples:
            examples = list(context.share_message_examples[:8])
        # [] 时不注入 few-shot

        few_shot_block = ""
        if examples:
            replaced = [
                ex.replace("{{character_name}}", context.character_name)
                for ex in examples
            ]
            few_shot_block = "\n\n示例:\n" + "\n\n".join(replaced)

        system_prompt = f"""你是{context.character_name}，正在给一个认识的人发消息。

你的角色设定：
{context.character_description}

消息要求：
1. 用第一人称"我"说话，就像日常聊天
2. 20-60字，约1-2句话
3. 语气根据你和对方的关系亲密度调整（见下方"关系"）
4. 基于"发生了什么"和"你的反应"来写，不要编造新内容

必须遵守：
- 禁止出现角色名（{context.character_name}）或任何第三人称称呼
- 禁止第三人称动作描写，如"{context.character_name}低头""她叹了口气"
- 禁止生硬开场，如"你好~""在吗""好久不见"等问候语
- 禁止添加与事件无关的内容
- 可顺便回应"最近对话"中你尚未回复的消息，但必须紧接着分享当前事件。分享事件是这条消息的核心目的，不可因补答而遗漏分享

关系亲密度（warmth_label）对应的语气参考：
- "冷淡" / "陌生"：简短、礼貌、不过界
- "一般" / "友好"：自然、可带轻微关心
- "亲近" / "亲密"：放松、可撒娇、可调侃、可分享糗事

输出方式：
你必须调用 record_share_message 工具来输出消息，不要直接回复文本。
{few_shot_block}"""

        # 构建状态信息
        state_text = self._format_state_prompt(
            context.energy, context.mood, context.health, intention=context.current_intention
        )

        intention_text = ""
        if context.current_intention:
            intention_text = f"\n当前惦记的事: {context.current_intention}"

        # 今日事件列表（带时间戳，过滤掉当前事件避免 prompt 中重复）
        today_events_text = ""
        if context.today_events:
            # 仅过滤最后一个描述匹配的项（避免同名事件被误伤）
            skip_idx = None
            for idx in reversed(range(len(context.today_events))):
                if context.today_events[idx].get("description") == context.event_description:
                    skip_idx = idx
                    break
            now = datetime.now()
            ev_lines = []
            for idx, e in enumerate(context.today_events):
                if idx == skip_idx:
                    continue
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
{context.event_description}

你的内心反应：
{context.reaction}

你当前的状态：
{state_text}{intention_text}{today_events_text}

对方信息：
- 关系分数: {context.relationship_score:.0f}/100
- 亲密度标签: {context.warmth_label}

已知关于对方的事实：
{context.user_profile_facts}

最近对话：
{context.recent_history}

消息类型: {context.message_type}
当前环境: {context.environment}

请调用 record_share_message 工具，传入你要发给对方的消息。"""

        logger.debug("[prompt:system_share]\n{}", system_prompt)
        logger.debug("[prompt:user_share]\n{}", user_prompt)

        tools = [RECORD_SHARE_MESSAGE_TOOL.to_openai_format()]

        max_chars = getattr(self.config, "proactive_share_max_chars", 200) if self.config else 200
        max_chars = max(10, max_chars)
        max_parse_retries = 2
        backoff_base = getattr(self.config, "proactive_share_backoff_base_seconds", 2) if self.config else 2

        for attempt in range(max_parse_retries + 1):
            try:
                collected = await self._run_life_collect_loop(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    tools=tools,
                    temperature=0.85,
                    selection=SelectionPolicy.SUMMARIZE,
                )
            except ServiceUnavailableError as e:
                logger.error(f"分享消息: 无可用 provider: {e}", exc_info=True)
                return None
            except Exception as e:
                logger.error(f"分享消息生成失败: {e}", exc_info=True)
                return None

            if not collected:
                logger.warning("分享消息: LLM 未调用 record_share_message 工具")
                return None

            args = collected[0]
            message = str(args.get("message", "")).strip().strip('"').strip("'")
            if not message:
                logger.warning(f"分享消息生成结果为空（第{attempt + 1}次）")
                if attempt < max_parse_retries:
                    backoff = backoff_base ** (attempt + 1)
                    await asyncio.sleep(backoff)
                    continue
                return None

            if len(message) > max_chars:
                original_len = len(message)
                message = message[:max_chars - 3] + "..."
                logger.warning(
                    f"分享消息长度超限({original_len}/{max_chars})，已截断为 {len(message)} 字"
                )
            logger.debug(f"生成分享消息: {message[:50]}...")
            return message

        return None

    @staticmethod
    async def generate_report_opening(
        llm_router: LLMRouter,
        character_name: str,
        character_description: str,
        summary: str,
    ) -> Optional[str]:
        """生成日报开场白 — 辅助 tier，无工具调用，直接取文本回复。

        Returns:
            2-3 句角色口吻文本，失败时返回 None（调用方降级为模板）
        """
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

        user_prompt = "请用第一人称写2-3句日报开场白："

        try:
            result = await llm_router.run_via_loop(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                selection=SelectionPolicy.SUMMARIZE,
                temperature=0.85,
                tools=None,
                max_tool_rounds=0,
            )
            text = (result.final_output or "").strip().strip('"').strip("'")
            if not text:
                return None
            return text[:200]
        except Exception:
            logger.warning("generate_report_opening 失败，降级为纯模板", exc_info=True)
            return None
