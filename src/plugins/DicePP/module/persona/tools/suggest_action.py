"""suggest_action 工具 — Chat LLM 将行动灵感传递给生活模拟"""
import asyncio
from typing import Any, Callable

from ..agent.runtime_types import ToolSpec, ToolResult, ToolExecutionContext
from pydantic import BaseModel, Field

from utils.logger import logger

class _SuggestActionArgs(BaseModel):
    """行动灵感参数"""
    action_idea: str = Field(..., description="行动灵感的自然语言描述，如'出去散散步''做点吃的''给窗台上的植物浇水'")


async def _suggest_action_handler(parsed: BaseModel, ctx: ToolExecutionContext) -> ToolResult:
    return ToolResult(observation="ok")


SUGGEST_ACTION_TOOL = ToolSpec(
    name="suggest_action",
    description="当对话中产生了角色可能想做的行动灵感时调用此工具。"
                "行动灵感可能来自：用户给角色的建议、角色主动提出的想法、"
                "或聊天中自然涌现的灵光一闪。"
                "调用后角色会在合适的时机自主决定是否执行，你不需要等待结果。",
    args_schema=_SuggestActionArgs,
    handler=_suggest_action_handler,
)


def build_suggest_action_tool(
    store, action_evaluator, character_life, min_relationship: int,
    life_lock: asyncio.Lock, user_id: str = "",
) -> ToolSpec:
    """构建 suggest_action 工具 (T6 新路径)"""

    async def handler(parsed: BaseModel, ctx: ToolExecutionContext) -> ToolResult:
        action_idea = str(parsed.action_idea or "").strip()
        if not action_idea:
            return ToolResult(observation="action noted")

        rel = await store.get_relationship(user_id)
        if not rel or rel.composite_score < min_relationship:
            logger.info(
                "[suggest_action] SKIP user=%s score=%.2f threshold=%d",
                user_id, rel.composite_score if rel else 0, min_relationship,
            )
            return ToolResult(observation="action noted")

        async def _evaluate_and_inject():
            try:
                async with life_lock:
                    ongoing = [a.description for a in character_life.get_ongoing_activities()]
                    result, reason = await action_evaluator.evaluate(action_idea, ongoing, user_id=user_id)
                    logger.info(
                        "[suggest_action] EVAL user=%s result=%s reason=%s",
                        user_id, result, reason,
                    )
                    if result == "approved":
                        injected = await character_life._inject_spontaneous_event_impl(action_idea)
                        logger.info(
                            "[suggest_action] INJECT user={} success={}", user_id, injected,
                        )
            except Exception:
                logger.exception("[suggest_action] 异步评估失败")

        asyncio.create_task(_evaluate_and_inject())
        logger.info("[suggest_action] QUEUED user={} idea={}", user_id, action_idea[:80])
        return ToolResult(observation="action noted")

    return ToolSpec(
        name="suggest_action",
        description="当对话中产生了角色可能想做的行动灵感时调用此工具。"
                    "行动灵感可能来自：用户给角色的建议、角色主动提出的想法、"
                    "或聊天中自然涌现的灵光一闪。"
                    "调用后角色会在合适的时机自主决定是否执行，你不需要等待结果。",
        args_schema=_SuggestActionArgs,
        handler=handler,
    )
