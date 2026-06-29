"""
LLM 路由基础设施 — life/ 包内共享，非公共 API

供 Agent 基类和子类在包内共用。提供：
- _run_life_collect_loop: 通过 tool-bridge 收集结构化输出

注意：此模块不应被 life/ 包外的代码导入。
"""
from typing import Any, Optional
from ..llm.router import LLMRouter
from ..llm.selection import SelectionPolicy
from ..data.store import PersonaDataStore

# keep in sync with PersonaConfig.background_llm_timeout_seconds default
_DEFAULT_BG_TIMEOUT = 90


async def _run_life_collect_loop(
    router: LLMRouter,
    store: PersonaDataStore,
    messages: list,
    tools: list,
    temperature: float,
    selection: SelectionPolicy,
    bg_timeout: int = _DEFAULT_BG_TIMEOUT,
    max_rounds: int = 10,
    extra_registry: Optional[Any] = None,
) -> list:
    """LLM 路由基础设施 — 通过 tool-bridge 收集结构化输出

    Args:
        router: LLM 路由器
        store: 数据存储
        messages: [system, user] 消息列表
        tools: OpenAI 格式工具定义
        temperature: 采样温度
        selection: 模型选择策略
        bg_timeout: 超时秒数
        max_rounds: 最大轮次
        extra_registry: 额外工具注册表（只读查询工具）

    Returns:
        collected_args: 收集的 LLM 工具调用参数列表
    """
    from ..agent.tool_bridge import run_structured_collect
    from utils.logger import logger

    tool_name = ""
    if not tools:
        return []
    first = tools[0]
    if isinstance(first, dict):
        func = first.get("function", first)
        tool_name = func.get("name", "")

    collected, run_result = await run_structured_collect(
        router=router,
        store=store,
        messages=messages,
        temperature=temperature,
        timeout=bg_timeout,
        selection=selection,
        required_tools=[tool_name] if tool_name else None,
        max_rounds=max_rounds,
        extra_registry=extra_registry,
    )
    run_result.log_if_failed(str(tool_name))
    return collected
