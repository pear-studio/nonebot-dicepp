import abc
from typing import Type, Dict

from module.roll.result import RollResult


class RollExpConnector(metaclass=abc.ABCMeta):
    """
    用于链接掷骰表达式结果
    """
    symbol: str = None

    @abc.abstractmethod
    def connect(self, lhs: RollResult, rhs: RollResult) -> RollResult:
        """
        修改并返回掷骰数据, 返回的变量应该基于初始变量
        """
        raise NotImplementedError()


ROLL_CONNECTORS_DICT: Dict[str, Type[RollExpConnector]] = {}


def roll_connector(symbol: str):
    """
    类修饰器, 将自定义掷骰表达式连接符注册到字典中
    Args:
        symbol: 一个字符串, 唯一地匹配该连接符, 若有多个符合则较早定义的优先
    """
    def inner(cls: Type[RollExpConnector]):
        """
        Args:
            cls: 修饰的类必须继承自RollExpConnector
        Returns:
            cls: 返回修饰后的cls
        """
        assert issubclass(cls, RollExpConnector)
        assert " " not in symbol
        assert symbol not in ROLL_CONNECTORS_DICT.keys()
        cls.symbol = symbol
        ROLL_CONNECTORS_DICT[symbol] = cls
        return cls
    return inner

@roll_connector("/")
class REModDivide(RollExpConnector):
    """
    表示除法
    """
    def connect(lhs: RollResult, rhs: RollResult) -> RollResult:
        # 合并浮点情况
        lhs.float_state = (lhs.float_state or rhs.float_state)
        # 防止以下除以0的情况
        divide_by = sum(rhs.val_list)
        if lhs.float_state:
            divide_by = round(divide_by,2)
            if divide_by == 0:
                lhs.val_list = [0]
            else:
                lhs.val_list = [round(float(sum(lhs.val_list)) / float(sum(rhs.val_list)),2)]
        else:
            if divide_by == 0:
                lhs.val_list = [0]
            else:
                lhs.val_list = [int(sum(lhs.val_list) / sum(rhs.val_list))]
        lhs.info = f"{lhs.info}/{rhs.info}"
        lhs.type = None
        lhs.exp = f"{lhs.exp}/{rhs.exp}"
        lhs.dice_num += rhs.dice_num #不论如何，骰子数目累加
        lhs.d20_num += rhs.d20_num #不论如何，骰子数目累加
        lhs.d100_num += rhs.d100_num #不论如何，骰子数目累加
        #lhs.d20_state = rhs.d20_state if not lhs.d20_state else lhs.d20_state
        # 除法不继承大成功
        lhs.average_list += [ 100 - stat for stat in rhs.average_list]  # 平均值反加
        return lhs

@roll_connector("*")
class REModMultiply(RollExpConnector):
    """
    表示乘法
    """
    def connect(lhs: RollResult, rhs: RollResult) -> RollResult:
        # 合并浮点情况
        lhs.float_state = (lhs.float_state or rhs.float_state)
        lhs.val_list = [sum(lhs.val_list) * sum(rhs.val_list)]
        if lhs.float_state:
            lhs.val_list = [round(lhs.val_list[0],2)]
        lhs.info = f"{lhs.info}*{rhs.info}"
        lhs.type = None
        lhs.exp = f"{lhs.exp}*{rhs.exp}"
        lhs.dice_num += rhs.dice_num #不论如何，骰子数目累加
        lhs.d20_num += rhs.d20_num #不论如何，骰子数目累加
        lhs.d100_num += rhs.d100_num #不论如何，骰子数目累加
        #lhs.d20_state = rhs.d20_state if not lhs.d20_state else lhs.d20_state
        lhs.success += rhs.success # 大成功次数累加
        lhs.fail += rhs.fail # 大成功次数累加
        lhs.average_list += rhs.average_list # 平均值正加
        return lhs

@roll_connector("-")
class REModSubstract(RollExpConnector):
    """
    表示减法
    """
    def connect(lhs: RollResult, rhs: RollResult) -> RollResult:
        # 合并浮点情况
        lhs.float_state = (lhs.float_state or rhs.float_state)
        lhs.val_list += [v*-1 for v in rhs.val_list]
        lhs.info = f"{lhs.info}-{rhs.info}"
        lhs.type = None
        lhs.exp = f"{lhs.exp}-{rhs.exp}"
        lhs.dice_num += rhs.dice_num #不论如何，骰子数目累加
        lhs.d20_num += rhs.d20_num #不论如何，骰子数目累加
        lhs.d100_num += rhs.d100_num #不论如何，骰子数目累加
        #lhs.d20_state = rhs.d20_state if not lhs.d20_state else lhs.d20_state
        lhs.success += rhs.fail # -大成功=+大失败
        lhs.fail += rhs.success # -大失败=+大成功
        lhs.average_list += [ 100 - stat for stat in rhs.average_list]  # 平均值反加
        return lhs

@roll_connector("+")
class REModAdd(RollExpConnector):
    """
    表示加法
    """
    def connect(lhs: RollResult, rhs: RollResult) -> RollResult:
        # 合并浮点情况
        lhs.float_state = (lhs.float_state or rhs.float_state)
        lhs.val_list += rhs.val_list
        lhs.info = f"{lhs.info}+{rhs.info}"
        lhs.type = None
        lhs.exp = f"{lhs.exp}+{rhs.exp}"
        lhs.dice_num += rhs.dice_num #不论如何，骰子数目累加
        lhs.d20_num += rhs.d20_num #不论如何，骰子数目累加
        lhs.d100_num += rhs.d100_num #不论如何，骰子数目累加
        lhs.success += rhs.success # 大成功次数累加
        lhs.fail += rhs.fail # 大成功次数累加
        lhs.average_list += rhs.average_list # 平均值正加
        return lhs
