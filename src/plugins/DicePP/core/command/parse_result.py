"""
CommandParseResult — 统一命令解析输出结构 (Task 2.1)

字段层级：
  - 元信息层：command_name, raw
  - 词法层：tokens  （原始切分，不做语义解释）
  - 语义层：flags, kwargs, mentions, segments, args, tail_text
  - 错误层：issues

权威字段定义见 openspec/changes/refactor-command-parsing-all-commands/design.md 决策2。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# 辅助数据类
# ---------------------------------------------------------------------------

@dataclass
class MentionInfo:
    """@提及目标的结构化表示"""
    user_id: str
    display_name: str = ""

    def __repr__(self) -> str:
        return f"MentionInfo(user_id={self.user_id!r}, display_name={self.display_name!r})"


@dataclass
class MessageSegment:
    """消息分段（文本/AT/图片/CQ 码等）的通用表示"""

    #: 分段类型，如 "text" / "at" / "image" / "cq"
    seg_type: str

    #: 分段携带的数据，格式依 seg_type 而定
    data: Dict[str, Any] = field(default_factory=dict)

    # 便捷工厂方法
    @classmethod
    def text(cls, content: str) -> "MessageSegment":
        return cls(seg_type="text", data={"text": content})

    @classmethod
    def at(cls, user_id: str, display_name: str = "") -> "MessageSegment":
        return cls(seg_type="at", data={"user_id": user_id, "display_name": display_name})

    @classmethod
    def image(cls, url: str) -> "MessageSegment":
        return cls(seg_type="image", data={"url": url})

    @classmethod
    def cq(cls, cq_type: str, params: Dict[str, str]) -> "MessageSegment":
        return cls(seg_type="cq", data={"cq_type": cq_type, "params": params})

    def __repr__(self) -> str:
        return f"MessageSegment(type={self.seg_type!r}, data={self.data!r})"


@dataclass
class ParseIssue:
    """结构化解析问题（警告或错误）"""

    #: 问题类型："warning" | "error"
    issue_type: str

    #: 错误码（如 "UNKNOWN_FLAG" / "INVALID_ARG_TYPE"）
    code: str

    #: 人类可读的错误描述（用于日志/调试，不直接回复用户）
    message: str

    #: 关联的 token 索引（-1 表示未定位）
    token_index: int = -1

    #: 是否可恢复：True 表示解析层已降级处理，命令仍可执行；False 表示致命错误
    recoverable: bool = True

    def __repr__(self) -> str:
        return (
            f"ParseIssue({self.issue_type!r}, code={self.code!r}, "
            f"recoverable={self.recoverable}, msg={self.message!r})"
        )


# ---------------------------------------------------------------------------
# 主数据结构
# ---------------------------------------------------------------------------

@dataclass
class CommandParseResult:
    """
    统一命令解析输出结构。

    所有已迁移命令的解析层须返回此结构，命令适配层消费 args/flags/kwargs/tail_text。
    不应直接解析 tokens 以替代 args——tokens 仅供调试与回溯。
    """

    # ── 元信息层 ─────────────────────────────────────────────────────────
    #: 触发命令的名称（如 "r" / "mode"），由前缀剥离后确定
    command_name: str = ""

    #: 去掉 bot 前缀后的原始输入，不做任何处理
    raw: str = ""

    # ── 词法层 ────────────────────────────────────────────────────────────
    #: 原始输入按空白切分的词法单元列表，不做语义解释，保留原始字面值
    tokens: List[str] = field(default_factory=list)

    # ── 语义层 ────────────────────────────────────────────────────────────
    #: 布尔标志集合（已识别的 flags，如 {"h", "s"}）
    flags: Set[str] = field(default_factory=set)

    #: 键值型参数（如 {"reason": "攻击地精"}）
    kwargs: Dict[str, str] = field(default_factory=dict)

    #: AT 提及目标快捷视图（是 segments 中 AT 类型片段的结构化列表）
    mentions: List[MentionInfo] = field(default_factory=list)

    #: 完整消息分段列表（含文本/AT/图片/CQ 码）
    segments: List[MessageSegment] = field(default_factory=list)

    #: 位置参数列表（已剥离 flags/kwargs/tail_text 后的主参数）
    args: List[str] = field(default_factory=list)

    #: 尾部自由文本（如掷骰原因、备注）
    tail_text: str = ""

    # ── 错误层 ────────────────────────────────────────────────────────────
    #: 结构化解析问题列表
    issues: List[ParseIssue] = field(default_factory=list)

    # ── 便捷属性 ──────────────────────────────────────────────────────────

    @property
    def has_errors(self) -> bool:
        """是否存在不可恢复的错误"""
        return any(i.issue_type == "error" and not i.recoverable for i in self.issues)

    @property
    def has_warnings(self) -> bool:
        """是否存在警告"""
        return any(i.issue_type == "warning" for i in self.issues)

    def add_warning(self, code: str, message: str, token_index: int = -1) -> None:
        """追加一条可恢复警告"""
        self.issues.append(ParseIssue(
            issue_type="warning", code=code, message=message,
            token_index=token_index, recoverable=True
        ))

    def add_error(self, code: str, message: str,
                  token_index: int = -1, recoverable: bool = False) -> None:
        """追加一条错误"""
        self.issues.append(ParseIssue(
            issue_type="error", code=code, message=message,
            token_index=token_index, recoverable=recoverable
        ))

    def first_arg(self, default: str = "") -> str:
        """返回第一个位置参数，不存在时返回 default"""
        return self.args[0] if self.args else default

    def has_flag(self, flag: str) -> bool:
        """检查是否包含指定 flag"""
        return flag in self.flags

    def get_kwarg(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """获取键值参数，不存在时返回 default"""
        return self.kwargs.get(key, default)

    def __repr__(self) -> str:
        return (
            f"CommandParseResult("
            f"cmd={self.command_name!r}, "
            f"args={self.args!r}, "
            f"flags={self.flags!r}, "
            f"tail_text={self.tail_text!r}, "
            f"issues={len(self.issues)})"
        )
