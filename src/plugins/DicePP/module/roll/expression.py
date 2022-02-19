import abc
import re
from typing import List, Tuple, Optional

from utils.string import to_english_str

from .roll_config import *
from .modifier import RollExpModifier, ROLL_MODIFIERS_DICT
from .connector import RollExpConnector, ROLL_CONNECTORS_DICT
from .roll_utils import RollDiceError, roll_a_dice, match_outer_parentheses
from .result import RollResult


class RollExpression(metaclass=abc.ABCMeta):
    """
    投骰表达式基类
    """

    @abc.abstractmethod
    def get_result(self) -> RollResult:
        """
        Returns:
            返回该掷骰表达式执行的结果
        """
        raise NotImplementedError()


class RollExpressionXDY(RollExpression):
    """
    基础表达式之一, 代表X个Y面骰
    """

    def __init__(self, xdy_str: str):
        """
        Args:
            xdy_str: 满足正则表达式"^[+-]?([1-9][0-9]*)?D([1-9][0-9]*)?$"
        """
        # 不需要调用父类初始化函数
        self.positive: int = 1  # 结果是正还是负
        if xdy_str[0] in ["+", "-"]:
            notation, xdy_str = xdy_str[0], xdy_str[1:]
            self.positive = 1 if notation == "+" else -1

        d_index = xdy_str.find("D")
        if d_index == -1:
            raise RollDiceError(f"解析表达式时出现错误 ERROR CODE: 100 Info: {xdy_str}")
        num_str, type_str = xdy_str[:d_index], xdy_str[d_index+1:]

        self.dice_num = int(num_str) if num_str else 1
        self.dice_type = int(type_str) if type_str else DICE_TYPE_DEFAULT

        if self.dice_num > DICE_NUM_MAX:
            raise RollDiceError(f"骰子数量不能大于{DICE_NUM_MAX}")
        if self.dice_type > DICE_TYPE_MAX:
            raise RollDiceError(f"骰子面数不能大于{DICE_TYPE_MAX}")

    def get_result(self) -> RollResult:
        """
        随机生成结果
        """
        res: RollResult = RollResult()
        res.val_list = [self.positive * roll_a_dice(self.dice_type) for _ in range(self.dice_num)]
        if self.positive == 1:
            res.info = "+".join([str(v) for v in res.val_list])
        else:
            res.info = "".join([str(v) for v in res.val_list])
        res.info = f"({res.info})"
        res.type = self.dice_type
        res.exp = f"{'-' if self.positive == -1 else ''}{self.dice_num}D{self.dice_type}"
        if self.dice_type == 20:  # 识别大成功与大失败
            res.d20_num = self.dice_num
            if self.dice_num == 1:
                res.d20_state = res.val_list[0]
        return res


class RollExpressionInt(RollExpression):
    """
    基础表达式之一, 代表一个整数
    """

    def __init__(self, val: int):
        # 不需要调用父类初始化函数
        self.val = val

        if val < DICE_CONSTANT_MIN or val > DICE_CONSTANT_MAX:
            raise RollDiceError(f"常量大小必须在{DICE_CONSTANT_MIN}至{DICE_CONSTANT_MAX}之间")

    def get_result(self) -> RollResult:
        """
        返回之前定义的固定值
        """
        res = RollResult()
        res.val_list = [self.val]
        res.info = str(self.val)
        res.exp = res.info
        return res


class RollExpressionComplex(RollExpression):
    """
    一个复杂表达式, 包含多个表达式, 修饰符, 连接符
    """

    def __init__(self):
        self.__exp_list: List[Tuple[RollExpConnector, RollExpression]] = []
        self.__mod_list: List[RollExpModifier] = []

    def append_exp(self, con: RollExpConnector, exp) -> None:
        """
        添加一个掷骰表达式结果
        Args:
            exp: Type[RollResult]
            con: Type[RollConnection]
        """
        if not issubclass(type(exp), RollExpression):
            raise RollDiceError(f"参数类型应为{RollExpression}, 当前为{type(exp)}")
        self.__exp_list.append((con, exp))

    def append_modifier(self, new_mod: RollExpModifier) -> None:
        """
        添加一个掷骰修饰器
        Args:
            new_mod: Type[RollModifier]
        """
        self.__mod_list.append(new_mod)

    def get_result(self) -> RollResult:
        """
        从上至下递归地得到RollInfo
        """
        res = RollResult()
        for con, exp in self.__exp_list:  # 用连接符合并结果
            res = con.connect(res, exp.get_result())
        if len(self.__exp_list) == 1:  # 连接符只有一个则应该保留信息
            res.type = self.__exp_list[0][1].get_result().type
        for mod in self.__mod_list:
            res = mod.modify(res)
        res.info = res.info if res.info[0] != "+" else res.info[1:]
        res.info = f"({res.info})"
        res.exp = res.exp if res.exp[0] != "+" else res.exp[1:]
        res.exp = f"({res.exp})"
        return res


def parse_roll_exp(input_str: str, depth: int = 0) -> RollExpression:
    """
    解析掷骰表达式字符串

    Args:
        input_str: 掷骰表达式字符串, 格式说明:
                        所有的字母都是大写; 不含空格和中文字符;
                        不包含#(重复掷骰在函数外通过多次调用返回的RollExpression处理);
        depth: 递归深度, 默认为0
    Returns:
        roll_exp: 解析后的表达式
    """
    if depth > PARSE_RECURSION_DEPTH_MAX:
        raise RollDiceError("超出最大解析深度")

    if not input_str:
        raise RollDiceError("表达式不能为空")
    # 去掉最外层的括号
    try:
        while input_str and match_outer_parentheses(input_str) == len(input_str) - 1:
            input_str = input_str[1:-1]
        if not input_str:
            raise RollDiceError("表达式含有空括号")
    except ValueError:
        raise RollDiceError("表达式含有不完整括号")

    # 先处理首个连接符, 若不是+或者-, 则默认为+
    if input_str[0] not in ("+", "-"):
        input_str = "+" + input_str

    # 递归终点之一: 是单个int
    try:
        val = int(input_str)
        return RollExpressionInt(val)
    except ValueError:
        pass
    # 递归终点之一: 形如(+/-)XDY, 不包含其他修饰符和连接符
    xdy_re = "([1-9][0-9]*)?D([1-9][0-9]*)?"
    xdy_match = re.match("[+-]"+xdy_re+"$", input_str)
    if xdy_match:
        return RollExpressionXDY(input_str)

    # 其他情况都要用复杂表达式处理
    exp = RollExpressionComplex()
    cur_str = input_str
    next_con = ROLL_CONNECTORS_DICT[cur_str[0]]()
    cur_str = cur_str[1:]

    cur_con: Optional[RollExpConnector]
    next_con: Optional[RollExpConnector]

    # 除开首个连接符以外一个连接符都没有的情况, 就是一个表达式+零个/多个修饰符
    has_con = any((s in cur_str) for s in ROLL_CONNECTORS_DICT.keys())

    if not has_con:
        prev_str = cur_str
        has_mod = True
        while has_mod:
            has_mod = False
            for mod_re in ROLL_MODIFIERS_DICT.keys():
                mod_match = re.search(mod_re, cur_str)
                if mod_match:
                    m_span = mod_match.span()
                    mod_str, cur_str = cur_str[m_span[0]:m_span[1]], cur_str[:m_span[0]] + cur_str[m_span[1]:]
                    exp.append_modifier(ROLL_MODIFIERS_DICT[mod_re](mod_str))
                    has_mod = True
                    break
        if prev_str == cur_str:  # 解析一圈都没变化, 说明格式不对, 开始死循环了
            raise RollDiceError(f"表达式{cur_str}格式不正确")
        core_exp = parse_roll_exp(cur_str, depth+1)  # 解析剩下的部分
        exp.append_exp(next_con, core_exp)
        return exp

    # 处理连接符, 字符串应当是 连接符+子表达式+连接符+子表示式...
    # 循环处理当前表达式和下一个连接符
    cur_exp: Optional[RollExpression]
    left_str = cur_str
    while left_str:
        cur_con, next_con = next_con, None  # 当前的连接符和下一个连接符

        try:
            par_match = match_outer_parentheses(left_str)
        except ValueError:
            raise RollDiceError("含有不完整的括号")
        if par_match != -1:  # 有括号则递归处理括号
            par_str, left_str = left_str[:par_match+1], left_str[par_match+1:]
            cur_exp = parse_roll_exp(par_str[1:-1], depth+1)
            # 查找连接符
            if left_str:
                for cur_symbol in ROLL_CONNECTORS_DICT.keys():
                    if left_str[:len(cur_symbol)] == cur_symbol:
                        next_con = ROLL_CONNECTORS_DICT[cur_symbol]()
                        left_str = left_str[len(cur_symbol):]
                        break
                if not next_con:
                    raise RollDiceError(f"表达式{left_str}格式不正确")
            exp.append_exp(cur_con, cur_exp)
        else:  # 没有括号则暴力匹配连接符来找到最左侧的表达式和下一个连接符
            next_con_str_index = -1
            next_symbol = None
            # python的dict自从3.5以后默认是有序的
            for cur_symbol in ROLL_CONNECTORS_DICT.keys():
                cur_index = left_str.rfind(cur_symbol)
                # 早注册symbol的优先于晚注册symbol的
                if cur_index != -1:
                    next_con_str_index, next_symbol = cur_index, cur_symbol
                    break
            if next_symbol:  # 如果有下一个连接符
                cur_exp = parse_roll_exp(left_str[:next_con_str_index], depth+1)
                exp.append_exp(cur_con, cur_exp)
                next_con = ROLL_CONNECTORS_DICT[next_symbol]()
                next_exp = parse_roll_exp(left_str[next_con_str_index+1:], depth+1)
                exp.append_exp(next_con, next_exp)
            else:
                cur_exp = parse_roll_exp(left_str, depth+1)
                exp.append_exp(cur_con, cur_exp)
            break

        if next_con and left_str == "":  # 如果存在下一个连接符但表达式为空, 肯定有问题
            raise RollDiceError(f"连接符{next_con.symbol}后为空表达式!")
    return exp


def preprocess_roll_exp(input_str: str) -> str:
    """
    预处理掷骰表达式
    """
    output_str = input_str.strip()
    # output_str = re.sub(r"\s", "", output_str)  # 去除空格和换行
    output_str = output_str.upper()
    output_str = to_english_str(output_str)
    output_str = re.sub(r"(^|[^0-9])(D[0-9]*优势)",
                        lambda match: match.group(1) + "2" + match.group(2)[:-2] + "K1",
                        output_str)
    output_str = re.sub(r"(^|[^0-9])(D[0-9]*劣势)",
                        lambda match: match.group(1) + "2" + match.group(2)[:-2] + "KL1",
                        output_str)
    output_str = re.sub(r"^(.+)抗性$",
                        lambda match: f"({match.group(1)})/2",
                        output_str)
    output_str = re.sub(r"^(.+)易伤$",
                        lambda match: f"({match.group(1)})*2",
                        output_str)
    return output_str


def exec_roll_exp(input_str: str) -> RollResult:
    """
    根据输入执行一次掷骰表达式并返回结果
    若需要重复执行多次同一掷骰表达式, 应当直接调用preprocess_roll_exp和parse_roll_exp并重复get_result
    """
    exp_str = preprocess_roll_exp(input_str)
    exp = parse_roll_exp(exp_str)
    return exp.get_result()


def is_roll_exp(input_str: str) -> bool:
    """
    如果输入一个合法的掷骰表达式, 返回True, 否则返回False
    """
    try:
        parse_roll_exp(preprocess_roll_exp(input_str))
    except RollDiceError:
        return False
    return True
