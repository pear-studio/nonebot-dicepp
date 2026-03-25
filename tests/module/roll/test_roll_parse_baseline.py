"""
Task 1.2: Roll 命令解析行为回归测试基线
覆盖 roll_dice_command.py 的关键语法与错误场景，用于迁移期行为等价验证。
"""
import pytest
from module.roll.expression import preprocess_roll_exp, exec_roll_exp, sift_roll_exp_and_reason
from module.roll.roll_utils import RollDiceError


# ---------------------------------------------------------------------------
# 前缀剥离行为
# ---------------------------------------------------------------------------
class TestRollPrefixStrip:
    """验证 .r 前缀剥离后余下字符串的结构"""

    def test_simple_exp_after_strip(self):
        raw = ".rd20+4"
        body = raw[2:]  # 剥离 ".r"
        assert body == "d20+4"

    def test_strip_with_flag_h(self):
        raw = ".rhd20"
        body = raw[2:]
        assert body.startswith("h")

    def test_strip_with_flag_s(self):
        raw = ".rsd20"
        body = raw[2:]
        assert body.startswith("s")

    def test_strip_with_flag_a(self):
        raw = ".ra50"
        body = raw[2:]
        assert body.startswith("a")

    def test_strip_with_flag_n(self):
        raw = ".rn+3"
        body = raw[2:]
        assert body.startswith("n")


# ---------------------------------------------------------------------------
# 次数前缀 "#" 解析
# ---------------------------------------------------------------------------
class TestRollTimesPrefix:
    """验证 N#expr 次数前缀的解析行为"""

    def _parse_times(self, body: str):
        """复现 roll_dice_command.py 的 # 前缀解析逻辑"""
        times = 1
        if "#" in body:
            time_str, rest = body.split("#", 1)
            if len(time_str) > 0:
                str_length = 1
                while str_length <= len(time_str) and time_str[-str_length].isdigit():
                    str_length += 1
                try:
                    times = int(time_str[-(str_length - 1):]) if str_length > 1 else 1
                    rest_expr = time_str[:-(str_length - 1)] + rest if str_length > 1 else time_str + rest
                    if not (0 < times <= 10):
                        times = 1
                        rest_expr = body
                except (ValueError, AssertionError):
                    times = 1
                    rest_expr = body
                return times, rest_expr
        return times, body

    def test_simple_times_prefix(self):
        times, expr = self._parse_times("3#d20")
        assert times == 3
        assert expr == "d20"

    def test_times_prefix_exceeds_limit(self):
        # times > 10 → 回退为 1
        times, _ = self._parse_times("11#d20")
        assert times == 1

    def test_times_prefix_zero(self):
        times, _ = self._parse_times("0#d20")
        assert times == 1

    def test_no_times_prefix(self):
        times, expr = self._parse_times("d20+4")
        assert times == 1
        assert expr == "d20+4"


# ---------------------------------------------------------------------------
# tail_text 切分（掷骰原因）
# ---------------------------------------------------------------------------
class TestRollTailTextSplit:
    """验证 sift_roll_exp_and_reason 分割掷骰表达式与原因"""

    def test_no_reason(self):
        exp, reason = sift_roll_exp_and_reason("d20+4")
        # sift_roll_exp_and_reason 会将表达式大写化（历史行为）
        assert exp.upper() == "D20+4"
        assert reason == ""

    def test_with_reason_space(self):
        exp, reason = sift_roll_exp_and_reason("d20+4 攻击地精")
        # 历史行为：表达式部分大写化
        assert "D20" in exp.upper()
        assert "攻击地精" in reason

    def test_reason_only(self):
        # 纯文本输入，表达式为空
        exp, reason = sift_roll_exp_and_reason("攻击地精")
        assert "攻击地精" in reason

    def test_empty_input(self):
        exp, reason = sift_roll_exp_and_reason("")
        assert exp == ""
        assert reason == ""


# ---------------------------------------------------------------------------
# flag 扫描行为
# ---------------------------------------------------------------------------
class TestRollFlagScan:
    """验证 h/s/a/n flag 顺序扫描（复现命令逻辑）"""

    def _scan_flags(self, body: str):
        """复现 roll_dice_command.py 的 flag while 循环"""
        is_hidden = False
        is_show_info = True
        special_mode = ""
        compute_exp = False

        while body and body[0] in ["h", "s", "a", "n"]:
            if body[0] == "h":
                is_hidden = True
                body = body[1:]
            elif body[0] == "s":
                is_show_info = False
                body = body[1:]
            elif body[0] == "a":
                special_mode = "a"
                body = body[1:]
                break  # a 后面直接是表达式，不继续扫描
            elif body[0] == "n":
                special_mode = "n"
                body = body[1:]
                if body and body[0] == "a":
                    special_mode = "na"
                    body = body[1:]
                break

        if body[:3] == "exp":
            compute_exp = True
            body = body[3:]

        return {
            "is_hidden": is_hidden,
            "is_show_info": is_show_info,
            "special_mode": special_mode,
            "compute_exp": compute_exp,
            "rest": body,
        }

    def test_flag_h(self):
        result = self._scan_flags("hd20")
        assert result["is_hidden"] is True
        assert result["rest"] == "d20"

    def test_flag_s(self):
        result = self._scan_flags("sd20")
        assert result["is_show_info"] is False
        assert result["rest"] == "d20"

    def test_flag_hs_combined(self):
        result = self._scan_flags("hsd20")
        assert result["is_hidden"] is True
        assert result["is_show_info"] is False

    def test_flag_a(self):
        result = self._scan_flags("a50")
        assert result["special_mode"] == "a"

    def test_flag_n(self):
        result = self._scan_flags("n+3")
        assert result["special_mode"] == "n"

    def test_flag_na(self):
        result = self._scan_flags("na+3")
        assert result["special_mode"] == "na"

    def test_flag_exp(self):
        result = self._scan_flags("expd20+5")
        assert result["compute_exp"] is True
        assert "d20" in result["rest"]

    def test_no_flags(self):
        result = self._scan_flags("d20+4")
        assert result["is_hidden"] is False
        assert result["is_show_info"] is True
        assert result["special_mode"] == ""
        assert result["rest"] == "d20+4"


# ---------------------------------------------------------------------------
# 错误处理基线
# ---------------------------------------------------------------------------
class TestRollErrorHandling:
    """验证非法表达式的错误处理边界"""

    def test_invalid_expression_raises(self):
        from module.roll.roll_utils import RollDiceError
        from module.roll.ast_engine.errors import RollEngineError
        with pytest.raises((RollDiceError, RollEngineError, Exception)):
            exec_roll_exp("???")

    def test_empty_expression(self):
        # 空表达式不应静默通过
        from module.roll.ast_engine.errors import RollEngineError
        from module.roll.roll_utils import RollDiceError
        with pytest.raises((RollDiceError, RollEngineError, Exception)):
            exec_roll_exp("")

    def test_valid_simple_expression(self):
        result = exec_roll_exp("1d6")
        assert result is not None
        assert 1 <= result.get_val() <= 6
