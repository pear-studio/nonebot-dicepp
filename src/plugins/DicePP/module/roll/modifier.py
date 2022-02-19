import abc
import operator
from typing import Dict, Type, Union, Iterable


from .roll_config import *
from .roll_utils import RollDiceError, roll_a_dice
from .result import RollResult


class RollExpModifier(metaclass=abc.ABCMeta):
    """
    用于表示掷骰表达式修饰符
    """
    @abc.abstractmethod
    def __init__(self, args: str):
        # 确保init签名一致
        pass

    @abc.abstractmethod
    def modify(self, roll_res: RollResult) -> RollResult:
        """
        修改并返回掷骰数据, 返回的变量应该基于初始变量
        """
        raise NotImplementedError()


ROLL_MODIFIERS_DICT: Dict[str, Type[RollExpModifier]] = {}


def roll_modifier(regexp: Union[str, Iterable[str]]):
    """
    类修饰器, 将自定义掷骰表达式修饰符注册到列表中
    Args:
        regexp: 一个或多个正则表达式, 用来唯一地匹配该修饰符, 若有多个符合则较早定义的优先
    """
    def inner(cls: Type[RollExpModifier]):
        """
        Args:
            cls: 修饰的类必须继承自RollExpModifier
        Returns:
            cls: 返回修饰后的cls
        """
        assert issubclass(cls, RollExpModifier)
        if type(regexp) is str:
            symbols = [regexp]
        else:
            symbols = regexp
        for s in symbols:
            assert " " not in s and s.isupper()
            assert s not in ROLL_MODIFIERS_DICT.keys()
            ROLL_MODIFIERS_DICT[s] = cls
        return cls
    return inner


# 注意定义的顺序也即是执行优先级, 越早定义则优先级越高
# 同一种修饰符的匹配优先级为从左到右


@roll_modifier("(R|X|XO)(<|>|=)?[1-9][0-9]*")
class REModReroll(RollExpModifier):
    """
    表示某一个骰子满足条件则重骰, 爆炸, 或者爆炸一次
    """
    def __init__(self, args: str):
        super().__init__(args)
        # 模式
        if args[1] == "O":
            mod, args = args[:2], args[2:]
        else:
            mod, args = args[0], args[1:]

        # 判定条件
        if args[0] in ("<", ">", "="):
            comp, rhs = args[0], args[1:]
        else:
            comp, rhs = "=", args

        if comp == ">":
            self.op = operator.gt
        elif comp == "<":
            self.op = operator.lt
        elif comp == "=":
            self.op = operator.eq

        self.mod: str = mod
        self.comp: str = comp
        self.rhs: int = int(rhs)
        if self.rhs < DICE_CONSTANT_MIN or self.rhs > DICE_CONSTANT_MAX:
            raise RollDiceError(f"常量大小必须在{DICE_CONSTANT_MIN}至{DICE_CONSTANT_MAX}之间")

    def modify(self, roll_res: RollResult) -> RollResult:
        """
        输入的roll_res必须由形如XDY的表达式产生, 如果是常量表达式或复合表达式将会抛出一个异常
        """
        if roll_res.type is None:  # 只处理基础表达式
            raise RollDiceError(f"重骰对象只能为形如XDY的表达式, 如2D20R1, 此处为{roll_res.exp}")

        # 如果一个条件都不满足直接原样返回
        if all((not self.op(val, self.rhs) for val in roll_res.val_list)):
            roll_res.exp += self.mod + self.comp + str(self.rhs)
            return roll_res
        # 否则重新构筑
        new_val_list = []
        # 因为只有基础xdy表达式有+, 所以可以直接用+或/分割, 这样可以使嵌套Reroll和Explode显示正确
        new_info_list = []
        new_connector_list = []
        left = 0
        prev_info = roll_res.get_info()
        for right in range(len(prev_info)):
            if prev_info[right] in ["+", "|"]:
                new_info_list.append(prev_info[left:right])
                new_connector_list.append(prev_info[right])
                left = right + 1
        new_info_list.append(prev_info[left:])

        if len(new_info_list) != len(new_connector_list) + 1 or len(new_info_list) != len(roll_res.val_list):
            error_info = f"{len(new_info_list)} {len(new_connector_list)} {len(roll_res.val_list)}"
            raise RollDiceError(f"[REModReroll] 解析表达式出现错误! Error Code: 101\nInfo: {error_info}")
        for index, val in enumerate(roll_res.val_list):
            if self.op(val, self.rhs):
                if self.mod == "R":
                    new_val_list.append(roll_a_dice(roll_res.type))
                    new_info_list[index] = f"[{new_info_list[index]}->{new_val_list[-1]}]"
                elif self.mod == "XO":
                    new_val_list.append(val)
                    new_val_list.append(roll_a_dice(roll_res.type))
                    new_info_list[index] += f"|{new_val_list[-1]}"
                else:  # "x"
                    new_val_list.append(val)
                    repeat_time = 0
                    while self.op(new_val_list[-1], self.rhs) and repeat_time <= EXPLODE_LIMIT:
                        repeat_time += 1
                        add_dice = roll_a_dice(roll_res.type)
                        new_val_list.append(add_dice)
                        new_info_list[index] += f"|{add_dice}"
            else:
                new_val_list.append(val)
                new_info_list[index] = f"{new_val_list[-1]}"
        new_info = new_info_list[0]
        for i in range(len(new_connector_list)):
            new_info += new_connector_list[i] + new_info_list[i+1]
        roll_res.val_list = new_val_list
        roll_res.info = new_info
        roll_res.exp += self.mod + self.comp + str(self.rhs)
        roll_res.d20_num = 2  # 使用了这个修饰器以后无法判断d20数量, 设成2以后之后就不会用到d20_state了
        roll_res.d20_state = 0
        return roll_res


@roll_modifier("KH?[1-9][0-9]?")
class REModMax(RollExpModifier):
    """
    表示取表达式中x个最大值
    """
    def __init__(self, args: str):
        super().__init__(args)
        if args[1] == "H":
            val_str = args[2:]
        else:
            val_str = args[1:]
        self.num = int(val_str)

    def modify(self, roll_res: RollResult) -> RollResult:
        """
        注意val的顺序会按从低到高的顺序重新排序, 但info里的内容不会
        """
        if self.num == 1:
            new_info = "max"
        else:
            new_info = f"max{self.num}"

        new_info += "{" + str(roll_res.val_list)[1:-1] + "}"
        # 如果按下面这种写法就可以在嵌套时显示中间结果, 但是会影响到大成功或大失败的判断, 除非增加新的字段
        # if roll_res.type:
        #     new_info += "{" + str(roll_res.val_list)[1:-1] + "}"
        # else:  # 嵌套
        #     new_info += "{" + roll_res.info + "}"
        # roll_res.type = None

        roll_res.val_list = sorted(roll_res.val_list)[-self.num:]
        roll_res.info = new_info
        roll_res.exp = f"{roll_res.exp}K{self.num}"
        if roll_res.type == 20:
            roll_res.d20_num = len(roll_res.val_list)
            if roll_res.d20_num == 1:
                roll_res.d20_state = roll_res.val_list[0]

        return roll_res


@roll_modifier("KL[1-9][0-9]?")
class REModMin(RollExpModifier):
    """
    表示取表达式中x个最小值
    """

    def __init__(self, args: str):
        super().__init__(args)
        self.num = int(args[2:])

    def modify(self, roll_res: RollResult) -> RollResult:
        """
        注意val的顺序会按从低到高的顺序重新排序, 但info里的内容不会
        """
        if self.num == 1:
            new_info = "min"
        else:
            new_info = f"min{self.num}"
        new_info += "{" + str(roll_res.val_list)[1:-1] + "}"
        # if roll_res.type:
        #     new_info += "{" + str(roll_res.val_list)[1:-1] + "}"
        # else:
        #     new_info += "{" + roll_res.info + "}"
        # roll_res.type = None

        roll_res.val_list = sorted(roll_res.val_list)[:self.num]
        roll_res.info = new_info
        roll_res.exp = f"{roll_res.exp}KL{self.num}"
        if roll_res.type == 20:
            roll_res.d20_num = len(roll_res.val_list)
            if roll_res.d20_num == 1:
                roll_res.d20_state = roll_res.val_list[0]
        return roll_res


@roll_modifier("CS(<|>|=)?[1-9][0-9]*")
class REModCountSuccess(RollExpModifier):
    """
    表示计算当前结果中符合条件的骰值的数量
    """

    def __init__(self, args: str):
        super().__init__(args)

        args = args[2:]
        # 判定条件
        if args[0] in ("<", ">", "="):
            comp, rhs = args[0], args[1:]
        else:
            comp, rhs = "=", args

        if comp == ">":
            self.op = operator.gt
        elif comp == "<":
            self.op = operator.lt
        elif comp == "=":
            self.op = operator.eq

        self.comp: str = comp
        self.rhs: int = int(rhs)
        if self.rhs < DICE_CONSTANT_MIN or self.rhs > DICE_CONSTANT_MAX:
            raise RollDiceError(f"常量大小必须在{DICE_CONSTANT_MIN}至{DICE_CONSTANT_MAX}之间")

    def modify(self, roll_res: RollResult) -> RollResult:
        result = sum([self.op(val, self.rhs) for val in roll_res.val_list])

        roll_res.info = f"[count{self.comp}{self.rhs}" + "{" + str(roll_res.val_list)[1:-1] + "}" + f"={result}]"
        roll_res.val_list = [result]
        roll_res.exp = f"{roll_res.exp}CS{self.comp}{self.rhs}"
        roll_res.d20_num = 2  # 使用了这个修饰器以后无法判断d20数量, 设成2以后之后就不会用到d20_state了
        roll_res.d20_state = 0
        return roll_res
