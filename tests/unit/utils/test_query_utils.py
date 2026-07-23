"""命令拆分函数 contract tests"""

import pytest

from plugins.DicePP.core.query_utils import command_split


class TestCommandSplit:
    """command_split 解析 contract tests"""

    def test_empty_string(self):
        assert command_split("") == []

    def test_only_spaces(self):
        assert command_split("   ") == []

    def test_single_word(self):
        assert command_split("hello") == ["hello"]

    def test_multiple_words(self):
        assert command_split("hello world") == ["hello", "world"]

    def test_hashtag_prefix(self):
        assert command_split("#tag") == ["#tag"]

    def test_ampersand_prefix(self):
        assert command_split("&cat") == ["&cat"]

    def test_mixed_hashtag_and_word(self):
        assert command_split("#tag word") == ["#tag", "word"]

    def test_double_quoted_phrase(self):
        assert command_split('"hello world"') == ["hello world"]

    def test_empty_quotes(self):
        """空引号不产生 token"""
        assert command_split('""') == []

    def test_multiple_quoted_phrases(self):
        """多个引号短语"""
        assert command_split('"a" "b"') == ["a", "b"]

    def test_forward_slash_or(self):
        assert command_split("a/b") == ["a/b"]

    def test_hashtag_forward_slash_ampersand(self):
        """# / & 中的 / 作为普通 token 收集"""
        assert command_split("# / &") == ["/"]

    def test_complex_query(self):
        result = command_split('#tag "exact phrase" word &cat')
        assert "#tag" in result
        assert "exact phrase" in result
        assert "word" in result
        assert "&cat" in result

    def test_unterminated_quote(self):
        """未闭合引号：将引号视为普通字符，收集到末尾"""
        result = command_split('"unterminated')
        assert result == ['unterminated']

    def test_multiple_hashtags(self):
        result = command_split("#foo #bar")
        assert result == ["#foo", "#bar"]

    def test_special_chars_only(self):
        result = command_split("# & /")
        assert result == ["/"]

    def test_leading_trailing_spaces(self):
        result = command_split("  hello  world  ")
        assert result == ["hello", "world"]
