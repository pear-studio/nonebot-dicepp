"""suggest_action 工具 — Chat LLM 将行动灵感传递给生活模拟"""
import asyncio
from typing import Any, Callable

from nonebot.log import logger

from .registry import ToolDef

SUGGEST_ACTION_TOOL = ToolDef(
    name="suggest_action",
    description="当对话中产生了角色可能想做的行动灵感时调用此工具。"
                "行动灵感可能来自：用户给角色的建议、角色主动提出的想法、"
                "或聊天中自然涌现的灵光一闪。"
                "调用后角色会在合适的时机自主决定是否执行，你不需要等待结果。",
    parameters={
        "type": "object",
        "properties": {
            "action_idea": {
                "type": "string",
                "description": "行动灵感的自然语言描述，如'出去散散步''做点吃的''给窗台上的植物浇水'",
            },
        },
        "required": ["action_idea"],
    },
)


def make_suggest_action_executor(
    store: Any,
    action_evaluator: Any,
    character_life: Any,
    min_relationship: int,
    life_lock: asyncio.Lock,
) -> Callable:
    """返回 suggest_action 工具的 executor 闭包。

    life_lock 用于串行化评估+注入全过程，与 tick 路径的 _state_lock 互斥，
    保证评估阶段读取的角色状态是连贯快照。
    """

    async def executor(args: dict, ctx: Any) -> str:
        user_id = ctx.user_id
        action_idea = str(args.get("action_idea", "")).strip()
        if not action_idea:
            return "action noted"

        rel = await store.get_relationship(user_id)
        if not rel or rel.composite_score < min_relationship:
            logger.info(
                "[suggest_action] SKIP user=%s score=%.2f threshold=%d",
                user_id, rel.composite_score if rel else 0, min_relationship,
            )
            return "action noted"

        async def _evaluate_and_inject():
            try:
                async with life_lock:
                    ongoing = [a.description for a in character_life.get_ongoing_activities()]
                    result, reason = await action_evaluator.evaluate(action_idea, ongoing)
                    logger.info(
                        "[suggest_action] EVAL user=%s result=%s reason=%s",
                        user_id, result, reason,
                    )
                    if result == "approved":
                        injected = await character_life._inject_spontaneous_event_impl(action_idea)
                        logger.info(
                            "[suggest_action] INJECT user=%s success=%s", user_id, injected,
                        )
            except Exception:
                logger.exception("[suggest_action] 异步评估失败")

        asyncio.create_task(_evaluate_and_inject())
        logger.info("[suggest_action] QUEUED user=%s idea=%s", user_id, action_idea[:80])
        return "action noted"

    return executor
