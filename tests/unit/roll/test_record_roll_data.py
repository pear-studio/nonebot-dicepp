"""
record_roll_data 单元测试
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from core.statistics.user_stat import UserStatInfo
from core.statistics.group_stat import GroupStatInfo
from plugins.DicePP.module.roll.roll_dice_command import record_roll_data
from plugins.DicePP.core.communication import MessageMetaData


class TestRecordRollDataGroupStat:
    """验证 record_roll_data 群统计路径不抛 AttributeError"""

    @pytest.mark.asyncio
    async def test_group_stat_callback_does_not_throw(self):
        """群聊掷骰时 update_group_stat 回调执行成功，不因 roll_group 属性缺失而失败"""
        bot = MagicMock()
        bot.stat_manager = MagicMock()
        bot.stat_manager.update_user_stat = AsyncMock()
        bot.stat_manager.update_group_stat = AsyncMock()

        # 构造群聊 meta（group_id 非空）
        meta = MagicMock(spec=MessageMetaData)
        meta.group_id = "test_group_123"
        meta.user_id = "test_user_456"

        # 构造含 d20 的掷骰结果
        mock_result = MagicMock()
        mock_result.d20_num = 1
        mock_result.d20_list = [15]

        await record_roll_data(bot, meta, [mock_result])

        # 用户统计应被更新
        bot.stat_manager.update_user_stat.assert_awaited_once()
        # 群统计应被更新
        bot.stat_manager.update_group_stat.assert_awaited_once()

        # 验证群统计回调确实执行且落盘了掷骰数据
        group_call_args = bot.stat_manager.update_group_stat.await_args
        group_updater = group_call_args[0][1]  # 第二个参数是 updater

        stat = GroupStatInfo()
        group_updater(stat)
        # 群统计中掷骰次数应为 1（不含维度拆分字段）
        assert stat.roll.times.cur_day_val == 1
        # 不应尝试访问 roll_group（GroupStatInfo 无此字段）
        assert not hasattr(stat, 'roll_group')
