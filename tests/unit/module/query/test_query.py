"""Pure query normalization tests."""

import pytest

from plugins.DicePP.core.data.query_store import regexp_normalize


class TestRegexpNormalize:
    def test_normalize_escapes_special_chars(self):
        result = regexp_normalize("(力量)")
        assert "\\(" in result
        assert "\\)" in result
        assert result.startswith("\\("), f"'(' 应被转义，实际: {result}"

    @pytest.mark.parametrize("input_str", ["力量", "火球术"])
    def test_normalize_preserves_normal_chars(self, input_str):
        assert regexp_normalize(input_str) == input_str

    def test_normalize_escapes_dot(self):
        assert "\\." in regexp_normalize("v1.0")

    def test_normalize_empty_string(self):
        assert isinstance(regexp_normalize(""), str)
