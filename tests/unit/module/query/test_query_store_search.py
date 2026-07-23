"""Pure QueryStore search-SQL generation tests."""

from plugins.DicePP.core.data.query_store import QueryStore


class TestGenerateSearchSqlRegexp:
    def test_equal_prefix_generates_exact_match(self):
        sql, params = QueryStore._generate_search_sql_regexp(["=火球术"])
        assert sql == "名称 regexp ?"
        assert params == ["^火球术$"]

    def test_equal_prefix_with_regex_chars(self):
        _, params = QueryStore._generate_search_sql_regexp(["=(力量)"])
        assert params == ["^\\(力量\\)$"]

    def test_equal_prefix_ignores_prefix_only(self):
        _, params = QueryStore._generate_search_sql_regexp(["="])
        assert params == ["="]

    def test_multiple_commands_generate_or_pattern(self):
        _, params = QueryStore._generate_search_sql_regexp(["火球术", "寒冰锥"])
        assert params == ["火球术|寒冰锥"]

    def test_combined_equal_and_normal_pattern(self):
        _, params = QueryStore._generate_search_sql_regexp(["=火球术", "寒冰锥"])
        assert params == ["^火球术$|寒冰锥"]
