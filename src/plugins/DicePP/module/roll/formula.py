import operator
from typing import Dict, Type, Union, Iterable, Tuple, Any


def condition_split(input_str:str) -> Tuple[Any,str,int]:
    """
    分割<8这种文本为某一 operator 方法与 数值
    """
    opr = operator.eq
    comp: str = "="
    rhs: str = ""
    val: int = 0
    if input_str[0] == ">":
        if len(input_str) > 1 and input_str[1] == "=":
            opr = operator.ge
            comp = ">="
            rhs = input_str[2:]
        else:
            opr = operator.gt
            comp = ">"
            rhs = input_str[1:]
    elif input_str[0] == "<":
        if len(input_str) > 1 and input_str[1] == "=":
            opr = operator.le
            comp = "<="
            rhs = input_str[2:]
        else:
            opr = operator.lt
            comp = "<"
            rhs = input_str[1:]
    elif input_str[0] == "=":
        if len(input_str) > 1 and input_str[1] == "=":
            rhs = input_str[2:]
        else:
            rhs = input_str[1:]
    else:
        rhs = input_str
    
    try:
        val = int(rhs)
    except ValueError:
        raise RollDiceError(f"无法处理的条件公式:{input_str}")
    return (opr,comp,val)

def condition_expectation(var_min: int,var_max: int,opr: Any,var_op: int) -> float:
    """
    计算满足条件值的价值率
    """
    result: float = 0.0
    var_count: int = var_max - var_min + 1 # 一共多少个值
    var_valuable: int = 0 # 有多少个值是有价值的
    if opr == operator.ge:
        # 大于等于
        if var_op <= var_max:
            var_valuable = var_max - var_op + 1
    elif opr == operator.gt:
        # 大于
        if var_op < var_max:
            var_valuable = var_max - var_op
    elif opr == operator.le:
        # 小于等于
        if var_op >= var_min:
            var_valuable = var_op - var_min + 1
    elif opr == operator.lt:
        # 小于
        if var_op > var_min:
            var_valuable = var_op - var_min
    elif opr == operator.eq:
        #等于
        if var_op >= var_min and var_op <= var_max:
            var_valuable = 1
    else:
        raise RollDiceError(f"无法处理的条件方法")
    # 计算价值率
    if var_count == 0:
        raise RollDiceError(f"错误的范围")
    
    result = round(float(var_valuable) / float(var_count),2)
    return result

def condition_range(var_min: int,var_max: int,opr: Any,var_op: int) -> Tuple[int,int]:
    """
    计算满足条件值的最小值与最大值
    """
    var_valuable_min: int = var_min
    var_valuable_max: int = var_max
    if opr == operator.ge:
        # 大于等于
        if var_op <= var_max:
            var_valuable_min = var_op
    elif opr == operator.gt:
        # 大于
        if var_op < var_max:
            var_valuable_min = var_op + 1
    elif opr == operator.le:
        # 小于等于
        if var_op >= var_min:
            var_valuable_max = var_op
    elif opr == operator.lt:
        # 小于
        if var_op > var_min:
            var_valuable_max = var_op - 1
    elif opr == operator.eq:
        #等于
        if var_op >= var_min and var_op <= var_max:
            var_valuable_min = var_op
            var_valuable_max = var_op
        else:
            raise RollDiceError(f"错误的范围")
    else:
        raise RollDiceError(f"无法处理的条件方法")

    return (var_valuable_min,var_valuable_max)