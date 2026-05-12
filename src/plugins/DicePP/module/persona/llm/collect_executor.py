"""Collect Executor — 收集工具调用参数，不执行副作用。

用于分享/事件/反应/日记/评分/观察等"只收集不执行"的场景。
"""
from typing import List, Dict

from .client import ToolCallInfo


class CollectExecutor:
    """收集所有工具调用参数，不执行副作用。"""

    def __init__(self):
        self.collected: List[Dict] = []

    async def __call__(self, tool_calls: List[ToolCallInfo]) -> List[Dict]:
        results = []
        for tc in tool_calls:
            self.collected.append({
                "name": tc["name"],
                "arguments": tc["arguments"],
            })
            results.append({
                "tool_call_id": tc["id"],
                "content": '{"status": "ok"}',
            })
        return results
