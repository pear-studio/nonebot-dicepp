"""
单元测试: SimpleMemory
"""

import pytest
import sys
sys.path.insert(0, "src")

from plugins.DicePP.module.llm.memory import SimpleMemory


class TestSimpleMemory:
    """测试 SimpleMemory"""

    def test_add_and_get(self):
        """测试添加和获取消息"""
        memory = SimpleMemory(max_size=5)

        # 添加消息
        memory.add_message("user1", "user", "你好")
        memory.add_message("user1", "assistant", "你好！有什么可以帮助你的吗？")

        # 获取历史
        history = memory.get_history("user1")

        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "你好"
        assert history[1]["role"] == "assistant"
        assert history[1]["content"] == "你好！有什么可以帮助你的吗？"

    def test_user_isolation(self):
        """测试用户隔离"""
        memory = SimpleMemory(max_size=5)

        # 为不同用户添加消息
        memory.add_message("user1", "user", "我是用户1")
        memory.add_message("user2", "user", "我是用户2")

        # 验证隔离
        history1 = memory.get_history("user1")
        history2 = memory.get_history("user2")

        assert len(history1) == 1
        assert len(history2) == 1
        assert history1[0]["content"] == "我是用户1"
        assert history2[0]["content"] == "我是用户2"

    def test_group_isolation(self):
        """测试群聊和私聊隔离"""
        memory = SimpleMemory(max_size=5)

        # 私聊消息
        memory.add_message("user1", "user", "私聊消息", group_id="")
        # 群聊消息
        memory.add_message("user1", "user", "群聊消息", group_id="group1")

        # 验证隔离
        private_history = memory.get_history("user1", "")
        group_history = memory.get_history("user1", "group1")

        assert len(private_history) == 1
        assert len(group_history) == 1
        assert private_history[0]["content"] == "私聊消息"
        assert group_history[0]["content"] == "群聊消息"

    def test_max_size(self):
        """测试最大容量限制"""
        memory = SimpleMemory(max_size=3)

        # 添加超过限制的消息
        for i in range(5):
            memory.add_message("user1", "user", f"消息{i}")

        # 获取历史（应该只有最后3条）
        history = memory.get_history("user1")

        assert len(history) == 3
        assert history[0]["content"] == "消息2"
        assert history[1]["content"] == "消息3"
        assert history[2]["content"] == "消息4"

    def test_clear(self):
        """测试清空历史"""
        memory = SimpleMemory(max_size=5)

        # 添加消息
        memory.add_message("user1", "user", "消息1")
        memory.add_message("user1", "assistant", "回复1")

        # 验证有数据
        assert len(memory.get_history("user1")) == 2

        # 清空
        memory.clear("user1")

        # 验证已清空
        assert len(memory.get_history("user1")) == 0

    def test_clear_with_group(self):
        """测试清空指定群组的历史"""
        memory = SimpleMemory(max_size=5)

        # 添加消息
        memory.add_message("user1", "user", "私聊消息", group_id="")
        memory.add_message("user1", "user", "群聊消息", group_id="group1")

        # 清空群聊
        memory.clear("user1", "group1")

        # 验证群聊已清空，私聊还在
        assert len(memory.get_history("user1", "")) == 1
        assert len(memory.get_history("user1", "group1")) == 0

    def test_empty_history(self):
        """测试空历史"""
        memory = SimpleMemory(max_size=5)

        # 获取未存在的用户历史
        history = memory.get_history("nonexistent")

        assert history == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
