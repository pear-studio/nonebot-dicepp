"""掷骰命令集成测试。"""
import pytest


@pytest.mark.integration
class TestRollDice:
    async def test_basic_roll(self, h):
        await h.send_group(".r", checker=lambda s: "测试用户 的掷骰结果为 1D20=" in s)
        await h.send_group(".rd", checker=lambda s: "测试用户 的掷骰结果为 1D20=" in s)
        await h.send_group(".rd20", checker=lambda s: "测试用户 的掷骰结果为 1D20=" in s)

    async def test_multi_roll(self, h):
        await h.send_group(".r2#d20", checker=lambda s: "2次" in s and "1D20" in s)
        await h.send_group(".r2#d20+1", checker=lambda s: "2次" in s and "1D20+1" in s)
        await h.send_group(".r2#d20+5", checker=lambda s: "1D20+5" in s and "2次" in s)

    async def test_roll_with_reason(self, h):
        await h.send_group(".rd20 Attack",
                           checker=lambda s: "测试用户 为 attack 进行的掷骰结果为 1D20=" in s)
        await h.send_group(".r2#d20 Attack Twice",
                           checker=lambda s: "attack twice" in s and "2次" in s and "1D20" in s)
        await h.send_group(".rd8原因",
                           checker=lambda s: "测试用户 为 原因 进行的掷骰结果为 1D8=" in s)
        await h.send_group(".r原因",
                           checker=lambda s: "测试用户 为 原因 进行的掷骰结果为 1D20=" in s)

    async def test_hidden_roll(self, h):
        await h.send_group(".rh", checker=lambda s: "|Group: group|" in s and "|Private: user|" in s)
        await h.send_private(".rh", checker=lambda s: "|Group: group|" not in s and "|Private: user|" in s)
        await h.send_group(".rh d20 原因",
                           checker=lambda s: "测试用户 为 原因 进行的暗骰结果为" in s and
                           "1D20=" in s and "测试用户 进行了一次暗骰" in s)

    async def test_hidden_roll_target_split(self, h):
        await h.send_group(".rh", group_id="test_group_hidden", user_id="test_user_hidden",
                           target_checker=lambda cmds: len(cmds) >= 2 and
                           any("test_group_hidden" in str(c) for c in cmds) and
                           any("test_user_hidden" in str(c) for c in cmds))

    async def test_show_info_off(self, h):
        await h.send_group(".rsd20+5", checker=lambda s: "1D20+5=" in s and s.count("=") == 1)
        await h.send_group(".rs10D20cs>5",
                           checker=lambda s: "10D20CS>5=" in s and s.count("=") == 1 and "{" not in s)

    async def test_private_roll(self, h):
        await h.send_private(".r", checker=lambda s: "掷骰结果为 1D20=" in s)
        await h.send_private(".rd20", checker=lambda s: "掷骰结果为 1D20=" in s)

    async def test_error_expressions(self, h):
        await h.send_group(".r(1+1)d6", checker=lambda s: "语法错误" in s)
        await h.send_group(".r2(DK3)", checker=lambda s: "语法错误" in s)
        await h.send_group(".rh()", checker=lambda s: "语法错误" in s)

    async def test_expectation_mode(self, h):
        await h.send_private(".r exp 2d20k1", checker=lambda s: "期望" in s)
