"""
事件生成 Agent

System Agent: 生成客观生活事件
Character Agent: 生成角色对事件的反应
"""
from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime
import json
import logging

from ..llm.router import LLMRouter
from ..data.models import ModelTier

logger = logging.getLogger("persona.event_agent")


@dataclass
class EventGenerationResult:
    description: str = ""
    duration_minutes: int = 0


@dataclass
class EventReactionResult:
    reaction: str = ""
    share_desire: float = 0.0


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

    def __init__(self, llm_router: LLMRouter):
        self.llm_router = llm_router

    async def generate_event(self, context: EventContext) -> str:
        """
        System Agent: 生成客观生活事件

        DEPRECATED: 请使用 generate_event_result() 获取结构化数据。

        Returns:
            事件描述 (20-50 字)
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
1. 以第一人称"我"描述正在做什么或刚做了什么
2. 20-50字，简洁具体
3. 符合世界观和场景设定
4. 可以是日常琐事或有趣遭遇

只输出事件描述，不要解释。"""

        # 构建对话历史上下文
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

        user_prompt = f"当前时间: {context.current_time.strftime('%H:%M')}{diary_context}{events_context}\n\n请生成一个符合世界观的生活事件:"

        try:
            response = await self.llm_router.generate(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model_tier=ModelTier.AUXILIARY,
                temperature=0.8,
            )

            # 清理响应
            event = response.strip().strip('"').strip("'")
            if len(event) > 50:
                event = event[:47] + "..."

            logger.debug(f"生成事件: {event}")
            return event

        except Exception as e:
            logger.error(f"事件生成失败: {e}")
            # 返回默认事件
            return f"我正在房间里休息。"

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
1. 以第一人称"我"描述正在做什么或刚做了什么
2. 20-50字，简洁具体
3. 符合世界观和场景设定
4. 可以是日常琐事或有趣遭遇

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
                                "description": "20-50字的生活事件描述",
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

            if len(description) > 50:
                description = description[:47] + "..."

            logger.debug(f"生成事件: {description}, duration={duration_minutes}")
            return EventGenerationResult(description=description, duration_minutes=duration_minutes)

        except Exception as e:
            logger.error(f"事件生成失败: {e}")
            return EventGenerationResult(description=f"我正在房间里休息。", duration_minutes=0)

    async def generate_reaction(
        self,
        event: str,
        character_name: str,
        character_description: str,
        today_events: Optional[List[dict]] = None,
    ) -> str:
        """
        Character Agent: 生成角色对事件的反应

        DEPRECATED: 请使用 generate_event_reaction() 获取结构化数据。

        Returns:
            角色内心反应 (30-80 字)
        """
        system_prompt = f"""你是{character_name}。

角色设定:
{character_description}

请对发生的事件做出内心反应。
要求:
1. 使用第一人称"我"
2. 30-80字，表达真实感受
3. 反映角色性格特点
4. 可以是想法、感受或简短独白

只输出反应内容，不要解释。"""

        # 构建当天上下文
        today_context = ""
        if today_events:
            today_context = "\n今天已发生事件:\n" + "\n".join(
                f"- {e.get('description', '')}" for e in today_events
            )

        user_prompt = f"{today_context}\n\n当前事件: {event}\n\n你的内心反应是:"

        try:
            response = await self.llm_router.generate(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model_tier=ModelTier.AUXILIARY,
                temperature=0.9,
            )

            reaction = response.strip().strip('"').strip("'")
            if len(reaction) > 80:
                reaction = reaction[:77] + "..."

            logger.debug(f"生成反应: {reaction}")
            return reaction

        except Exception as e:
            logger.error(f"反应生成失败: {e}")
            return f"（{character_name}默默地想着这件事）"

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
