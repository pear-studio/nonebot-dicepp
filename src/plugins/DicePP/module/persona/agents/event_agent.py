"""
事件生成 Agent

System Agent: 生成客观生活事件
Character Agent: 生成角色对事件的反应
"""
import asyncio
from dataclasses import dataclass
from typing import List, Literal, Optional, TYPE_CHECKING
from datetime import datetime
import json
import logging

from ..llm.router import LLMRouter
from ..data.models import ModelTier

if TYPE_CHECKING:
    from core.config.pydantic_models import PersonaConfig

# 模块级默认值常量（与 PersonaConfig 同步）
_DEFAULT_SHARE_RETRIES = 2
_DEFAULT_SHARE_TIMEOUT = 60
_DEFAULT_SHARE_BACKOFF_BASE = 2  # 指数退避基数，用于高峰期 API 限流时降低重试频率
_DEFAULT_SHARE_MAX_CHARS = 200
_EVENT_DESCRIPTION_MAX_LEN = 60

logger = logging.getLogger("persona.event_agent")


@dataclass
class EventGenerationResult:
    description: str = ""
    duration_minutes: int = 0


@dataclass
class EventReactionResult:
    reaction: str = ""
    share_desire: float = 0.0


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
    ):
        self.character_name = character_name
        self.character_description = character_description
        self.world = world
        self.scenario = scenario
        self.recent_diaries = recent_diaries
        self.today_events = today_events
        self.permanent_state = permanent_state
        self.current_time = current_time


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

    def __init__(self, llm_router: LLMRouter, config: Optional["PersonaConfig"] = None):
        self.llm_router = llm_router
        self.config = config

    async def generate_event_result(self, context: EventContext) -> EventGenerationResult:
        """
        System Agent: 通过 Function Calling 强制产出结构化事件数据。
        """
        system_prompt = f"""你是世界观设定专家。基于以下信息生成一个生活事件。

角色:
{context.character_name} - {context.character_description or "普通人"}

世界观:
{context.world or "现代日常世界"}

场景:
{context.scenario or "日常生活"}

角色状态:
{context.permanent_state or "无特殊状态"}

生成要求:
1. 以第三人称客观叙述描述发生了什么（不携带主观情绪）
2. 只记录可观察的行为和状态（动作、位置、物品、身体状态）
3. 不包含心理活动、情绪评价、内心独白
4. 不使用"觉得""认为""感到"等主观动词
5. 20-60字，简洁具体
6. 符合世界观和场景设定

你必须通过调用 record_event 工具来输出结果。"""

        diary_context = ""
        if context.recent_diaries:
            diary_context = "\n最近日记:\n" + "\n".join(
                f"- {d[:100]}..." if len(d) > 100 else f"- {d}"
                for d in context.recent_diaries[-3:]
            )

        events_context = ""
        if context.today_events:
            events_context = "\n今天已发生事件:\n" + "\n".join(
                f"- {e.get('description', '')}"
                for e in context.today_events[-3:]
            )

        user_prompt = f"当前时间: {context.current_time.strftime('%H:%M')}{diary_context}{events_context}\n\n请生成一个符合世界观的生活事件，并通过 record_event 工具记录:"

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "record_event",
                    "description": "记录生成的生活事件",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "description": {
                                "type": "string",
                                "description": "20-60字的生活事件描述",
                            },
                            "duration_minutes": {
                                "type": "integer",
                                "minimum": 0,
                                "maximum": 2880,
                                "description": "事件持续时间（分钟），0 表示瞬时事件，最多 48 小时",
                            },
                        },
                        "required": ["description", "duration_minutes"],
                    },
                },
            }
        ]

        try:
            # 使用强制 tool_choice 确保只发一轮请求
            content, metadata = await self.llm_router.generate_with_forced_tool(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                tools=tools,
                tool_name="record_event",
                model_tier=ModelTier.AUXILIARY,
                temperature=0.8,
            )

            args = json.loads(content)
            description = str(args.get("description", "")).strip().strip('"').strip("'")
            if not description:
                description = "我正在房间里休息。"
            duration_minutes = max(0, min(2880, int(args.get("duration_minutes", 0))))

            if len(description) > _EVENT_DESCRIPTION_MAX_LEN:
                description = description[:_EVENT_DESCRIPTION_MAX_LEN - 3] + "..."

            logger.debug(f"生成事件: {description}, duration={duration_minutes}")
            return EventGenerationResult(description=description, duration_minutes=duration_minutes)

        except Exception as e:
            logger.error(f"事件生成失败: {e}")
            return EventGenerationResult(description=f"我正在房间里休息。", duration_minutes=0)

    async def generate_event_reaction(
        self,
        event: str,
        character_name: str,
        character_description: str,
        share_policy: str = "optional",
        today_events: Optional[List[dict]] = None,
    ) -> EventReactionResult:
        """
        Character Agent: 通过 Function Calling 同时产出内心反应和分享欲望值。
        """
        system_prompt = f"""你是{character_name}。

角色设定:
{character_description}

请对发生的事件做出内心反应，并通过工具调用记录你的反应和分享欲望。
要求:
1. 使用第一人称"我"
2. 反应 30-80 字，表达真实感受
3. 反映角色性格特点
4. 分享欲望值 0~1，表示你想把这件事告诉用户的程度"""

        today_context = ""
        if today_events:
            today_context = "\n今天已发生事件:\n" + "\n".join(
                f"- {e.get('description', '')}" for e in today_events
            )

        user_prompt = f"{today_context}\n\n当前事件: {event}\n\n请先思考，然后通过 record_reaction 工具记录你的内心反应和分享欲望。"

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "record_reaction",
                    "description": "记录角色对事件的内心反应和分享欲望",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "reaction": {
                                "type": "string",
                                "description": "30-80 字的内心反应，仅用于日记和上下文",
                            },
                            "share_desire": {
                                "type": "number",
                                "minimum": 0.0,
                                "maximum": 1.0,
                                "description": "角色想把这件事告诉用户的欲望值，0~1",
                            },
                        },
                        "required": ["reaction", "share_desire"],
                    },
                },
            }
        ]

        try:
            content, metadata = await self.llm_router.generate_with_forced_tool(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                tools=tools,
                tool_name="record_reaction",
                model_tier=ModelTier.AUXILIARY,
                temperature=0.9,
            )

            args = json.loads(content)
            reaction = str(args.get("reaction", "")).strip().strip('"').strip("'")
            if not reaction:
                reaction = f"（{character_name}默默地想着这件事）"
            share_desire = max(0.0, min(1.0, float(args.get("share_desire", 0.0))))

            if len(reaction) > 80:
                reaction = reaction[:77] + "..."

            logger.debug(f"生成反应: {reaction}, share_desire={share_desire}")
            return EventReactionResult(reaction=reaction, share_desire=share_desire)

        except Exception as e:
            logger.error(f"反应生成失败: {e}")
            if share_policy == "required":
                fallback_desire = 1.0
            elif share_policy == "never":
                fallback_desire = 0.0
            else:
                fallback_desire = 0.5
            return EventReactionResult(
                reaction=f"（{character_name}默默地想着这件事）",
                share_desire=fallback_desire,
            )

    async def generate_diary(
        self,
        events: List[dict],
        character_name: str,
        character_description: str,
        yesterday_diary: Optional[str] = None,
    ) -> str:
        """
        生成日记总结

        Args:
            events: 当天的所有事件和反应
            character_name: 角色名
            character_description: 角色描述
            yesterday_diary: 昨天的日记（可选）

        Returns:
            日记内容 (100-300 字)
        """
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

只输出日记内容，不要添加日期或标题。"""

        # 构建事件上下文
        events_text = "\n".join(
            f"- {e.get('description', '')}\n  我的反应: {e.get('reaction', '')}"
            for e in events
        )

        yesterday_context = ""
        if yesterday_diary:
            yesterday_context = f"\n\n昨天的日记:\n{yesterday_diary[:200]}..."

        user_prompt = f"今天发生的事情:\n{events_text}{yesterday_context}\n\n请写一篇日记总结今天:"

        try:
            response = await self.llm_router.generate(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model_tier=ModelTier.AUXILIARY,
                temperature=0.85,
            )

            diary = response.strip()
            if len(diary) > 300:
                diary = diary[:297] + "..."

            logger.info(f"生成日记: {len(diary)} 字")
            return diary

        except Exception as e:
            logger.error(f"日记生成失败: {e}")
            return f"今天发生了一些事，但我太累了，简单记录一下。"

    async def generate_share_message(self, context: ShareMessageContext) -> Optional[str]:
        """
        为指定目标生成个性化分享消息。

        使用 AUXILIARY tier 模型，通过 generate_with_forced_tool 强制输出。
        默认单次 10 秒超时、最多 2 次重试，实际值受配置项
        proactive_share_timeout_seconds / proactive_share_max_retries /
        proactive_share_backoff_base_seconds 控制。
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

关系亲密度（warmth_label）对应的语气参考：
- "冷淡" / "陌生"：简短、礼貌、不过界
- "一般" / "友好"：自然、可带轻微关心
- "亲近" / "亲密"：放松、可撒娇、可调侃、可分享糗事
{few_shot_block}"""

        user_prompt = f"""以下是你刚才经历的事：
{context.event_description}

你的内心反应：
{context.reaction}

对方信息：
- 关系分数: {context.relationship_score:.0f}/100
- 亲密度标签: {context.warmth_label}

已知关于对方的事实：
{context.user_profile_facts}

最近对话：
{context.recent_history}

消息类型: {context.message_type}
当前环境: {context.environment}

请写一条你要发给对方的消息。只输出消息内容，不要解释。"""

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "record_share_message",
                    "description": "记录为指定用户生成的分享消息。20-60字的第一人称口语消息，禁止出现角色名和第三人称描写",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "message": {
                                "type": "string",
                                "description": "20-60字的分享消息",
                            },
                        },
                        "required": ["message"],
                    },
                },
            }
        ]

        max_retries = self.config.proactive_share_max_retries if self.config else _DEFAULT_SHARE_RETRIES
        timeout_seconds = self.config.proactive_share_timeout_seconds if self.config else _DEFAULT_SHARE_TIMEOUT
        backoff_base = self.config.proactive_share_backoff_base_seconds if self.config else _DEFAULT_SHARE_BACKOFF_BASE
        max_chars = self.config.proactive_share_max_chars if self.config else _DEFAULT_SHARE_MAX_CHARS

        max_chars = max(10, max_chars)

        for attempt in range(1, max_retries + 2):  # 原始 + max_retries 次重试
            content, metadata = await self.llm_router.generate_with_forced_tool(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                tools=tools,
                tool_name="record_share_message",
                model_tier=ModelTier.AUXILIARY,
                temperature=0.85,
                timeout=timeout_seconds,
            )
            try:
                args = json.loads(content)
            except json.JSONDecodeError as je:
                logger.warning(
                    f"分享消息 JSON 解析失败（第{attempt}次）: {je}, "
                    f"content={content[:100]!r}"
                )
                if attempt < max_retries + 1:
                    backoff = backoff_base ** attempt
                    await asyncio.sleep(backoff)
                continue

            message = str(args.get("message", "")).strip().strip('"').strip("'")
            if not message:
                logger.warning(f"分享消息生成结果为空（第{attempt}次）")
                if attempt < max_retries + 1:
                    backoff = backoff_base ** attempt
                    await asyncio.sleep(backoff)
                continue

            if len(message) > max_chars:
                original_len = len(message)
                message = message[:max_chars - 3] + "..."
                logger.warning(
                    f"分享消息长度超限({original_len}/{max_chars})，已截断为 {len(message)} 字"
                )
            logger.debug(f"生成分享消息: {message[:50]}...")
            return message

        return None
