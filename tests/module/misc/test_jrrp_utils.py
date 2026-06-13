"""
JRRP 共享函数和工具的单元测试
"""
import pytest
import datetime
from unittest.mock import patch, MagicMock

from module.misc.jrrp_utils import (
    compute_jrrp,
    JrrpResult,
    format_jrrp_info_line,
    format_jrrp_trend_line,
    format_jrrp_text,
)


class TestComputeJrrp:
    """compute_jrrp 单元测试"""

    def test_deterministic__same_input_same_output(self):
        """同一 user_id + 同一 date 多次调用返回相同 (jrrp, zrrp)"""
        r1 = compute_jrrp("user_abc", datetime.datetime(2024, 6, 15, 12, 0, 0))
        r2 = compute_jrrp("user_abc", datetime.datetime(2024, 6, 15, 12, 0, 0))
        r3 = compute_jrrp("user_abc", datetime.datetime(2024, 6, 15, 12, 0, 0))
        assert r1.jrrp == r2.jrrp == r3.jrrp
        assert r1.zrrp == r2.zrrp == r3.zrrp

    def test_different_user_id_different_result(self):
        """不同 user_id 在同一天通常产生不同结果"""
        r1 = compute_jrrp("user_aaa", datetime.datetime(2024, 6, 15))
        r2 = compute_jrrp("user_bbb", datetime.datetime(2024, 6, 15))
        assert r1.jrrp != r2.jrrp or r1.zrrp != r2.zrrp, (
            f"Expected different results for different users, got jrrp={r1.jrrp}/{r2.jrrp}"
        )
        assert 1 <= r1.jrrp <= 100
        assert 1 <= r2.jrrp <= 100

    def test_different_date_different_result(self):
        """同一用户在不同日期通常产生不同结果"""
        r1 = compute_jrrp("user_abc", datetime.datetime(2024, 6, 15))
        r2 = compute_jrrp("user_abc", datetime.datetime(2024, 6, 16))
        assert r1.jrrp != r2.jrrp or r1.zrrp != r2.zrrp, (
            f"Expected different results for different dates, got jrrp={r1.jrrp}/{r2.jrrp}"
        )
        assert 1 <= r1.jrrp <= 100
        assert 1 <= r2.jrrp <= 100

    def test_result_in_valid_range(self):
        """结果值在 [1, 100] 范围内"""
        for i in range(20):
            r = compute_jrrp(f"user_{i}", datetime.datetime(2024, 6, 15))
            assert 1 <= r.jrrp <= 100, f"jrrp={r.jrrp} out of range"
            assert 1 <= r.zrrp <= 100, f"zrrp={r.zrrp} out of range"

    def test_namedtuple_fields(self):
        """JrrpResult 包含所有必需字段"""
        r = compute_jrrp("test", datetime.datetime(2024, 6, 15))
        assert isinstance(r.jrrp, int)
        assert isinstance(r.zrrp, int)
        assert isinstance(r.delta, int)
        assert isinstance(r.delta_percent, float)
        assert r.direction in ('up', 'down', 'same')
        assert isinstance(r.is_min, bool)
        assert isinstance(r.is_max, bool)
        # is_min 和 is_max 不互斥（jrrp 不可能同时为 1 和 100），但至少 one of them can be True
        assert not (r.is_min and r.is_max)

    def test_direction_up(self):
        """jrrp > zrrp 时 direction='up'"""
        # 通过大量迭代找到 direction='up' 的 case（概率较高）
        for i in range(50):
            r = compute_jrrp(f"up_test_{i}", datetime.datetime(2024, 6, 15))
            if r.jrrp > r.zrrp:
                assert r.direction == 'up'
                assert r.delta > 0
                assert r.delta_percent >= 0
                return
        pytest.skip("未找到 direction='up' 的测试用例")

    def test_direction_down(self):
        """jrrp < zrrp 时 direction='down'"""
        for i in range(50):
            r = compute_jrrp(f"down_test_{i}", datetime.datetime(2024, 6, 15))
            if r.jrrp < r.zrrp:
                assert r.direction == 'down'
                assert r.delta < 0
                assert r.delta_percent >= 0
                return
        pytest.skip("未找到 direction='down' 的测试用例")


class TestFormatJrrpInfoLine:
    """format_jrrp_info_line 测试"""

    def test_normal_value(self):
        assert format_jrrp_info_line("pear", 75) == "pear的今日人品是:75"

    def test_min_value(self):
        result = format_jrrp_info_line("pear", 1)
        assert "大凶" in result
        assert "1" in result

    def test_max_value(self):
        result = format_jrrp_info_line("pear", 100)
        assert "大吉" in result
        assert "100" in result


class TestFormatJrrpTrendLine:
    """format_jrrp_trend_line 测试"""

    def test_up(self):
        line = format_jrrp_trend_line(60, 75, 25.0, 'up')
        assert "上升" in line
        assert "25.0%" in line

    def test_down(self):
        line = format_jrrp_trend_line(75, 60, 20.0, 'down')
        assert "下降" in line
        assert "20.0%" in line

    def test_same(self):
        line = format_jrrp_trend_line(50, 50, 0.0, 'same')
        assert "相同" in line


class TestFormatJrrpText:
    """format_jrrp_text 组合函数测试"""

    def test_contains_both_lines(self):
        text = format_jrrp_text("pear", 75, 60, 25.0, 'up')
        assert "pear的今日人品是:75" in text
        assert "上升" in text

    def test_no_triple_newline(self):
        """确保没有多余空行"""
        text = format_jrrp_text("pear", 75, 60, 25.0, 'up')
        assert "\n\n\n" not in text

    def test_exact_concatenation(self):
        """format_jrrp_text 是 info_line + trend_line 的精确拼接"""
        info = format_jrrp_info_line("pear", 75)
        trend = format_jrrp_trend_line(60, 75, 25.0, 'up')
        full = format_jrrp_text("pear", 75, 60, 25.0, 'up')
        assert full == info + trend
