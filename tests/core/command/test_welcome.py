"""欢迎词集成测试。"""
import pytest
from core.communication import NoticeData, GroupIncreaseNoticeData


@pytest.mark.integration
class TestWelcome:
    async def test_default_welcome(self, h):
        notice = GroupIncreaseNoticeData("test_user_a", "test_group_a", "test_user_b")
        await h.send_notice(notice, checker=lambda s: "欢迎！" in s)

    async def test_show_welcome_status(self, h):
        await h.send_group(".welcome", checker=lambda s: "欢迎词" in s)

    async def test_welcome_still_default(self, h):
        notice = GroupIncreaseNoticeData("test_user_a", "test_group_a", "test_user_b")
        await h.send_notice(notice, checker=lambda s: "欢迎！" in s)

    async def test_set_custom_welcome(self, h):
        await h.send_group(".welcome ABC", group_id="test_group_a",
                           checker=lambda s: "欢迎词现在已被设为 \"ABC\"" in s)
        notice = GroupIncreaseNoticeData("test_user_a", "test_group_a", "test_user_b")
        await h.send_notice(notice, checker=lambda s: "ABC" in s)

    async def test_other_group_unaffected(self, h):
        notice = GroupIncreaseNoticeData("test_user_c", "test_group_b", "test_user_c")
        await h.send_notice(notice, checker=lambda s: "欢迎！" in s)

    async def test_welcome_too_long(self, h):
        await h.send_group(".welcome " + "*" * 999, group_id="test_group_a",
                           checker=lambda s: "不可用的欢迎词" in s and "欢迎词合计长度不能大于" in s)

    async def test_too_long_does_not_change_existing(self, h):
        await h.send_group(".welcome", checker=lambda s: "欢迎词" in s)
        notice = GroupIncreaseNoticeData("test_user_a", "test_group_a", "test_user_b")
        await h.send_notice(notice, checker=lambda s: "ABC" in s)

    async def test_turn_off_welcome(self, h):
        await h.send_group(".welcome off", group_id="test_group_a",
                           checker=lambda s: "欢迎词已关闭" in s)
        notice = GroupIncreaseNoticeData("test_user_a", "test_group_a", "test_user_b")
        await h.send_notice(notice, checker=lambda s: not s)

    async def test_reset_welcome_to_default(self, h):
        await h.send_group(".welcome default", group_id="test_group_a",
                           checker=lambda s: "欢迎词已被重置" in s)
        notice = GroupIncreaseNoticeData("test_user_a", "test_group_a", "test_user_b")
        await h.send_notice(notice, checker=lambda s: "欢迎！" in s)
