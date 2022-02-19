"""
掷骰工具
"""

from random import randint
from typing import List, Tuple


class RollDiceError(Exception):
    """
    因为掷骰产生的异常, 说明操作失败的原因, 应当在上一级捕获
    """
    def __init__(self, info: str):
        self.info = info

    def __str__(self):
        return f"[Roll] [Error] {self.info}"


def roll_a_dice(dice_type: int) -> int:
    """
    返回一颗dice_type面骰的结果
    """
    return randint(1, dice_type)


def match_outer_parentheses(input_str: str) -> int:
    """
    若输入字符串的第一个字符是(, 返回对应的)的索引. 若不存在对应的), 抛出一个ValueError. 若首字母不是(, 返回-1
    """
    if not input_str or input_str[0] != "(":
        return -1
    level = 0
    for index, char in enumerate(input_str):
        if char == "(":
            level += 1
        elif char == ")":
            level -= 1
        if level == 0:
            return index
    raise ValueError("Input's parentheses is incomplete!")


def remove_redundant_parentheses(input_str: str, readable: bool = True) -> str:
    """
    递归地去掉字符串中一些冗余的括号, 字符串必须不包含空格, 即连接符紧跟着括号
    readable为True则会尽可能少的清除括号, readable为False则会在保留数学正确性的情况下清除尽可能多的括号
    """
    priority_dict = {"+": 1, "-": 2, "*": 3, "/": 4}
    max_priority = 5

    def remove_par(par_str: str, outer_priority_lhs: int, outer_priority_rhs: int) -> str:
        """
        尝试去掉字符串中冗余的括号
        Args:
            par_str: 字符串必须以(开头并以)结尾
            outer_priority_lhs: 外层左侧连接符的运算优先级
            outer_priority_rhs: 外层右侧连接符的运算优先级
        """
        try:
            assert par_str and par_str[0] == "(" and par_str[-1] == ")"
        except AssertionError:
            raise RollDiceError(f"去除冗余括号算法错误! 信息: {par_str} {outer_priority_lhs} {outer_priority_rhs}")
        # 找到内部所有的括号, 不包括最外层的
        inner_par_info_list: List[Tuple[int, int]] = []  # 每一个括号表达式的左索引和右索引, 按左索引从小到大排列, 左右不重叠
        par_index_lhs = 1
        while par_index_lhs < len(par_str)-1:  # 从左往右扫描
            if par_str[par_index_lhs] == "(":
                par_index_rhs = par_index_lhs + match_outer_parentheses(par_str[par_index_lhs:])
                inner_par_info_list.append((par_index_lhs, par_index_rhs))
                par_index_lhs = par_index_rhs + 1
            else:
                par_index_lhs += 1
        # 找到内部所有的运算符, 不包括内部括号内的
        inner_operators: List[Tuple[int, int]] = []  # 每一个运算符的索引和运算优先级, 按索引从小到大排列
        for operator, priority in priority_dict.items():
            try:
                left = 1
                while left < len(par_str)-1:
                    index = par_str.index(operator, left)
                    is_valid = True
                    for par_info in inner_par_info_list:
                        if par_info[0] < index < par_info[1]:
                            is_valid = False
                            left = par_info[1]+1
                            break
                    if is_valid:
                        inner_operators.append((index, priority))
                        left = index + 1
            except ValueError:
                pass
        inner_operators = sorted(inner_operators, key=lambda x: x[0])
        # print(par_str, inner_par_info_list, inner_operators, outer_priority_rhs, outer_priority_lhs)
        # 递归剔除内部括号
        if len(inner_par_info_list) != 0:
            if len(inner_operators) == 0:  # 内部没有运算符, 直接递归剔除[1:-1]
                assert len(inner_par_info_list) == 1, str(inner_par_info_list)
                output_str = f"({remove_par(par_str[1:-1], outer_priority_lhs, outer_priority_rhs)})"
            else:  # 内部有运算符, 尝试剔除内部的括号
                output_list = []
                priority_lhs, priority_rhs = outer_priority_lhs, inner_operators[0][1]  # 当前处理的括号的左侧优先级和右侧优先级
                operator_index = 0
                left, right = 0, inner_operators[0][0]  # 左右侧运算符的索引
                for par_info in inner_par_info_list:
                    while right < par_info[1]:  # 找到最右侧的运算符
                        if operator_index < len(inner_operators):
                            right = inner_operators[operator_index][0]
                            priority_lhs, priority_rhs = priority_rhs, inner_operators[operator_index][1]
                            operator_index += 1
                        else:
                            right = par_info[1]+1
                            priority_lhs, priority_rhs = priority_rhs, outer_priority_rhs
                    output_list.append(par_str[left:par_info[0]])
                    if readable:  # 为了可读性, 不需要去掉所有没有数学意义的括号, 所以把记录优先级相关的去掉了而是直接用最高优先级跳过后面的检查
                        output_list.append(remove_par(par_str[par_info[0]:par_info[1]+1], max_priority, max_priority))
                    else:
                        output_list.append(remove_par(par_str[par_info[0]:par_info[1] + 1], priority_lhs, priority_rhs))
                    left = right

                output_list.append(par_str[left:])
                # print("output_list", output_list)
                output_str = "".join(output_list)
        else:
            output_str = par_str

        # 判断自己最外部的括号能不能被剔除
        if len(inner_operators) == 0:  # 内部没有运算符, 则可以去掉括号
            can_remove_outer = True
        else:
            can_remove_outer = (outer_priority_lhs <= inner_operators[0][1]
                                and outer_priority_rhs <= inner_operators[-1][1])
        # print(par_str, output_str, can_remove_outer)
        if can_remove_outer:
            return output_str[1:-1]
        else:
            return output_str

    return remove_par(f"({input_str})", -1, -1)
