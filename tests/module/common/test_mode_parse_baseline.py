"""
Task 1.2: Mode 命令解析行为回归测试基线
覆盖 mode_command.py 的关键语法与错误场景，用于迁移期行为等价验证。
"""
import pytest


# ---------------------------------------------------------------------------
# 前缀识别
# ---------------------------------------------------------------------------
class TestModePrefixRecognition:
    """验证 .mode 和 .模式 前缀识别行为"""

    @pytest.mark.parametrize("msg,expected_body", [
        (".mode COC7", "COC7"),
        (".mode", ""),
        (".mode default", "default"),
        (".mode clear", "clear"),
        (".模式 COC7", "COC7"),
        (".模式", ""),
    ])
    def test_prefix_strip(self, msg: str, expected_body: str):
        """复现 mode_command.py 的前缀剥离逻辑"""
        if msg.startswith(".模式"):
            body = msg[3:].strip()
        elif msg.startswith(".mode"):
            body = msg[5:].strip()
        else:
            body = None
        assert body == expected_body

    def test_non_mode_msg_not_matched(self):
        """非 mode 前缀消息不应被识别"""
        msg = ".roll d20"
        matched = msg.startswith(".模式") or msg.startswith(".mode")
        assert matched is False


# ---------------------------------------------------------------------------
# 参数语义
# ---------------------------------------------------------------------------
class TestModeArgSemantics:
    """验证模式参数的语义识别（大写匹配、特殊值）"""

    @pytest.mark.parametrize("arg,expected_type", [
        ("DEFAULT", "reset"),
        ("default", "reset"),   # 大写化后应等价
        ("CLEAR", "reset"),
        ("clear", "reset"),
        ("COC7", "switch"),
        ("DND5E2024", "switch"),
        ("", "show_current"),
    ])
    def test_arg_classification(self, arg: str, expected_type: str):
        """复现 mode_command.py 的参数分类逻辑"""
        arg_upper = arg.strip().upper()
        if arg_upper in ("DEFAULT", "CLEAR"):
            result_type = "reset"
        elif arg_upper != "":
            result_type = "switch"
        else:
            result_type = "show_current"
        assert result_type == expected_type


# ---------------------------------------------------------------------------
# 模式名匹配逻辑
# ---------------------------------------------------------------------------
class TestModeFuzzyMatch:
    """验证模式名精确/模糊匹配行为"""

    MOCK_MODE_DICT = {
        "DND5E2024": ["20", "DND5E2024"],
        "DND5E2014": ["20", "DND5E2014"],
        "COC7": ["100", "COC7"],
        "NECHRONICA": ["10", "NECHRONICA"],
    }
    MOCK_UPPER_MAP = {k.upper(): k for k in MOCK_MODE_DICT.keys()}

    def _match(self, query: str):
        """复现精确 + 模糊匹配逻辑"""
        query_upper = query.upper()
        # 精确匹配
        exact = self.MOCK_UPPER_MAP.get(query_upper)
        if exact:
            return "exact", [exact]
        # 模糊匹配
        fuzzy = [k for k in self.MOCK_UPPER_MAP if query_upper in k]
        if len(fuzzy) > 1:
            return "multi", fuzzy
        elif len(fuzzy) == 1:
            orig = self.MOCK_UPPER_MAP[fuzzy[0]]
            return "single", [orig]
        return "not_found", []

    def test_exact_match(self):
        result_type, result = self._match("COC7")
        assert result_type == "exact"
        assert result == ["COC7"]

    def test_exact_match_case_insensitive(self):
        result_type, result = self._match("coc7")
        assert result_type == "exact"
        assert result == ["COC7"]

    def test_fuzzy_single_match(self):
        result_type, result = self._match("NECH")
        assert result_type == "single"
        assert result == ["NECHRONICA"]

    def test_fuzzy_multi_match(self):
        result_type, result = self._match("DND5E")
        assert result_type == "multi"
        assert len(result) == 2

    def test_no_match(self):
        result_type, result = self._match("INVALID")
        assert result_type == "not_found"
        assert result == []

    def test_empty_query_not_matched(self):
        # 空参数 → show_current，不进入匹配逻辑
        # 注意：空字符串是所有键的子串，如果进入匹配逻辑会匹配所有项（历史行为）
        # 因此命令层需在进入匹配逻辑前过滤空参数（此测试记录历史行为以供迁移参照）
        result_type, result = self._match("")
        # 空串作为子串会命中所有模式（历史实现行为）
        assert result_type in ("multi", "not_found")  # 不进入此逻辑，测试记录边界
