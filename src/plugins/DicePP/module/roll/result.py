from typing import List, Optional, Union

#from .roll_utils import remove_redundant_parentheses


class RollResult:
    """
    记录掷骰过程中的一些数据, 可以被视为一个结构体
    """
    def __init__(self):
        self.val_list: List[int] = []  # 结果值列表
        self.info: str = ""  # 代表结果的字符串
        self.type: int = Optional[None]  # 表示骰子的面数, 仅对基础的XDY类型表达式产生的结果有效, 如果是非基础表达式, 则type为None
        self.exp: str = ""  # 代表表达式的字符串
        # 特定骰子数量，注意3D20K1只算1个D20, 而D20+D20算两个D20
        self.dice_num: int = 0  # 表示表达式包含多少个骰子（任意）
        self.d20_num: int = 0  # 表示表达式包含多少个D20
        self.d100_num: int = 0  # 表示表达式包含多少个D100
        self.average_list: List[int] = [] # 代表骰子平均出目
        self.success: int = 0  # 大成功，不论是何种骰
        self.fail: int = 0  # 大失败，不论是何种骰
        self.float_state: bool = False  # 是否使用float的运算方式
    
    def success_or_fail(self,success_val: int,fail_val: int):
        """
        处理大成功与大失败
        """
        self.success = 0
        self.fail = 0
        for val in self.val_list:
            if val == success_val:
                self.success += 1
            elif val == fail_val:
                self.fail += 1

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
        final_info = self.info[1:] if self.info.startswith("+") else self.info
        #final_info = remove_redundant_parentheses(final_info, readable=False)
        #if final_info.startswith("(") and final_info.endswith(")"):
        #    final_info = final_info[1:-1]
        return final_info

    def get_exp(self) -> str:
        """
        获得形如D20+1的掷骰表达式
        """
        final_exp = self.exp[1:] if self.exp.startswith("+") else self.exp
        #final_exp = remove_redundant_parentheses(final_exp, readable=False)
        #if final_exp.startswith("(") and final_exp.endswith(")"):
        #    final_exp = final_exp[1:-1]
        return final_exp

    def get_val(self) -> Union[int,float]:
        """
        获得掷骰结果数值
        """
        if self.float_state:
            return round(sum(self.val_list),2)
        else:
            return int(sum(self.val_list))

    def get_val_str(self) -> str:
        """
        获得掷骰结果数值
        """
        val_str = str(self.get_val())
        if self.float_state:
            if "." in val_str:
                val_strs = val_str.split(".")
                if len(val_strs[1]) < 2:
                    val_strs[1] += "0" * (2 - len(val_strs[1]))
                if len(val_strs[1]) > 2:
                    val_strs[1] = val_strs[1][:2]
                return ".".join(val_strs)
            else:
                return val_str + ".00"
        else:
            return val_str

    def get_styled_dice_info(self) -> str:
        """
        获得风格化的掷骰结果数值文本
        """
        txt = ""
        for val in self.val_list:
            txt += "[" + str(val) + "]"
        return txt

    def get_complete_result(self) -> str:
        """
        获得形如 2D20*2=(1+1)*2=4的字符串, 不包括掷骰表达式
        """
        exp = self.get_exp()
        info = self.get_info()
        val = self.get_val_str()
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
