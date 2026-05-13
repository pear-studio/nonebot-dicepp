"""收集型工具 executor — 记录参数到可变列表，不产生副作用"""
from typing import List


def make_collecting_executor(results: List[dict]):
    """返回一个收集型 executor，每次调用时将 args 存入 results 列表。

    Args:
        results: 可变列表，由调用方创建和读取
    """
    async def executor(args: dict, ctx) -> str:
        results.append(args)
        return '{"status": "ok"}'
    return executor
