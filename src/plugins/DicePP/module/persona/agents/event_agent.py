"""
事件生成 Agent

System Agent: 生成客观生活事件
Character Agent: 生成角色对事件的反应
"""
from typing import List, Optional
from datetime import datetime
import logging

from ..llm.router import LLMRouter
from ..data.models import ModelTier

logger = logging.getLogger("persona.event_agent")


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
        self.current_time = current_time or datetime.now()


class EventGenerationAgent:
    """事件生成 Agent - 使用辅助模型"""

    def __init__(self, llm_router: LLMRouter):
        self.llm_router = llm_router

    async def generate_event(self, context: EventContext) -> str:
        """
        System Agent: 生成客观生活事件

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
1. 以第三人称客观描述发生了什么
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
            return f"{context.character_name}正在房间里休息。"

    async def generate_reaction(
        self,
        event: str,
        character_name: str,
        character_description: str,
        today_events: Optional[List[dict]] = None,
    ) -> str:
        """
        Character Agent: 生成角色对事件的反应

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
