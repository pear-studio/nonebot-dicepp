r"""LLM 响应 JSON 解析的统一容错实现

设计目的：消除 chat/scoring.py 与 life/observation.py 中重复的 3 级
容错代码，统一使用此模块以降低维护成本。

3 级容错策略
============
1. 直接 ``json.loads(text)``。
2. 去除 markdown ``\`\`\`json ... \`\`\``` 围栏 + 前缀 ``json`` 后再解析。
3. 括号计数提取首个完整 JSON 对象 / 数组（带 ``in_string`` / ``escape``
   状态跟踪，避免值中含 ``}`` 或 ``\\"`` 时计数误判）。

使用约束
========
- 仅适用于 **自由文本响应**（如 chat 完成、辅助模型直出），LLM 可能添加
  ``\`\`\`json`` 围栏或前后多余空白。
- **不适用于 tool-call 结构化输出**（``tool_choice="required"`` 多轮路径），
  那些路径返回的 ``arguments`` 已是合法 JSON 字符串，
  应直接用 ``json.loads(content)`` 包 ``try/except json.JSONDecodeError``，
  无需 markdown 围栏处理。
"""
from __future__ import annotations

import json
from plugins.DicePP.utils.logger import logger
import re
from typing import Any, Optional


def _strip_markdown_fence(text: str) -> str:
    """去除 ```json ... ``` 围栏和裸 ``json`` 前缀"""
    cleaned = re.sub(r'```json\s*|\s*```', '', text, flags=re.DOTALL)
    cleaned = re.sub(r'^[\s\n]*json\s*', '', cleaned, flags=re.DOTALL)
    return cleaned.strip()


def _extract_balanced(text: str, start_char: str, end_char: str) -> Optional[str]:
    """括号计数提取首个完整结构，处理字符串内的 ``"`` / ``\\"`` 转义"""
    start = text.find(start_char)
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == '\\':
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if not in_string:
            if ch == start_char:
                depth += 1
            elif ch == end_char:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return None


def safe_json_loads(text: str, fallback: Any = None, *, log_prefix: str = "") -> Any:
    """3 级容错地解析 LLM 自由文本响应中的 JSON。

    Args:
        text: LLM 响应文本，可能含 markdown 围栏 / 前后噪声。
        fallback: 全部尝试失败后返回的兜底值（默认 None）。
        log_prefix: 日志前缀，便于定位来自哪个调用点。

    Returns:
        解析结果（可能是 dict/list/标量），全部失败返回 ``fallback``。
    """
    # 尝试 1：直接解析
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # 尝试 2：去除 markdown 围栏
    try:
        cleaned = _strip_markdown_fence(text)
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 尝试 3：括号计数提取（先试数组再试对象）
    for start_char, end_char in (('[', ']'), ('{', '}')):
        snippet = _extract_balanced(text, start_char, end_char)
        if snippet is None:
            continue
        try:
            return json.loads(snippet)
        except json.JSONDecodeError:
            continue

    logger.warning(
        "%sJSON 解析失败，返回兜底值. raw=%r",
        f"{log_prefix} " if log_prefix else "",
        text[:200],
    )
    return fallback
