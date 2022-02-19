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


@roll_connector("+")
class REModAdd(RollExpConnector):
    """
    表示加法
    """
    def connect(self, lhs: RollResult, rhs: RollResult) -> RollResult:
        lhs.val_list += rhs.val_list
        lhs.info = f"{lhs.info}+{rhs.info}"
        lhs.type = None
        lhs.exp = f"{lhs.exp}+{rhs.exp}"
        lhs.d20_num += rhs.d20_num
        lhs.d20_state = rhs.d20_state if not lhs.d20_state else lhs.d20_state
        return lhs


@roll_connector("-")
class REModSubstract(RollExpConnector):
    """
    表示减法
    """
    def connect(self, lhs: RollResult, rhs: RollResult) -> RollResult:
        lhs.val_list += [v*-1 for v in rhs.val_list]
        lhs.info = f"{lhs.info}-{rhs.info}"
        lhs.type = None
        lhs.exp = f"{lhs.exp}-{rhs.exp}"
        lhs.d20_num += rhs.d20_num
        lhs.d20_state = rhs.d20_state if not lhs.d20_state else lhs.d20_state
        return lhs


@roll_connector("*")
class REModMultiply(RollExpConnector):
    """
    表示乘法
    """
    def connect(self, lhs: RollResult, rhs: RollResult) -> RollResult:
        lhs.val_list = [sum(lhs.val_list) * sum(rhs.val_list)]
        lhs.info = f"{lhs.info}*{rhs.info}"
        lhs.type = None
        lhs.exp = f"{lhs.exp}*{rhs.exp}"
        lhs.d20_num += rhs.d20_num
        lhs.d20_state = rhs.d20_state if not lhs.d20_state else lhs.d20_state
        return lhs


@roll_connector("/")
class REModDivide(RollExpConnector):
    """
    表示除法
    """
    def connect(self, lhs: RollResult, rhs: RollResult) -> RollResult:
        lhs.val_list = [int(sum(lhs.val_list) / sum(rhs.val_list))]
        lhs.info = f"{lhs.info}/{rhs.info}"
        lhs.type = None
        lhs.exp = f"{lhs.exp}/{rhs.exp}"
        lhs.d20_num += rhs.d20_num
        lhs.d20_state = rhs.d20_state if not lhs.d20_state else lhs.d20_state
        return lhs
