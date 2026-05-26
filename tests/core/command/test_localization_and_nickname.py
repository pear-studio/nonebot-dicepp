"""本地化对话 + 昵称设置集成测试。"""
import pytest


@pytest.mark.integration
class TestLocalization:
    async def test_greeting_matches_locale(self, h):
        await h.send_group("你好", group_id="test_group_a", checker=lambda s: "你好" in s)
        await h.send_group("你好", group_id="test_group_b", checker=lambda s: "你好" in s)

    async def test_random_greeting_avoided_on_frequent_use(self, h):
        """频繁使用时不应出现随机问候语"""
        await h.send_group("你好123", group_id="test_group_c",
                           checker=lambda s: "你好啊" not in s and "你好呀" not in s)
        await h.send_group("你好", group_id="test_group_a",
                           checker=lambda s: "你好啊" not in s and "你好呀" not in s)


@pytest.mark.integration
class TestNickname:
    async def test_set_group_nickname(self, h):
        await h.send_group(".nn 梨子", group_id="group1")
        await h.send_group(".rd", group_id="group1", checker=lambda s: "梨子" in s)
        await h.send_group(".rd", group_id="group2", checker=lambda s: "梨子" not in s)

    async def test_set_default_nickname(self, h):
        await h.send_private(".nn 西瓜")
        await h.send_private(".rd", checker=lambda s: "西瓜" in s)
        await h.send_group(".rd", group_id="group3", checker=lambda s: "西瓜" in s)
        await h.send_group(".rd", group_id="group1", checker=lambda s: "西瓜" not in s and "梨子" in s)

    async def test_illegal_nickname_rejected(self, h):
        await h.send_private(".nn .", checker=lambda s: "非法昵称！" in s)

    async def test_reset_nickname(self, h):
        await h.send_private(".nn", checker=lambda s: "已将您的昵称从" in s)
        await h.send_private(".nn", checker=lambda s: "您尚未设置过昵称" in s)
        await h.send_private(".rd", checker=lambda s: "西瓜" not in s)
        await h.send_group(".rd", group_id="group1", checker=lambda s: "梨子" in s)
