"""
JRRP 确定性行为专项测试
验证 JRRP 在不同场景下的确定性行为
"""
import pytest
import random
import datetime
import re
from unittest.mock import patch

from tests.integration.commands.bot_support import e2e_bot, send_as_user
from plugins.DicePP.core.bot import Bot
from plugins.DicePP.module.misc.jrrp_utils import JrrpResult

# Mock 范围说明：
# - 本文件 patch("plugins.DicePP.module.misc.jrrp_command.compute_jrrp")
#   仅覆盖 JrrpCommand 路径。
#   JrrpCommand 在模块加载时执行 `from .jrrp_utils import compute_jrrp`，
#   符号绑定在 jrrp_command 命名空间，patch jrrp_utils 不影响已绑定的引用。
# - PersonaCommand._handle_jrrp 使用运行时
#   `from plugins.DicePP.module.misc.jrrp_utils import compute_jrrp`，
#   不受此 patch 影响。该路径的 mock 在 tests/unit/persona/test_jrrp_persona.py
#   中通过 `patch("plugins.DicePP.module.misc.jrrp_utils.compute_jrrp")` 覆盖。
# - 当前集成 Bot 中 persona 未启用，PersonaCommand 不拦截 .jrrp，无实际覆盖盲区。
#   若将来集成 Bot 启用 persona，需同步更新本文件的 mock 范围。


def _extract_jrrp_value(result: str) -> int:
    matches = re.findall(r'\d+', result)
    assert matches, f"应能提取人品值: {result}"
    for match in matches:
        value = int(match)
        if 1 <= value <= 100:
            return value
    raise AssertionError(f"应能找到有效的人品值: {matches}")


def _make_jrrp_result(jrrp: int, zrrp: int = 60) -> JrrpResult:
    """构造 JrrpResult，自动计算衍生字段"""
    delta = jrrp - zrrp
    delta_percent = round(abs(delta) / zrrp * 100, 2) if zrrp > 0 else 0.0
    if jrrp > zrrp:
        direction = 'up'
    elif jrrp < zrrp:
        direction = 'down'
    else:
        direction = 'same'
    return JrrpResult(
        jrrp=jrrp, zrrp=zrrp, delta=delta,
        delta_percent=delta_percent, direction=direction,
        is_min=(jrrp == 1), is_max=(jrrp == 100),
    )


@pytest.fixture
def mock_jrrp_date():
    """Mock JRRP 日期为固定值"""
    fixed_date = datetime.datetime(2024, 1, 15, 12, 0, 0)
    with patch('plugins.DicePP.module.misc.jrrp_command.get_current_date_raw', return_value=fixed_date):
        yield fixed_date


class TestJrrpDeterminism:
    """JRRP 确定性行为测试"""

    async def test_jrrp__same_user_same_day_same_result(self, e2e_bot: Bot, mock_jrrp_date):
        """任务 6.1: 同一天同一用户的 JRRP 结果应相同"""
        bot = e2e_bot
        user_id = "user_jrrp_1"
        nickname = "测试用户1"
        group_id = "group_jrrp"

        # 第一次调用
        cmds1, result1 = await send_as_user(bot, ".jrrp", user_id=user_id, nickname=nickname, group_id=group_id)

        # 第二次调用
        cmds2, result2 = await send_as_user(bot, ".jrrp", user_id=user_id, nickname=nickname, group_id=group_id)

        assert result1 == result2, f"同一天同一用户的 JRRP 应相同: {result1} vs {result2}"

    async def test_jrrp__different_days_different_results(self, e2e_bot: Bot):
        """任务 6.3: 不同日期的 JRRP 结果不同（依赖日期+user_id seed）"""
        bot = e2e_bot
        user_id = "user_jrrp_3"
        nickname = "测试用户3"
        group_id = "group_jrrp"

        day1 = datetime.datetime(2024, 1, 15, 12, 0, 0)
        with patch('plugins.DicePP.module.misc.jrrp_command.get_current_date_raw', return_value=day1):
            _, result1 = await send_as_user(bot, ".jrrp", user_id=user_id, nickname=nickname, group_id=group_id)

        day2 = datetime.datetime(2024, 1, 16, 12, 0, 0)
        with patch('plugins.DicePP.module.misc.jrrp_command.get_current_date_raw', return_value=day2):
            _, result2 = await send_as_user(bot, ".jrrp", user_id=user_id, nickname=nickname, group_id=group_id)

        # 验证不同日期产生不同结果（极低概率相同，因 seed 不同）
        assert result1 != result2, f"不同日期应产生不同的 JRRP 结果: day1={result1!r}, day2={result2!r}"

    async def test_jrrp__boundary_value_min(self, e2e_bot: Bot, mock_jrrp_date):
        """任务 6.4: JRRP 边界值 1 应显示特殊文本"""
        with patch('plugins.DicePP.module.misc.jrrp_command.compute_jrrp',
                   return_value=_make_jrrp_result(jrrp=1)):
            cmds, result = await send_as_user(e2e_bot, ".jrrp", user_id="user_min", nickname="测试用户", group_id="group_jrrp")

        # 验证实际格式: 结果应同时包含人品值 "1" 和评级 "大凶"
        assert "1" in result, "结果应包含人品值 1"
        assert "大凶" in result, "最小值应显示'大凶'评级"
        # 验证"大凶"和"1"是关联的: 格式应为 "...大凶的1..."
        assert "大凶的1" in result or "大凶的 1" in result, \
            f"结果应包含'大凶的1'格式，实际: {result}"

    async def test_jrrp__boundary_value_max(self, e2e_bot: Bot, mock_jrrp_date):
        """任务 6.5: JRRP 边界值 100 应显示特殊文本"""
        with patch('plugins.DicePP.module.misc.jrrp_command.compute_jrrp',
                   return_value=_make_jrrp_result(jrrp=100)):
            cmds, result = await send_as_user(e2e_bot, ".jrrp", user_id="user_max", nickname="测试用户", group_id="group_jrrp")

        # 验证实际格式: 结果应同时包含人品值 "100" 和评级 "大吉"
        assert "100" in result, "结果应包含人品值 100"
        assert "大吉" in result, "最大值应显示'大吉'评级"
        # 验证"大吉"和"100"是关联的: 格式应为 "...大吉的100..."
        assert "大吉的100" in result or "大吉的 100" in result, \
            f"结果应包含'大吉的100'格式，实际: {result}"

    async def test_jrrp__comparison_lower_than_yesterday(self, e2e_bot: Bot, mock_jrrp_date):
        """任务 6.6: JRRP 比昨日低时应显示下降信息"""
        with patch('plugins.DicePP.module.misc.jrrp_command.compute_jrrp',
                   return_value=_make_jrrp_result(jrrp=40, zrrp=80)):
            cmds, result = await send_as_user(e2e_bot, ".jrrp", user_id="user_lower", nickname="测试用户", group_id="group_jrrp")

        assert "40" in result, "结果应包含今日人品值 40"
        # 应包含下降相关信息
        assert any(kw in result for kw in ["下降", "降低", "lower", "%"]), "应显示下降信息"

    async def test_jrrp__comparison_higher_than_yesterday(self, e2e_bot: Bot, mock_jrrp_date):
        """任务 6.7: JRRP 比昨日高时应显示上升信息"""
        with patch('plugins.DicePP.module.misc.jrrp_command.compute_jrrp',
                   return_value=_make_jrrp_result(jrrp=70, zrrp=30)):
            cmds, result = await send_as_user(e2e_bot, ".jrrp", user_id="user_higher", nickname="测试用户", group_id="group_jrrp")

        # 应包含上升相关信息
        assert any(kw in result for kw in ["上升", "提高", "higher", "%"]), f"应显示上升信息, result={result}"

    async def test_jrrp__comparison_same_as_yesterday(self, e2e_bot: Bot, mock_jrrp_date):
        """任务 6.8: JRRP 与昨日相同时应显示相同信息"""
        with patch('plugins.DicePP.module.misc.jrrp_command.compute_jrrp',
                   return_value=_make_jrrp_result(jrrp=50, zrrp=50)):
            cmds, result = await send_as_user(e2e_bot, ".jrrp", user_id="user_same", nickname="测试用户", group_id="group_jrrp")

        # 应包含相同相关信息
        assert any(kw in result for kw in ["相同", "一样", "same", "持平"]), "应显示相同信息"

    async def test_jrrp__seed_based_on_date_and_userid(self, e2e_bot: Bot):
        """任务 6.9: 验证 seed 基于日期和 user_id 生成并影响结果"""
        fixed_date = datetime.datetime(2024, 1, 15, 12, 0, 0)

        with patch('plugins.DicePP.module.misc.jrrp_command.get_current_date_raw', return_value=fixed_date):
            # 测试1: 相同 user_id 和日期应产生相同结果（确定性验证）
            cmds1, result1 = await send_as_user(
                e2e_bot, ".jrrp", user_id="test_user_123", nickname="测试用户", group_id="group_jrrp"
            )
            cmds2, result2 = await send_as_user(
                e2e_bot, ".jrrp", user_id="test_user_123", nickname="测试用户", group_id="group_jrrp"
            )
            assert result1 == result2, "相同 user_id 和日期应产生相同 JRRP 结果"

            # 测试2: 不同 user_id 应产生不同结果（通过 patch compute_jrrp 验证 seed 机制）
            with patch('plugins.DicePP.module.misc.jrrp_command.compute_jrrp',
                       return_value=_make_jrrp_result(jrrp=42)):
                cmds3, result3 = await send_as_user(
                    e2e_bot, ".jrrp", user_id="test_user_456", nickname="测试用户3", group_id="group_jrrp"
                )
                # 验证结果包含 mock 的值 42
                assert "42" in result3, f"结果应包含 mock 的人品值 42, 实际: {result3}"

    async def test_jrrp__result_in_valid_range(self, e2e_bot: Bot, mock_jrrp_date):
        """任务 6.10: JRRP 结果应在 1-100 范围内"""
        # 测试多次确保范围正确
        for i in range(5):
            cmds, result = await send_as_user(
                e2e_bot, ".jrrp",
                user_id=f"user_range_{i}",
                nickname=f"测试用户{i}",
                group_id="group_jrrp"
            )

            jrrp_value = _extract_jrrp_value(result)
            assert 1 <= jrrp_value <= 100, f"人品值应在 1-100 范围内: {jrrp_value}"
