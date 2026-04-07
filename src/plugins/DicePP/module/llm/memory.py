"""
LLM 对话记忆模块 - 管理用户对话历史
"""

from typing import List, Dict
from collections import deque


class SimpleMemory:
    """简单的内存对话历史管理"""

    def __init__(self, max_size: int = 20):
        """
        初始化记忆模块

        Args:
            max_size: 每个用户的最大历史记录数
        """
        self.max_size = max_size
        self._storage: Dict[str, deque] = {}

    def _get_key(self, user_id: str, group_id: str = "") -> str:
        """
        生成存储键

        Args:
            user_id: 用户 ID
            group_id: 群组 ID（可选）

        Returns:
            存储键
        """
        return f"{user_id}:{group_id}" if group_id else user_id

    def get_history(self, user_id: str, group_id: str = "") -> List[dict]:
        """
        获取对话历史

        Args:
            user_id: 用户 ID
            group_id: 群组 ID（可选）

        Returns:
            消息列表
        """
        key = self._get_key(user_id, group_id)
        if key not in self._storage:
            return []
        return list(self._storage[key])

    def add_message(self, user_id: str, role: str, content: str, group_id: str = ""):
        """
        添加消息到历史

        Args:
            user_id: 用户 ID
            role: 角色（system/user/assistant）
            content: 消息内容
            group_id: 群组 ID（可选）
        """
        key = self._get_key(user_id, group_id)
        if key not in self._storage:
            self._storage[key] = deque(maxlen=self.max_size)
        self._storage[key].append({"role": role, "content": content})

    def clear(self, user_id: str, group_id: str = ""):
        """
        清空对话历史

        Args:
            user_id: 用户 ID
            group_id: 群组 ID（可选）
        """
        key = self._get_key(user_id, group_id)
        if key in self._storage:
            del self._storage[key]
