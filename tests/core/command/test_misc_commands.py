"""DND/COC 生成 + JRRP + 统计集成测试。"""
import pytest


@pytest.mark.integration
class TestDNDCOC:
    async def test_dnd_single(self, h):
        await h.send_group(".dnd", checker=lambda s: "DND人物作成" in s and s.count("\n") == 1)

    async def test_dnd_multiple_no_space(self, h):
        await h.send_group(".dnd3", checker=lambda s: "DND人物作成" in s and s.count("\n") == 3)

    async def test_dnd_multiple_with_space(self, h):
        await h.send_group(".dnd 3", checker=lambda s: "DND人物作成" in s and s.count("\n") == 3)

    async def test_dnd_with_reason_no_space(self, h):
        await h.send_group(".dnd3 foo", checker=lambda s: "DND人物作成——foo:\n" in s and s.count("\n") == 3)

    async def test_dnd_with_reason_and_space(self, h):
        await h.send_group(".dnd 3   foo", checker=lambda s: "DND人物作成——foo:\n" in s and s.count("\n") == 3)


@pytest.mark.integration
class TestJRRP:
    async def test_jrrp(self, h):
        await h.send_group(".jrrp", checker=lambda s: "测试用户的今日人品是:" in s)


@pytest.mark.integration
class TestStats:
    async def test_personal_stats(self, h):
        await h.send_group(".统计",
                           checker=lambda s: "今日收到信息:" in s and "今日指令记录:" in s and "今日掷骰次数:" in s)

    async def test_group_stats(self, h):
        await h.send_group(".统计群聊", checker=lambda s: "今日收到信息:" in s and "今日指令记录:" in s)

    async def test_all_users_denied(self, h):
        await h.send_group(".统计所有用户", checker=lambda s: "权限不足" in s)

    async def test_all_users_master(self, h):
        await h.send_group(".统计所有用户", user_id="test_master",
                           checker=lambda s: "权限不足" not in s and "今日收到信息:" in s and "今日指令记录:" in s)

    async def test_all_groups_master(self, h):
        await h.send_group(".统计所有群聊", user_id="test_master",
                           checker=lambda s: "权限不足" not in s and "条群组信息" in s)
