"""Bot 激活/关闭、帮助、多命令、master 管理集成测试。"""
import pytest


@pytest.mark.integration
class TestBotActivate:
    async def test_bot_info(self, h):
        await h.send_group(".bot", checker=lambda s: "DicePP by 梨子" in s)

    async def test_activate_on_off_cycle(self, h):
        await h.send_group(".bot on", group_id="group_activate", checker=lambda s: not s)
        await h.send_group(".bot on", group_id="group_activate", to_me=True,
                           checker=lambda s: "DicePP现已开启。" in s)
        await h.send_group(".r", group_id="group_activate", checker=lambda s: not not s)
        await h.send_group(".bot off", group_id="group_activate", checker=lambda s: not s)
        await h.send_group(".bot off", group_id="group_activate", to_me=True,
                           checker=lambda s: "DicePP现已关闭。" in s)
        await h.send_group(".r", group_id="group_activate", checker=lambda s: not s)

    async def test_other_group_unaffected(self, h):
        await h.send_group(".r", checker=lambda s: not not s)

    async def test_reactivate(self, h):
        await h.send_group(".bot on", group_id="group_activate", to_me=True,
                           checker=lambda s: "DicePP现已开启。" in s)
        await h.send_group(".r", group_id="group_activate", checker=lambda s: not not s)

    async def test_dismiss(self, h):
        await h.send_group(".dismiss", group_id="group_activate", checker=lambda s: not s)
        await h.send_group(".dismiss", group_id="group_activate", to_me=True,
                           checker=lambda s: "再见啦。" in s)


@pytest.mark.integration
class TestHelp:
    async def test_help_main(self, h):
        await h.send_group(".help", checker=lambda s: "DicePP" in s)

    async def test_help_roll(self, h):
        await h.send_group(".help r", checker=lambda s: "骰" in s)

    async def test_help_command_list(self, h):
        await h.send_group(".help 指令", checker=lambda s: ".r" in s)

    async def test_help_link(self, h):
        await h.send_group(".help 链接", checker=lambda s: "pear-studio/nonebot-dicepp" in s)


@pytest.mark.integration
class TestMultiCommand:
    async def test_help_and_roll_chain(self, h):
        await h.send_group(".help\\\\.r",
                           checker=lambda s: "提出意见~\n测试用户 的掷骰结果为" in s)

    async def test_double_roll_chain(self, h):
        await h.send_group(".r\\\\.r\\\\",
                           checker=lambda s: s.count("测试用户 的掷骰结果为") == 2)


@pytest.mark.integration
class TestMaster:
    async def test_non_master_rejected(self, h):
        await h.send_group(".m reboot", checker=lambda s: not s)
        await h.send_group(".m send", checker=lambda s: not s)

    async def test_master_send_validation(self, h):
        await h.send_group(".m send", user_id="test_master", checker=lambda s: "非法输入" in s)
        await h.send_group(".m send ABC:1234:ABC", user_id="test_master",
                           checker=lambda s: "目标必须为user或group" in s)

    async def test_master_send_to_user(self, h):
        await h.send_group(".m send user:1234:ABC", user_id="test_master",
                           checker=lambda s: "|Private: 1234|" in s and "发送消息: abc 至 1234 (类型:user)" in s)

    async def test_master_send_to_group(self, h):
        await h.send_private(".m send group:1234:ABC", user_id="test_master",
                             checker=lambda s: "|Group: 1234|" in s and "发送消息: abc 至 1234 (类型:group)" in s)

    async def test_master_send_case_insensitive(self, h):
        await h.send_group(".m send USER:1234:ABC", user_id="test_master",
                           checker=lambda s: "|Private: 1234|" in s and "发送消息: abc 至 1234 (类型:user)" in s)
