"""Life 域收集型工具 — 无副作用，仅收集 LLM 结构化输出"""
from typing import List
from nonebot.log import logger
from .registry import ToolDef


RECORD_EVENT_TOOL = ToolDef(
    name="record_event",
    description="记录生成的生活事件及其对角色状态的影响",
    parameters={
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "事件描述，自然叙事，不强制字数上限但保持简洁",
            },
            "context_summary": {
                "type": "string",
                "minLength": 1,
                "description": "事件摘要，30-60字，仅包含关键事实（谁、在哪、做了什么、结果），用于聊天上下文注入",
            },
            "duration_minutes": {
                "type": "integer",
                "minimum": 0,
                "maximum": 2880,
                "description": "事件持续时间（分钟），0 表示瞬时事件，最多 48 小时",
            },
            "energy_delta": {
                "type": "integer",
                "minimum": -20,
                "maximum": 20,
                "description": "事件对体力的影响（可选，范围-20~+20）",
            },
            "mood_delta": {
                "type": "integer",
                "minimum": -20,
                "maximum": 20,
                "description": "事件对心情的影响（可选，范围-20~+20）",
            },
            "health_delta": {
                "type": "integer",
                "minimum": -20,
                "maximum": 20,
                "description": "事件对健康的影响（可选，范围-20~+20）",
            },
        },
        "required": ["description", "context_summary", "duration_minutes"],
    },
)

RECORD_REACTION_TOOL = ToolDef(
    name="record_reaction",
    description="记录角色对事件的内心反应、分享欲望、行动倾向和意向更新",
    parameters={
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
                "description": (
                    "角色主动想把这件事说出去的程度，0~1。锚点："
                    "0.0-0.2 纯个人日常/重复琐事，没必要说；"
                    "0.3-0.4 顺嘴可提的小事，被问才会说；"
                    "0.5-0.6 自然想提起的事，聊起来会主动提（小心情/新发现/吐槽）；"
                    "0.7-0.8 比较强的分享冲动（做了决定/情绪波动想找人说/小成就）；"
                    "0.9-1.0 迫不及待想说出去（强烈情绪/期待已久的成就感/兴奋念头）。"
                    "重复的日常动作给低分，依据是'分享价值'非'事件戏剧性'。"
                ),
            },
            "follow_up_action": {
                "type": ["string", "null"],
                "description": "根据当前情况，角色决定做并且已经开始做的事。如果有，填写具体描述，这会触发事件-反应链的续写。如果没有则填 null",
            },
            "pending_plan": {
                "type": ["string", "null"],
                "description": "角色产生的短期想法或计划，但还没有开始做。填写后会被记录到角色状态中供后续事件参考，但不会立即触发续写。null=保持当前备忘，空字符串=清空备忘，非空字符串=更新备忘",
            },
        },
        "required": ["reaction", "share_desire"],
    },
)

RECORD_DIARY_ENTRY_TOOL = ToolDef(
    name="record_diary_entry",
    description="记录日记内容",
    parameters={
        "type": "object",
        "properties": {
            "diary": {
                "type": "string",
                "description": "日记内容，100-300字，第一人称",
            },
        },
        "required": ["diary"],
    },
)

RECORD_SHARE_MESSAGE_TOOL = ToolDef(
    name="record_share_message",
    description="调用此工具输出你要发给对方的分享消息。20-60字的第一人称口语消息，禁止出现角色名和第三人称描写。不要直接回复文本，必须通过此工具输出。",
    parameters={
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "20-60字的分享消息",
            },
        },
        "required": ["message"],
    },
)


async def life_collecting_executor(args: dict, ctx) -> str:
    """life 域通用收集型 executor — 将 LLM 输出参数写入 ctx.collected_args"""
    if ctx is not None and ctx.collected_args is not None:
        ctx.collected_args.append(args)
    else:
        logger.warning(
            "life_collecting_executor: ctx 或 collected_args 为 None，数据丢弃"
        )
    return '{"status": "ok"}'


def make_collecting_executor(results: List[dict]):
    """返回一个收集型 executor，每次调用时将 args 存入 results 列表（闭包模式）。

    供 scoring.py 等非 life 路径使用——通过闭包捕获 results 列表，
    不依赖 ToolContext.collected_args。
    """
    async def executor(args: dict, ctx) -> str:
        results.append(args)
        return '{"status": "ok"}'
    return executor
