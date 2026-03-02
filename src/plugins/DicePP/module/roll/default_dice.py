import re
from typing import Any

from module.roll.roll_config import DICE_TYPE_DEFAULT, DICE_TYPE_MAX, DICE_NUM_MAX
from module.roll.roll_utils import RollDiceError
from module.roll.expression import preprocess_roll_exp

DEFAULT_DICE_EXPR = f"D{DICE_TYPE_DEFAULT}"
_PLACEHOLDER_PATTERN = re.compile(r'([0-9]*)D(?![0-9])')
_SIMPLE_D_PATTERN = re.compile(r'^(?:1D|D)([0-9]+)$')


def format_default_expr_from_input(raw_expr: str) -> str:
    """将用户输入的默认骰表达式标准化并校验."""
    sanitized = preprocess_roll_exp(raw_expr)
    if not sanitized:
        raise RollDiceError("默认骰表达式不能为空")
    if sanitized.isdigit():
        value = int(sanitized)
        if value < 2:
            raise RollDiceError("默认骰面数至少为2")
        if value > DICE_TYPE_MAX:
            raise RollDiceError(f"默认骰面数不能超过{DICE_TYPE_MAX}")
        return f"D{value}"
    if _PLACEHOLDER_PATTERN.search(sanitized):
        raise RollDiceError("默认骰表达式中不能包含未指定面的D")
    # 基础合法性校验：仅允许掷骰表达式常见字符
    allowed_chars = set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ+-*/()#<>=,._")
    if not set(sanitized).issubset(allowed_chars):
        raise RollDiceError(f"表达式 {sanitized} 含有非法字符")
    # 至少包含一个骰表达式或数字
    if "D" not in sanitized and not any(ch.isdigit() for ch in sanitized):
        raise RollDiceError(f"表达式 {sanitized} 缺少有效骰子或数值")
    return sanitized


def format_default_expr_from_storage(raw_value: Any) -> str:
    """读取持久化的默认骰配置并转换为合法表达式."""
    if raw_value is None:
        return DEFAULT_DICE_EXPR
    if isinstance(raw_value, int):
        value = max(2, min(int(raw_value), DICE_TYPE_MAX))
        return f"D{value}"
    text = str(raw_value).strip()
    if not text:
        return DEFAULT_DICE_EXPR
    try:
        return format_default_expr_from_input(text)
    except RollDiceError:
        return DEFAULT_DICE_EXPR


def extract_default_type_hint(default_expr: str) -> int:
    """对于类似D20的简单表达式提取骰面, 用于兼容旧逻辑."""
    match = _SIMPLE_D_PATTERN.match(default_expr)
    if match:
        value = int(match.group(1))
        if 2 <= value <= DICE_TYPE_MAX:
            return value
    return DICE_TYPE_DEFAULT


def apply_default_expr(exp_str: str, default_expr: str) -> str:
    """在掷骰表达式中注入默认骰表达式."""
    expr = exp_str.strip()
    if not expr:
        return default_expr
    default_wrapped = f"({default_expr})"
    if expr[0] in "+-*/":
        expr = f"{default_wrapped}{expr}"
    expr = _replace_placeholder(expr, default_expr, default_wrapped)
    return expr


def _repeat_default(default_expr: str, count: int) -> str:
    if count <= 0:
        raise RollDiceError("默认骰重复次数必须大于0")
    if count > DICE_NUM_MAX:
        raise RollDiceError(f"默认骰重复次数不能超过{DICE_NUM_MAX}")
    term = f"({default_expr})"
    if count == 1:
        return term
    return "(" + "+".join([term] * count) + ")"


def _replace_placeholder(expr: str, default_expr: str, default_wrapped: str) -> str:
    result = []
    length = len(expr)
    index = 0
    simple_match = _SIMPLE_D_PATTERN.match(default_expr)
    simple_type = int(simple_match.group(1)) if simple_match else None
    while index < length:
        char = expr[index]
        # 处理形如数字+D的占位符
        if char.isdigit():
            digit_start = index
            while index < length and expr[index].isdigit():
                index += 1
            if index < length and expr[index] == 'D' and (index + 1 == length or not expr[index + 1].isdigit()):
                if digit_start == 0 or not expr[digit_start - 1].isalnum():
                    count = int(expr[digit_start:index])
                    if simple_type is not None:
                        result.append(f"{count}D{simple_type}")
                    else:
                        result.append(_repeat_default(default_expr, count))
                    index += 1
                    continue
            # 回退: 不是占位符, 原样写回数字片段
            result.append(expr[digit_start:index])
            continue
        # 处理裸D的占位符
        if char == 'D' and (index + 1 == length or not expr[index + 1].isdigit()):
            if index == 0 or not expr[index - 1].isalnum():
                if simple_type is not None:
                    result.append(f"1D{simple_type}")
                else:
                    result.append(default_wrapped)
                index += 1
                continue
        result.append(char)
        index += 1
    return "".join(result)
