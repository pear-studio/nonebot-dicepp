import abc
import re
from typing import List, Tuple, Optional, Any, Union

from utils.string import to_english_str

from .roll_config import *
from .modifier import RollExpModifier, ROLL_MODIFIERS_DICT
from .connector import RollExpConnector, ROLL_CONNECTORS_DICT, REModSubstract, REModAdd
from .roll_utils import RollDiceError, roll_a_dice, match_outer_parentheses, clear_border_parentheses, remove_redundant_parentheses
from .result import RollResult

XDY_RE = "([1-9][0-9]*)?D([1-9][0-9]*)?"
XB_RE = "([1-9][0-9]*)?B"
AVAILABLE_CHARACTER = "1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZ.+-*/><=#()优劣势抗性易伤"

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

class RollExpressionFormula(RollExpression):
    """
    整个表达式的一个整合
    """
    def __init__(self, exp_str: str = "1D20"):
        # 不需要调用父类初始化函数
        self.exp_str = exp_str
        self.result_var = 0
        self.mod_list: List[RollExpModifier] = []
        self.exp_list: List[Any] = [] # 由parse处理

    def append_modifier(self, new_mod: RollExpModifier) -> None:
        """
        添加一个掷骰修饰器
        """
        self.mod_list.append(new_mod)

    def get_result(self) -> RollResult:
        """
        返回运算后的所有子内容
        """
        # res = RollResult()
        res = calculate_roll_exp(self.exp_list)
        #res.info = "(" + res.info + ")"
        #res.exp = "(" + res.exp + ")"
        return res

class RollExpressionInt(RollExpression):
    """
    基础表达式之一, 代表一个整数
    """

    def __init__(self, exp_str: str = "0"):
        # 不需要调用父类初始化函数
        self.exp_str = exp_str
        self.result_var = 0
        try:
            self.val = int(exp_str)
        except ValueError:
            raise RollDiceError(f"数值错误: {exp_str}")
        self.result_var = self.val

        if self.val < DICE_CONSTANT_MIN or self.val > DICE_CONSTANT_MAX:
            raise RollDiceError(f"常量大小必须在{DICE_CONSTANT_MIN}至{DICE_CONSTANT_MAX}之间")

    def get_result(self) -> RollResult:
        """
        返回之前定义的固定值
        """
        res = RollResult()
        res.val_list = [self.val]
        res.info = str(self.val)
        res.exp = self.exp_str
        return res

class RollExpressionFloat(RollExpression):
    """
    基础表达式之一, 代表一个浮点数
    """

    def __init__(self, exp_str: str = "0"):
        # 不需要调用父类初始化函数
        self.exp_str = exp_str
        self.result_var = 0
        try:
            if exp_str.endswith("F"):
                self.val = round(float(exp_str[:-1]),2)
            else:
                self.val = round(float(exp_str),2)
        except ValueError:
            raise RollDiceError(f"数值错误: {exp_str}")
        self.result_var = self.val

        if self.val < DICE_CONSTANT_MIN or self.val > DICE_CONSTANT_MAX:
            raise RollDiceError(f"常量大小必须在{DICE_CONSTANT_MIN}至{DICE_CONSTANT_MAX}之间")

    def get_result(self) -> RollResult:
        """
        返回之前定义的固定值
        """
        res = RollResult()
        res.val_list = [self.val]
        res.info = str(round(self.val,2))
        res.exp = self.exp_str
        res.float_state = True
        return res

class RollExpressionNull(RollExpression):
    """
    基础表达式之一, 代表出错
    """

    def __init__(self, exp_str: str = ""):
        # 不需要调用父类初始化函数
        self.exp_str = exp_str
        self.result_var = 0

    def get_result(self) -> RollResult:
        """
        返回一个报错
        """
        res = RollResult()
        res.val_list = [0]
        res.info = "[NULL]"
        res.exp = "[NULL]"
        return res

class RollExpressionXDY(RollExpression):
    """
    新的XDY表达式，为老的XDY与Complex的合体
    """
    def __init__(self, exp_str: str = "1D20", default_type: int = DICE_TYPE_DEFAULT):
        self.exp_str = exp_str
        self.result_var = 0
        self.mod_list: List[RollExpModifier] = []

        # 处理附加指令
        cur_str: str = exp_str
        prev_str: str = exp_str
        has_mod = True
        while has_mod:
            has_mod = False
            for mod_re in ROLL_MODIFIERS_DICT.keys():
                mod_match = re.search(mod_re, cur_str)
                if mod_match:
                    m_span = mod_match.span()
                    mod_str, cur_str = cur_str[m_span[0]:m_span[1]], cur_str[:m_span[0]] + cur_str[m_span[1]:]
                    self.append_modifier(ROLL_MODIFIERS_DICT[mod_re](mod_str))
                    has_mod = True
                    break
            if prev_str == cur_str:  # 解析一圈都没变化, 说明没有匹配的
                break
            prev_str = cur_str
        # 处理骰子部分
        d_index = cur_str.find("D")
        if d_index == -1:
            raise RollDiceError(f"解析表达式时未发现XDY格式: {exp_str}")
        num_str, type_str = cur_str[:d_index], cur_str[d_index+1:]
        
        try:
            self.dice_num = int(num_str) if num_str else 1
            self.dice_type = int(type_str) if type_str else default_type
        except ValueError:
            raise RollDiceError(f"X或Y数值错误: {exp_str}")

        if self.dice_num > DICE_NUM_MAX:
            raise RollDiceError(f"骰子数量不能大于{DICE_NUM_MAX}")
        if self.dice_type > DICE_TYPE_MAX:
            raise RollDiceError(f"骰子面数不能大于{DICE_TYPE_MAX}")

    def append_modifier(self, new_mod: RollExpModifier) -> None:
        """
        添加一个掷骰修饰器
        """
        self.mod_list.append(new_mod)

    def get_result(self) -> RollResult:
        """
        生成投掷结果
        """
        res: RollResult = RollResult()
        if self.dice_type < 2:
            res.val_list = [self.dice_type for _ in range(self.dice_num)]
        else:
            res.val_list = [roll_a_dice(self.dice_type) for _ in range(self.dice_num)]
        res.info = "".join(["[" + str(v) + "]" for v in res.val_list])
        # res.info = f"({res.info})"
        res.type = self.dice_type
        res.exp = f"{self.dice_num}D{self.dice_type}"
        # 记录d20与d100数量，与是否大成功
        if self.dice_type == 20:
            res.d20_num = self.dice_num
            res.success_or_fail(20,1)
        elif self.dice_type == 100:
            res.d100_num = self.dice_num
            res.success_or_fail(1,100)
        res.dice_num = self.dice_num
        if self.dice_type > 1:
            res.average_list = [round((val-1)*100/(self.dice_type-1)) for val in res.val_list]
        # 应用修饰
        for mod in self.mod_list:
            res = mod.modify(res)
        res.info = res.info[1:] if res.info.startswith("+") else res.info
        #res.info = f"({res.info})"
        res.exp = res.exp[1:] if res.exp.startswith("+") else res.exp
        #res.exp = f"({res.exp})"
        return res

class RollExpressionXDYEXP(RollExpression):
    """
    XDY快速期望表达式
    """
    def __init__(self, exp_str: str = "1D20", default_type: int = DICE_TYPE_DEFAULT):
        # 直接复制一个XDY，不作代码了
        self.rollexpression = RollExpressionXDY(exp_str,default_type)
        self.dice_type = self.rollexpression.dice_type
        self.dice_num = self.rollexpression.dice_num
        self.mods = len(self.rollexpression.mod_list)
        if self.mods > 1:
            raise RollDiceError("修饰器过多，目前快速期望仅支持0个或1个修饰器")

    def get_result(self) -> RollResult:
        """
        生成期望结果而非掷骰结果，暂时只支持0个或1个修饰器
        """
        res: RollResult = RollResult()
        res.float_state = True
        # 不一定用，但先算了总没错
        if self.dice_type < 2:
            res.val_list = [float(self.dice_type) for _ in range(self.dice_num)]
        else:
            res.val_list = [float(self.dice_type+1)/2 for _ in range(self.dice_num)]
        res.type = self.dice_type
        res.dice_num = self.dice_num
        res.exp = f"{self.dice_num}D{self.dice_type}"
        # 处理是否修饰的问题
        if self.mods == 0:
            # 如果没有修饰，那直接返回高速运算的期望结果（Float类）
            res.info = str(round(sum(res.val_list),2))
        else:
            # 应用修饰，让修饰自己处理一组合适的期望出来
            res = self.rollexpression.mod_list[0].expectation(res)
        res.info = res.info[1:] if res.info.startswith("+") else res.info
        #res.info = f"({res.info})"
        res.exp = res.exp[1:] if res.exp.startswith("+") else res.exp
        #res.exp = f"({res.exp})"
        return res

class RollExpressionXB(RollExpression):
    """
    全回合攻击的XB表达式
    """
    def __init__(self, exp_str: str = "5B"):
        self.exp_str = exp_str
        self.result_var = 0
        self.time_counter = 0
        # 处理骰子部分
        d_index = exp_str.find("B")
        if d_index == -1:
            raise RollDiceError(f"解析表达式时未发现XB格式: {exp_str}")
        num_str= exp_str[:d_index]
        
        try:
            self.multiply_num = int(num_str) if num_str else 5
        except ValueError:
            raise RollDiceError(f"XB数值错误: {exp_str}")
        
        if self.multiply_num <= 0 or self.multiply_num >= 100:
            raise RollDiceError(f"XB数值错误: {exp_str}")

    def get_result(self) -> RollResult:
        """
        生成以调用次数为准的值
        """
        res = RollResult()
        res.val_list = [self.time_counter * self.multiply_num]
        self.time_counter += 1
        res.info = str(res.val_list[0])
        res.exp = self.exp_str
        return res

def split_roll_str(input_str: str) -> Tuple[List[str],List[int]]:
    """
    分割掷骰表达式字符串
    Args:
        input_str: 掷骰表达式字符串, 格式说明:
                        所有的字母都是大写; 不含空格和中文字符;
                        不包含#(重复掷骰在函数外通过多次调用返回的RollExpression处理);
    输出结果的[0]为args_list
    """

    if not input_str:
        raise RollDiceError("表达式不能为空")
    
    roll_list: List[str] = []
    depth_list: List[int] = []
    arg_now: str = ""
    pl: int = 0
    word: str = ""
    depth: int =  0
    # 开始进行深度判断
    while pl < len(input_str):
        if depth > PARSE_RECURSION_DEPTH_MAX:
            raise RollDiceError("超出最大解析深度")
        word = input_str[pl]
        if word in ROLL_CONNECTORS_DICT.keys():
            if arg_now:
                depth_list.append(depth)
                roll_list.append(arg_now)
                depth_list.append(depth)
                roll_list.append(word)
                arg_now = ""
            else:
                depth_list.append(depth)
                roll_list.append(word)
        elif word == "(":
            if arg_now:
                depth_list.append(depth)
                roll_list.append(arg_now)
                arg_now = ""
            depth += 1
        elif word == ")":
            if arg_now:
                depth_list.append(depth)
                roll_list.append(arg_now)
                arg_now = ""
            depth -= 1
        elif word == " ":
            break
        else:
            arg_now += word
        pl += 1
    if arg_now:
        depth_list.append(depth)
        roll_list.append(arg_now)
    return (roll_list,depth_list)

def combine_roll_str(split_list: Tuple[List]) -> str:
    """
    用split的数据重新合并成掷骰表达式字符串
    """
    if len(split_list) == 2:
        roll_list: List[int] = split_list[0]
        depth_list: List[int] = split_list[1]
        result: str = ""
        depth: int = 0
        for i in range(len(roll_list)):
            if depth > depth_list[i]:
                result += ")" * (depth - depth_list[i])
            elif depth < depth_list[i]:
                result += "(" * (depth_list[i] - depth)
            depth = depth_list[i]
            result += roll_list[i]
        if depth > 0:
            result += ")" * (depth)
        elif depth < 0:
            result = "(" * abs(depth) + result
        return result
    else:
        return ""

def parse_single_roll_exp(input_str: str, default_type: int = DICE_TYPE_DEFAULT) -> Union[RollExpression, RollExpConnector]:
    """
    将str处理为一个有效的RollExpression
    """
    # 空白
    if len(input_str) == 0:
        return RollExpressionNull()
    # 单个Int数值
    if input_str.isdigit():
        return RollExpressionInt(input_str)
    # 单个Float数值
    if "." in input_str and input_str.replace(".","").isdigit():
        return RollExpressionFloat(input_str)
    if input_str.endswith("F") and input_str[:-1].replace(".","").isdigit():
        return RollExpressionFloat(input_str)
    # 连接符号
    if input_str in ROLL_CONNECTORS_DICT.keys():
        return ROLL_CONNECTORS_DICT[input_str]
    # 掷骰指令格式。XDY，包含修饰符
    if re.match(XDY_RE, input_str):
        if input_str.endswith("EXP"):        
            return RollExpressionXDYEXP(input_str[:-3],default_type)
        else:
            return RollExpressionXDY(input_str,default_type)
    # BAB逐步减值格式。XB
    if re.match(XB_RE, input_str):
        return RollExpressionXB(input_str)
    # 特殊处理：如果是错误的例如“1地精”这种，尝试给他改正
    if "D" in input_str:
        for index in range(len(input_str), -1, -1):
            if re.match(XDY_RE, input_str[:index]):
                return RollExpressionXDY(input_str[:index],default_type)
    elif "B" in input_str:
        for index in range(len(input_str), -1, -1):
            if re.match(XB_RE, input_str[:index]):
                return RollExpressionXB(input_str[:index])
    else:
        for index in range(len(input_str), -1, -1):
            if input_str[:index].isdigit():
                return RollExpressionInt(input_str[:index])
    # 如果都不满足,返回一个默认值Null
    return RollExpressionNull(input_str)
    
def calculate_roll_exp(roll_exp_list: List[Any]) -> RollResult:
    """
    用处理好的嵌套List生成掷骰结果
    """
    final_result = 0
    candidate : List[Any] = []
    # 将非数值非连接符的内容全部变为数值与连接符
    for roll in roll_exp_list:
        __type = type(roll)
        if __type is list:
            sub_result: RollResult = calculate_roll_exp(roll)
            sub_result.info = "(" + sub_result.info + ")"
            sub_result.exp = "(" + sub_result.exp + ")"
            candidate.append(sub_result) # RollResult
        elif issubclass(type(roll), RollExpression):
            candidate.append(roll.get_result()) # RollResult
        elif roll in ROLL_CONNECTORS_DICT.values():
            candidate.append(roll) # RollExpConnector
        else:
            raise RollDiceError(f"未知参数类型 {str(__type)}")
    # 按照dict中的顺序处理所有连接符
    for calculation in ROLL_CONNECTORS_DICT.values():
        length: int = len(candidate)
        index: int = 0
        while index < length:
            if candidate[index] is calculation:
                place = index
                this = candidate[index]
                # 右边为空的情况下就使用代替品
                if index+1 >= len(candidate):
                    # 用一个默认的代替品
                    right = RollResult()
                    right.val_list = [0]
                    right.info = ""
                    right.exp = ""
                else:
                    right = candidate[index+1]
                    candidate.pop(index+1)
                    length -= 1
                # 左边为空的情况下就使用代替品
                if index-1 < 0:
                    # 用一个默认的代替品
                    left = RollResult()
                    left.val_list = [0]
                    left.info = ""
                    left.exp = ""
                else:
                    left = candidate[index-1]
                    candidate.pop(index-1)
                    place -= 1
                    length -= 1
                if not (type(left) is RollResult and type(right) is RollResult):
                    raise RollDiceError("连接符两侧参数错误")
                # 1+1 = 2__ 中间为index
                candidate[index-1] = this.connect(left,right)
                continue
            index += 1
    if len(candidate) > 1:
        raise RollDiceError("出现无法正常处理到只剩下一个结果的情况")
    if type(candidate[0]) is not RollResult:
        raise RollDiceError("剩下非结果的内容")
    return candidate[0]
            
def create_leveling_list(var_list: List[Any],depth_list: List[int]) -> List[Any]:
    """
    使用数据列表与深度列表嵌套构建一个多层list
    """
    result: List[Any] = []
    depth: int = min(depth_list)
    leveling: bool = False
    level_depth: int = depth
    leveling_var_list: List[Any] = []
    leveling_depth_list: List[int] = []
    for index in range(len(var_list)):
        if leveling:
            if depth_list[index] <= depth:
                leveling = False
                result.append(create_leveling_list(leveling_var_list,leveling_depth_list))
                leveling_var_list = []
                leveling_depth_list = []
        if depth_list[index] == depth:
            result.append(var_list[index])
        elif depth_list[index] > depth:
            leveling_var_list.append(var_list[index])
            leveling_depth_list.append(depth_list[index])
            level_depth = depth_list[index]
            leveling = True
        if depth_list[index] < depth:
            break
    if leveling:
        result.append(create_leveling_list(leveling_var_list,leveling_depth_list))
    return result

def parse_roll_exp(input_str: str, default_type: int = DICE_TYPE_DEFAULT) -> RollExpression:
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
    # 去掉最外层的括号
    try:
        input_str = clear_border_parentheses(input_str)
        if not input_str:
            input_str = "1D" + str(default_type)
    except ValueError:
        raise RollDiceError("表达式含有不完整括号")

    # 创建表达式
    exp: RollExpressionFormula = RollExpressionFormula(input_str)
    split_list = split_roll_str(input_str)
    thing_list: List[Any] = []
    for item in split_list[0]:
        thing_list.append(parse_single_roll_exp(item,default_type))
    exp.exp_list = create_leveling_list(thing_list, split_list[1])
    return exp
    """
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
        return RollExpressionXDY(input_str,default_type)

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
    """


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

def sift_roll_exp_and_reason(input_str: str) -> Tuple[str,str]:
    """
    分割一个表达式与其原因
    """
    input_str = input_str.strip()
    length: int = len(input_str)
    exp_right: int = len(input_str)  # 肯定合格的表达式的右侧位置（不包含本值）
    # 空格为直接分隔符，从空格开始算原因
    if " " in input_str:
        exp_right = input_str.find(" ")  # 空格右侧一定是原因
    # 某些用户没有用空格将表达式与原因分开的习惯, 为了适配只能强行确认一遍
    for index in range(exp_right):
        # 检测非法字符
        if input_str[index].upper() not in AVAILABLE_CHARACTER:
            exp_right = index  # 找到非法字符
            break
    """
    # 这是老的暴力匹配
    exp_str = "d"
    reason_str = msg_str
    for reason_index in range(len(msg_str), 0, -1):
        is_valid = True
        exp_test, reason_test = msg_str[:reason_index].strip(), msg_str[reason_index:].strip()
        try:
            parse_roll_exp(preprocess_roll_exp(exp_test))#.get_result()
        except RollDiceError:
            is_valid = False
        if is_valid:
            exp_str, reason_str = exp_test, reason_test
            break
    """
    return (input_str[0:exp_right].strip().upper(),input_str[exp_right:length].strip())

def exec_roll_exp(input_str: str) -> RollResult:
    """
    根据输入执行一次掷骰表达式并返回结果
    若需要重复执行多次同一掷骰表达式, 应当直接调用preprocess_roll_exp和parse_roll_exp并重复get_result
    """
    exp = parse_roll_exp(preprocess_roll_exp(input_str))
    result: RollResult = exp.get_result()
    # result.info = remove_redundant_parentheses(result.info,readable=False) #不能在这里偷懒
    return result


def is_roll_exp(input_str: str) -> bool:
    """
    如果输入一个合法的掷骰表达式, 返回True, 否则返回False
    """
    try:
        parse_roll_exp(preprocess_roll_exp(input_str))
    except RollDiceError:
        return False
    return True
