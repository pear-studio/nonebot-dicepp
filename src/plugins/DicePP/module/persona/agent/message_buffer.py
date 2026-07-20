"""MessageBuffer — 管理 initial messages 和运行时 message_delta

职责：
- 封装初始消息和运行时新增消息，避免手动双写。
- 初始消息不可变；所有新增消息通过 add_message / add_messages 追加。
- 提供 get_messages()（完整列表）和 get_delta()（仅新增消息）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .runtime_types import ModelTurn


@dataclass
class MessageBuffer:
    """消息缓冲区 — runtime 内部使用"""

    _initial: list[dict] = field(default_factory=list)
    _delta: list[dict] = field(default_factory=list)

    @classmethod
    def from_initial(cls, messages: list[dict]) -> "MessageBuffer":
        return cls(_initial=list(messages), _delta=[])

    def add_message(self, msg: dict) -> None:
        self._delta.append(msg)

    def add_messages(self, msgs: list[dict]) -> None:
        self._delta.extend(msgs)

    def add_model_turn(self, turn: ModelTurn) -> None:
        """追加一个完整的结构化 assistant turn。"""
        self._delta.append(turn.to_message())

    def get_messages(self) -> list[dict]:
        """返回完整消息列表（initial + delta）"""
        return self._initial + self._delta

    def get_delta(self) -> list[dict]:
        """返回仅新增的消息（不含 initial）"""
        return list(self._delta)

    @property
    def initial(self) -> list[dict]:
        return list(self._initial)

    @property
    def delta(self) -> list[dict]:
        return list(self._delta)
