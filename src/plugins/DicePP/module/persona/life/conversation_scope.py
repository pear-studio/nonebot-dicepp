"""ConversationScope — Conversation 的不可变范围标识

一个 scope 由 (namespace, key) 唯一确定，是 Conversation 定义的一部分：
- namespace 表达 Conversation 的用途域（群聊 / 私聊 / 未来的 life.*）
- key 表达该域内的隔离维度（group_id / user_id / actor_key）

同一 scope 同时最多一个 active Conversation；不同 scope 严格隔离。
DB 分列存 namespace/key，不拼接裸字符串。

阶段 1 只定义 chat 路径；life.*（life.dm / life.character）已实现。
"""

from __future__ import annotations

from dataclasses import dataclass

# ── namespace 常量 ──────────────────────────────────────
NS_CHAT_GROUP = "chat.group"
NS_CHAT_PRIVATE = "chat.private"
NS_LIFE_DM = "life.dm"
NS_LIFE_CHARACTER = "life.character"


@dataclass(frozen=True)
class ConversationScope:
    """不可变的 Conversation 范围值对象。

    frozen=True 使其可哈希、可作 dict key / set 成员，且创建后不可变更。
    """

    namespace: str
    key: str

    @classmethod
    def from_chat(cls, user_id: str, group_id: str) -> "ConversationScope":
        """从 chat 消息的 (user_id, group_id) 构造 scope。

        有 group_id → 群聊（同群所有参与者共享）；否则 → 私聊（按用户隔离）。
        """
        if group_id:
            return cls(NS_CHAT_GROUP, group_id)
        return cls(NS_CHAT_PRIVATE, user_id)

    @classmethod
    def for_group(cls, group_id: str) -> "ConversationScope":
        return cls(NS_CHAT_GROUP, group_id)

    @classmethod
    def for_private(cls, user_id: str) -> "ConversationScope":
        return cls(NS_CHAT_PRIVATE, user_id)

    @classmethod
    def for_life_dm(cls, character_id: str) -> "ConversationScope":
        """Life DM scope：按 character_id 隔离。"""
        return cls(NS_LIFE_DM, character_id)

    @classmethod
    def for_life_character(cls, character_id: str) -> "ConversationScope":
        """Life Character scope：按 character_id 隔离。"""
        return cls(NS_LIFE_CHARACTER, character_id)

    @property
    def is_group(self) -> bool:
        """群聊 scope（多个参与者共享）。"""
        return self.namespace == NS_CHAT_GROUP

    @property
    def is_private(self) -> bool:
        """私聊 scope（key 即 user_id）。"""
        return self.namespace == NS_CHAT_PRIVATE

    @property
    def is_life(self) -> bool:
        """Life scope（DM / Character）。"""
        return self.namespace.startswith("life.")
