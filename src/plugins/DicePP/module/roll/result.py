from typing import List, Optional

from .roll_utils import remove_redundant_parentheses


class RollResult:
    """
    记录掷骰过程中的一些数据, 可以被视为一个结构体
    """
    def __init__(self):
        self.val_list: List[int] = []  # 结果值列表
        self.info: str = ""  # 代表结果的字符串
        self.type: int = Optional[None]  # 表示骰子的面数, 仅对基础的XDY类型表达式产生的结果有效, 如果是非基础表达式, 则type为None
        self.exp: str = ""  # 代表表达式的字符串
        self.d20_num: int = 0  # 表示这个结果对应的表达式包含多少个D20, 注意3D20K1只算1个D20, 而D20+D20算两个D20
        self.d20_state: int = 0  # 若只包含一颗d20, 表示这颗d20的骰值, 若d20_num != 1, 该属性无效

    def get_result(self) -> str:
        """
        获得形如 (1+1)*2=4的字符串, 不包括掷骰表达式
        """
        inter_info = self.get_info()
        final_res = str(self.get_val())
        if inter_info == final_res:
            return final_res
        else:
            return f"{inter_info}={final_res}"

    def get_info(self) -> str:
        """
        获得形如 (1+1)*2 的中间变量
        """
        final_info = self.info if self.info[0] != "+" else self.info[1:]
        final_info = remove_redundant_parentheses(final_info)
        return final_info

    def get_exp(self) -> str:
        """
        获得形如D20+1的掷骰表达式
        """
        final_exp = self.exp if self.exp[0] != "+" else self.exp[1:]
        final_exp = remove_redundant_parentheses(final_exp, readable=False)
        return final_exp

    def get_val(self) -> int:
        """
        获得掷骰结果数值
        """
        return sum(self.val_list)

    def get_complete_result(self) -> str:
        """
        获得形如 2D20*2=(1+1)*2=4的字符串, 不包括掷骰表达式
        """
        exp = self.get_exp()
        info = self.get_info()
        val = str(self.get_val())
        res = exp
        if res != info:
            res += f"={info}"
        if res != val and info != val:
            res += f"={val}"
        return res

    def get_exp_val(self) -> str:
        exp = self.get_exp()
        val = str(self.get_val())

        if exp != val:
            return f"{exp}={val}"
        else:
            return val
