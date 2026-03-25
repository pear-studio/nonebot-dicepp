"""
CqExtractor — CQ 码 / AT 提及结构化抽取 (Task 2.3)

职责：
  - 从消息的 raw_msg（含 CQ 码）中解析 MessageSegment 列表
  - 从 AT 类型的 segments 中构建 MentionInfo 快捷视图
  - 将抽取结果写入 CommandParseResult.segments / mentions

解析层（CommandTextParser）只处理 plain_msg（纯文本），
本模块处理 raw_msg（原始带 CQ 码的消息），二者分工明确。
"""
from __future__ import annotations

import re
from typing import List, Tuple

from core.command.parse_result import CommandParseResult, MentionInfo, MessageSegment

# CQ 码全匹配正则：[CQ:type,key=value,...]
_CQ_RE = re.compile(r'\[CQ:([a-zA-Z0-9_]+)(?:,([^\]]*))?\]')

# CQ 参数 key=value 切分
_CQ_PARAM_RE = re.compile(r'([a-zA-Z0-9_]+)=([^,\]]*)')


def _parse_cq_params(params_str: str) -> dict:
    """将 CQ 参数字符串解析为字典，如 "qq=12345,name=测试" → {"qq": "12345", "name": "测试"}"""
    if not params_str:
        return {}
    return {m.group(1): m.group(2) for m in _CQ_PARAM_RE.finditer(params_str)}


def extract_segments(raw_msg: str) -> List[MessageSegment]:
    """
    从含 CQ 码的原始消息字符串中提取 MessageSegment 列表。

    示例：
      "[CQ:at,qq=12345]d20+4 攻击" → [AT(12345), Text("d20+4 攻击")]
    """
    segments: List[MessageSegment] = []
    last_end = 0

    for m in _CQ_RE.finditer(raw_msg):
        # 前面的纯文本片段
        if m.start() > last_end:
            text_part = raw_msg[last_end:m.start()]
            if text_part:
                segments.append(MessageSegment.text(text_part))

        cq_type = m.group(1)
        params = _parse_cq_params(m.group(2) or "")

        if cq_type == "at":
            user_id = params.get("qq", "")
            display_name = params.get("name", "")
            segments.append(MessageSegment.at(user_id=user_id, display_name=display_name))
        elif cq_type == "image":
            url = params.get("url", params.get("file", ""))
            segments.append(MessageSegment.image(url=url))
        else:
            segments.append(MessageSegment.cq(cq_type=cq_type, params=params))

        last_end = m.end()

    # 尾部剩余文本
    if last_end < len(raw_msg):
        tail = raw_msg[last_end:]
        if tail:
            segments.append(MessageSegment.text(tail))

    # 无 CQ 码的纯文本消息
    if not segments and raw_msg:
        segments.append(MessageSegment.text(raw_msg))

    return segments


def extract_mentions(segments: List[MessageSegment]) -> List[MentionInfo]:
    """
    从 segments 中提取所有 AT 类型分段，构建 MentionInfo 快捷视图。
    """
    mentions: List[MentionInfo] = []
    for seg in segments:
        if seg.seg_type == "at":
            user_id = seg.data.get("user_id", "")
            display_name = seg.data.get("display_name", "")
            if user_id:
                mentions.append(MentionInfo(user_id=user_id, display_name=display_name))
    return mentions


def enrich_parse_result(result: CommandParseResult, raw_msg: str) -> None:
    """
    将 CQ 码抽取结果写入已有的 CommandParseResult。

    命令适配层在拿到 parse_result 后调用此函数：
        enrich_parse_result(result, meta.raw_msg)

    之后可通过 result.mentions / result.segments 消费结构化数据。
    """
    result.segments = extract_segments(raw_msg)
    result.mentions = extract_mentions(result.segments)
