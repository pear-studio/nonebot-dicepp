"""
tests/module/roll/test_roll_parse_args.py

覆盖 _parse_roll_args 的各个分支场景：
  - 无 flags 的基本表达式
  - `#` 次数（有效 / 越界回退）
  - BAB `b` 次数推导
  - `h/s` flags
  - `a` 模式表达式改写
  - `n/na` 模式表达式改写
  - `exp` 关键字
  - reason 分割（短输入 via sift_roll_exp_and_reason）
  - 前导空白容忍
"""
import sys
import os

# 确保 src/plugins/DicePP 在 sys.path 中（与其他 roll 测试相同）
_HERE = os.path.dirname(__file__)
_SRC = os.path.normpath(os.path.join(_HERE, "../../../src/plugins/DicePP"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest
from module.roll.roll_parse_args import _parse_roll_args, RollParseArgs, MULTI_ROLL_LIMIT


# ---------------------------------------------------------------------------
# 1. 基本表达式（无 flags）
# ---------------------------------------------------------------------------

def test_basic_expression_no_flags():
    """spec: Basic expression without flags"""
    r = _parse_roll_args("d20+4")
    assert r.times == 1
    assert r.is_hidden is False
    assert r.is_show_info is True
    assert r.special_mode == ""
    assert r.compute_exp is False
    assert r.exp_str == "D20+4"


def test_empty_raw():
    """raw 为空时：全部默认值，exp_str 为空"""
    r = _parse_roll_args("")
    assert r.times == 1
    assert r.exp_str == ""
    assert r.reason_str == ""


def test_whitespace_only_raw():
    """raw 为纯空白时：全部默认值"""
    r = _parse_roll_args("   ")
    assert r.times == 1
    assert r.exp_str == ""


def test_leading_whitespace_tolerance():
    """前导空白容忍：' d20' 与 'd20' 的解析结果等价"""
    r1 = _parse_roll_args("d20")
    r2 = _parse_roll_args("  d20")
    assert r1.exp_str == r2.exp_str
    assert r1.times == r2.times


# ---------------------------------------------------------------------------
# 2. `#` 次数解析
# ---------------------------------------------------------------------------

def test_hash_times_valid():
    """spec: Valid `#` prefix — times=3, exp_str=D20"""
    r = _parse_roll_args("3#d20")
    assert r.times == 3
    assert r.exp_str == "D20"


def test_hash_times_out_of_range():
    """spec: Out-of-range `#` prefix — times fallback to 1"""
    r = _parse_roll_args("11#d20")
    assert r.times == 1
    assert r.exp_str == "D20"


def test_hash_times_zero():
    """times=0 越界，回退 1"""
    r = _parse_roll_args("0#d20")
    assert r.times == 1


def test_hash_times_max():
    """times=MULTI_ROLL_LIMIT 是合法的"""
    r = _parse_roll_args(f"{MULTI_ROLL_LIMIT}#d20")
    assert r.times == MULTI_ROLL_LIMIT


def test_hash_at_start():
    """#d20：times=1，exp_str=D20"""
    r = _parse_roll_args("#d20")
    assert r.times == 1
    assert r.exp_str == "D20"


def test_hash_with_prefix_flags():
    """h3#d20：h flag + times=3"""
    r = _parse_roll_args("h3#d20")
    assert r.times == 3
    assert r.is_hidden is True
    assert r.exp_str == "D20"


# ---------------------------------------------------------------------------
# 3. BAB `b` 次数推导
# ---------------------------------------------------------------------------

def test_bab_times_computed():
    """spec: BAB times computed from +8-4b → times=2"""
    r = _parse_roll_args("d20+8-4b")
    assert r.times == 2
    # exp_str 包含完整表达式（包括 b 部分）
    assert "D20" in r.exp_str
    assert "8" in r.exp_str


def test_bab_no_ab_num():
    """没有 +N 部分时无法推导，times 回退 1"""
    r = _parse_roll_args("d20-4b")
    assert r.times == 1


def test_bab_default_b_num():
    """b 前无数字时 b_num 默认 5（需同时有 +N-B 结构）"""
    # d20+10-b  → '-' 触发 b_num=5（b 前无数字），ab_num=10 → times=ceil(10/5)=2
    r = _parse_roll_args("d20+10-b")
    assert r.times == 2


# ---------------------------------------------------------------------------
# 4. `h/s` flags
# ---------------------------------------------------------------------------

def test_h_flag():
    r = _parse_roll_args("hd20")
    assert r.is_hidden is True
    assert r.exp_str == "D20"


def test_s_flag():
    r = _parse_roll_args("sd20")
    assert r.is_show_info is False
    assert r.exp_str == "D20"


def test_hs_flags_combined():
    """spec: h and s flags combined"""
    r = _parse_roll_args("hsd20+4 攻击地精")
    assert r.is_hidden is True
    assert r.is_show_info is False
    assert r.exp_str == "D20+4"
    # reason 不应包含在 exp_str 中
    assert "攻击地精" not in r.exp_str


def test_sh_flags_combined():
    """sh 顺序与 hs 相同效果"""
    r = _parse_roll_args("shd20")
    assert r.is_hidden is True
    assert r.is_show_info is False


# ---------------------------------------------------------------------------
# 5. `a` 模式
# ---------------------------------------------------------------------------

def test_a_mode_digit_prefix():
    """spec: a mode — raw='a50' → special_mode='a', exp_str='D100CS<=50'"""
    r = _parse_roll_args("a50")
    assert r.special_mode == "a"
    assert r.exp_str == "D100CS<=50"


def test_a_mode_no_suffix():
    """a 后无内容 → D100CS<=50"""
    r = _parse_roll_args("a")
    assert r.special_mode == "a"
    assert r.exp_str == "D100CS<=50"


def test_a_mode_non_digit():
    """a 后非数字 → D100CS<=50 前缀"""
    r = _parse_roll_args("axyz")
    assert r.special_mode == "a"
    # 末尾无数字时走 else 分支
    assert "D100CS<=50" in r.exp_str


# ---------------------------------------------------------------------------
# 6. `n/na` 模式
# ---------------------------------------------------------------------------

def test_n_mode_with_modifier():
    """n 后接 + 修饰符 → D10+..."""
    r = _parse_roll_args("n+3")
    assert r.special_mode == "n"
    assert r.exp_str == "D10+3"


def test_n_mode_no_suffix():
    """n 后无内容 → D10"""
    r = _parse_roll_args("n")
    assert r.special_mode == "n"
    assert r.exp_str == "D10"


def test_na_mode():
    """spec: na mode — raw='na+3' → special_mode='na', exp_str='D10+3'"""
    r = _parse_roll_args("na+3")
    assert r.special_mode == "na"
    assert r.exp_str == "D10+3"


def test_na_mode_no_suffix():
    """na 后无内容 → D10"""
    r = _parse_roll_args("na")
    assert r.special_mode == "na"
    assert r.exp_str == "D10"


def test_na_mode_non_operator_suffix():
    """na 后接非运算符 → D10+ 前缀"""
    r = _parse_roll_args("na3")
    assert r.special_mode == "na"
    assert r.exp_str == "D10+3"


# ---------------------------------------------------------------------------
# 7. `exp` 关键字（计算期望）
# ---------------------------------------------------------------------------

def test_exp_keyword():
    """spec: exp keyword enables expectation computation"""
    r = _parse_roll_args("expd20+5 攻击地精")
    assert r.compute_exp is True
    assert r.exp_str == "D20+5"
    # reason 不应出现在 exp_str
    assert "攻击地精" not in r.exp_str


def test_exp_keyword_sets_compute_exp_true():
    r = _parse_roll_args("expd6")
    assert r.compute_exp is True


# ---------------------------------------------------------------------------
# 8. reason 分割
# ---------------------------------------------------------------------------

def test_reason_excluded_for_short_input():
    """spec: Reason excluded for short inputs"""
    r = _parse_roll_args("d20+4 攻击地精")
    assert r.exp_str == "D20+4"
    assert "攻击地精" not in r.exp_str


def test_reason_in_reason_str():
    """reason 存储在 reason_str，不在 exp_str"""
    r = _parse_roll_args("d20 攻击地精")
    assert r.reason_str == "攻击地精"


def test_no_reason():
    """无 reason 时 reason_str 为空"""
    r = _parse_roll_args("d20")
    assert r.reason_str == ""


# ---------------------------------------------------------------------------
# 9. 组合场景
# ---------------------------------------------------------------------------

def test_times_and_h_flag():
    """3#hd20：times=3, is_hidden=True"""
    # h 位于 time_str 前缀中，经 # 解析后 h 留在剩余串
    r = _parse_roll_args("3#hd20")
    assert r.times == 3
    assert r.is_hidden is True


def test_h_flag_times_and_exp():
    """h3#expd6：is_hidden=True, times=3, compute_exp=True"""
    r = _parse_roll_args("h3#expd6")
    assert r.is_hidden is True
    assert r.times == 3
    assert r.compute_exp is True


# ---------------------------------------------------------------------------
# 10. 5.3 补充边界组合
# ---------------------------------------------------------------------------

def test_exp_long_input_no_whitespace_split():
    """spec 5.3: compute_exp=True + 长输入（>=100字符）时不按空白拆分 reason，reason_str 为空"""
    # 构造一个 exp 消费后 len>=100 的输入，且含空白
    long_body = "d20+" + "+".join(["1"] * 50) + " 这是原因"
    assert len(long_body) >= 100, "前置：确认走长路径"
    r = _parse_roll_args("exp" + long_body)
    assert r.compute_exp is True
    # 核心断言：compute_exp=True 时原因不被截断，reason_str 保持为空
    assert r.reason_str == ""
    # exp_str 应包含空白后的内容（整个 long_body 作为 exp_str）
    assert "这是原因" in r.exp_str


def test_bab_with_h_flag():
    """spec 5.3: BAB 推导 + h flag，times 推导正确，exp_str 含 b 部分"""
    r = _parse_roll_args("hd20+8-4b")
    assert r.is_hidden is True
    assert r.times == 2
    assert "B" in r.exp_str   # b 部分不被剥除
    assert "D20" in r.exp_str


def test_bab_with_s_flag():
    """spec 5.3: BAB 推导 + s flag"""
    r = _parse_roll_args("sd20+8-4b")
    assert r.is_show_info is False
    assert r.times == 2


def test_hash_times_with_a_mode_and_reason():
    """spec 5.3: # times + a 模式 + reason（短输入），确认 times 解析残留前缀不干扰 a 改写"""
    r = _parse_roll_args("3#a50 检定理由")
    assert r.times == 3
    assert r.special_mode == "a"
    assert r.exp_str == "D100CS<=50"
    assert "检定理由" not in r.exp_str


def test_hash_times_with_n_mode_and_reason():
    """spec 5.3: # times + n 模式 + reason"""
    r = _parse_roll_args("2#n+3 原因")
    assert r.times == 2
    assert r.special_mode == "n"
    assert r.exp_str == "D10+3"


def test_hash_times_with_na_mode():
    """spec 5.3: # times + na 模式"""
    r = _parse_roll_args("3#na+5")
    assert r.times == 3
    assert r.special_mode == "na"
    assert r.exp_str == "D10+5"
