"""故障分类器：基于信号组合推断 possible_cause。

不做 retcode → fault_type 的严格映射。
LLOneBot 对不同的根因（登录过期/冻结/风控）可能返回同一个 retcode。
"""

from enum import Enum
from typing import Literal


class FaultTrigger(str, Enum):
    """故障触发路径，用于恢复条件判定。"""

    SEND_FAILURE = "send_failure"       # 连续 ActionFailed
    WS_DISCONNECT = "ws_disconnect"     # on_bot_disconnect
    HEARTBEAT_TIMEOUT = "heartbeat_timeout"  # 心跳超时


PossibleCause = Literal[
    "likely_login_expired",
    "likely_ws_disconnected",
    "ws_disconnected",
    "heartbeat_timeout",
    "unknown",
]


def classify(trigger: FaultTrigger, heartbeat_ok: bool) -> PossibleCause:
    """基于故障触发路径和心跳状态推断 possible_cause。

    heartbeat_ok: 心跳正常（最近 heartbeat_timeout_seconds 内有心跳）

    返回 possible_cause 字符串，可能值：
        likely_login_expired, likely_ws_disconnected,
        ws_disconnected, heartbeat_timeout, unknown
    """
    if trigger == FaultTrigger.SEND_FAILURE:
        if heartbeat_ok:
            return "likely_login_expired"
        else:
            return "likely_ws_disconnected"
    elif trigger == FaultTrigger.WS_DISCONNECT:
        return "ws_disconnected"
    elif trigger == FaultTrigger.HEARTBEAT_TIMEOUT:
        return "heartbeat_timeout"
    return "unknown"
