"""chat 模块共享类型/辅助 — 供 orchestrator 与 chat_agent 共用，避免双向依赖。

`ChatOutcome`（chat 调用结果）与 `_client_has_quota`（配额能力探测）原本定义在
orchestrator，被抽出的 ChatAgent 反向 import 形成模块环（靠 orchestrator 侧延迟
import 打破）。下沉到本中立模块后两侧均从此导入，orchestrator 得以顶层导入
ChatAgent、消除延迟导入。orchestrator 顶部 re-export 二者以保持既有导入路径
（command / factory / 测试仍 `from .chat.orchestrator import ChatOutcome`）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Optional


@dataclass
class ChatCallContext:
    """chat() 调用上下文 — 收敛透传参数，减少多层签名变更。

    收敛原先分散在多层调用链中的 is_command / image_data_urls /
    transient_message / nickname 参数。
    """

    is_command: bool = False
    image_data_urls: Optional[List[str]] = None
    transient_message: Optional[str] = None
    nickname: str = ""
    # 入站 hook 在 message_stream 写入并成功 append_visible 后返回的权威行 ID。
    # None 明确表示本次调用没有 hook 成功证据，不能用内容相同的历史 ref 猜测。
    inbound_message_stream_id: Optional[int] = None


@dataclass(frozen=True)
class ChatOutcome:
    """chat 调用结果。

    不携带待发送文本；用户可见内容必须已经由 delivery 发送。
    """

    status: Literal["sent", "skipped", "empty", "failed", "partial_sent"]
    sent_count: int = 0
    reason: str = ""
    counts_as_interaction: bool = False

    @property
    def skipped(self) -> bool:
        return self.status == "skipped"

    @property
    def sent(self) -> bool:
        return self.status in {"sent", "partial_sent"} and self.sent_count > 0

    @property
    def empty_reply(self) -> bool:
        return self.status == "empty"


def _client_has_quota(client) -> bool:
    """判断文本客户端是否配置了配额功能（排除 mock 对象）。"""
    from unittest.mock import Mock
    if isinstance(client, Mock):
        return False
    return getattr(client, "quota_check_enabled", False) and getattr(client, "data_store", None) is not None
