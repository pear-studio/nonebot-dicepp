"""
roll_parse_args.py — .r 命令专用解析适配器

将 CommandParseResult.raw（命令前缀剥离后的原始片段）转换为
结构化 RollParseArgs 数据类，与解析层解耦并可独立单元测试。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from module.roll.roll_const import MULTI_ROLL_LIMIT


@dataclass
class RollParseArgs:
    """
    .r 命令的结构化解析结果。

    字段：
        times        — 重复掷骰次数，默认 1
        is_hidden    — 是否暗骰（h flag），默认 False
        is_show_info — 是否展示中间结果（s flag 取反），默认 True
        special_mode — 特殊模式："" / "a" / "n" / "na"
        compute_exp  — 是否计算期望（exp 关键字），默认 False
        exp_str      — 掷骰表达式（已大写化），默认 ""
        reason_str   — 掷骰原因（从 raw 分割），默认 ""
    """
    times: int = 1
    is_hidden: bool = False
    is_show_info: bool = True
    special_mode: str = ""
    compute_exp: bool = False
    exp_str: str = ""
    reason_str: str = ""


def _parse_roll_args(raw: str) -> RollParseArgs:
    """
    从 CommandParseResult.raw 解析 .r 命令参数。

    **输入契约**：
      ``raw`` 为命令前缀（".r"）剥离后的原始片段。
      ``CommandTextParser`` **不保证**已将私有 flags（h/s/a/n）从 raw 中剥除——
      flags 仅被识别并写入 ``CommandParseResult.flags``，但 raw 保留原文不变。
      因此本函数需要对 h/s/a/n 做二次扫描，而不能依赖 ``parse_result.flags``。
      （注：a/n 需要对后续字符做表达式改写，必须直接操作 raw 字符串，
        h/s 顺带一起在 raw 上处理以保持一致性和简洁性。）

    **解析顺序**：
      1. 前导空白剥离（对 raw 中可能存在的开头空格容忍）
      2. ``#`` 次数解析（或 BAB ``b`` 推导，当 ``#`` 不存在时）
      3. 连续前缀 ``h/s/a/n`` 扫描（含 a/n 模式表达式改写）
      4. ``exp`` 关键字检测
      5. 表达式 / 原因分割（via ``sift_roll_exp_and_reason``）
    """
    # 延迟导入，避免循环依赖
    from module.roll import sift_roll_exp_and_reason

    result = RollParseArgs()
    s = raw.strip()  # 容忍前导空白

    # ── 1. `#` 次数解析 ──────────────────────────────────────────────────
    if "#" in s:
        time_str, s = s.split("#", 1)
        if time_str:
            # 从 time_str 尾部提取连续数字
            str_length = 1
            while str_length <= len(time_str) and time_str[-str_length].isdigit():
                str_length += 1
            str_length -= 1  # 回退到最后一个数字字符数量
            if str_length > 0:
                digit_part = time_str[-str_length:]
                prefix_part = time_str[:-str_length]
                try:
                    t = int(digit_part)
                    assert 1 <= t <= MULTI_ROLL_LIMIT
                    result.times = t
                    s = prefix_part + s
                except (ValueError, AssertionError):
                    # 越界或无效：times 回退为 1。
                    # 注意：digit_part（越界数字）被**有意丢弃**，不拼回剩余串。
                    # 这与旧 process_msg 行为一致：越界时数字被消费掉，
                    # 只有非数字的 prefix_part 才保留用于后续 h/s/a/n 扫描。
                    result.times = 1
                    s = prefix_part + s
            else:
                # time_str 不以数字结尾，将 time_str 拼回
                s = time_str + s
        # time_str 为空时：`#` 在最开头，times=1，s 已为 `#` 后部分

    # ── 2. BAB `b` 次数推导（仅当 `#` 不存在时生效） ─────────────────────
    elif "b" in s:
        time_str = s.split("b", 1)[0] + "b"
        i = 1
        collector = ""
        b_num = 0
        ab_num = 0
        while i <= len(time_str):
            _word = time_str[-i]
            if _word == "-" and "b" in collector and b_num == 0:
                try:
                    if collector != "b":
                        b_num = int(collector[:-1])
                    else:
                        b_num = 5
                except (ValueError, AssertionError):
                    b_num = 5
                collector = ""
            elif _word == "+" and ab_num == 0:
                try:
                    ab_num = int(collector)
                    collector = ""
                except (ValueError, AssertionError):
                    collector = ""
            else:
                if _word in "+-*/":
                    collector = ""
                else:
                    collector = _word + collector
            i += 1
        if b_num != 0 and ab_num != 0:
            try:
                t = math.ceil(ab_num / b_num)
                assert 1 <= t <= MULTI_ROLL_LIMIT
                result.times = t
            except (ValueError, AssertionError):
                result.times = 1
        # BAB 分支：保留 s 原样，不剥离 b 部分（exp_str 仍包含完整表达式）

    # ── 3. 连续前缀 `h/s/a/n` 扫描 ──────────────────────────────────────
    while s and s[0] in ("h", "s", "a", "n"):
        ch = s[0]
        if ch == "h":
            result.is_hidden = True
            s = s[1:]
        elif ch == "s":
            result.is_show_info = False
            s = s[1:]
        elif ch == "a":
            result.special_mode = "a"
            s = s[1:]  # 消费 'a'
            # 重写表达式为 a 模式
            if s:
                if s[0].isdigit():
                    s = "D100CS<=" + s
                elif s[-1].isdigit():
                    if len(s) >= 2 and s[-2].isdigit():
                        s = "D100CS<=" + s[-2:] + s[:-2]
                    else:
                        s = "D100CS<=" + s[-1] + s[:-1]
                else:
                    s = "D100CS<=50" + s
            else:
                s = "D100CS<=50"
            break  # a 模式完成后不再继续扫描
        elif ch == "n":
            result.special_mode = "n"
            s = s[1:]  # 消费 'n'
            if s and s[0] == "a":
                result.special_mode = "na"
                s = s[1:]  # 消费 'a'
                if s:
                    if s[0] in "+-*/":
                        s = "D10" + s
                    else:
                        s = "D10+" + s
                else:
                    s = "D10"
            else:
                if s:
                    if s[0] in "+-*/":
                        s = "D10" + s
                    else:
                        s = "D10+" + s
                else:
                    s = "D10"
            break  # n/na 模式完成后不再继续扫描

    # ── 4. `exp` 关键字检测 ──────────────────────────────────────────────
    if s[:3] == "exp":
        result.compute_exp = True
        s = s[3:]

    s = s.strip()

    # ── 5. 表达式 / 原因分割 ──────────────────────────────────────────────
    if len(s) < 100:
        sift = sift_roll_exp_and_reason(s)
        result.exp_str = sift[0]
        result.reason_str = sift[1]
    else:
        if " " in s and not result.compute_exp:
            exp_part, reason_part = s.split(" ", 1)
            result.exp_str = exp_part.strip()
            result.reason_str = reason_part.strip()
        else:
            result.exp_str = s
            result.reason_str = ""

    return result
