"""
单元测试: privacy 敏感数据处理工具
"""

import pytest

from plugins.DicePP.module.persona.utils.privacy import mask_sensitive_string


class TestMaskSensitiveString:
    """测试 mask_sensitive_string"""

    def test_mask_normal_string(self):
        result = mask_sensitive_string("sk-abcdefghijklmnopqrstuvwxyz123456")
        assert result.startswith("sk-")
        assert result.endswith("456")
        assert "*" in result

    def test_mask_short_string_returns_default(self):
        result = mask_sensitive_string("abc")
        assert result == "未设置"

    def test_mask_empty_string(self):
        result = mask_sensitive_string("")
        assert result == "未设置"

    def test_mask_none_treated_as_falsey(self):
        result = mask_sensitive_string(None)  # type: ignore[arg-type]
        assert result == "未设置"

    def test_custom_prefix_suffix(self):
        result = mask_sensitive_string("hello-world", prefix_len=2, suffix_len=2)
        assert result == "he*******ld"

    def test_exact_boundary_length(self):
        # prefix=3 + suffix=3 + 1 masked = 7 chars minimum
        result = mask_sensitive_string("1234567")
        assert result == "123*567"

    def test_one_below_boundary(self):
        result = mask_sensitive_string("123456")
        assert result == "未设置"
