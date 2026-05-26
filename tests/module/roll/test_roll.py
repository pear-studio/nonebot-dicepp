from typing import Callable

import pytest
from module.roll import roll_config
from module.roll.expression import parse_roll_exp, exec_roll_exp, RollExpression, preprocess_roll_exp
from module.roll.roll_utils import match_outer_parentheses, remove_redundant_parentheses, RollDiceError
from module.roll.result import RollResult


@pytest.mark.unit
class MyTestCase:
    def test_utils(self):
        assert match_outer_parentheses("()") == 1
        assert match_outer_parentheses("(ABC)") == 4
        assert match_outer_parentheses("(A()A)") == 5
        assert match_outer_parentheses("(AA)))") == 3
        assert match_outer_parentheses("()ABC") == 1
        assert match_outer_parentheses("(1+2)+1") == 4
        assert match_outer_parentheses("ABC") == -1
        assert match_outer_parentheses("") == -1
        assert match_outer_parentheses("ABC()") == -1
        with pytest.raises(ValueError):
            match_outer_parentheses("(((")
        with pytest.raises(ValueError):
            match_outer_parentheses("(A(A(A))")

        assert "" == remove_redundant_parentheses("()")
        assert "ABC" == remove_redundant_parentheses("(ABC)")
        assert "ABC" == remove_redundant_parentheses("((ABC))")
        assert "1+2" == remove_redundant_parentheses("(1)+(2)")
        assert "1+2" == remove_redundant_parentheses("(1+2)")
        assert "(1+2)+2" == remove_redundant_parentheses("(1+2)+2")
        assert "(1+2)*2" == remove_redundant_parentheses("(1+2)*2")
        assert "(A+B)*C" == remove_redundant_parentheses("(A+B)*C")
        assert "(A*B)+C" == remove_redundant_parentheses("(A*B)+C")
        assert "C*(A+B)" == remove_redundant_parentheses("C*(A+B)")
        assert "C+(A*B)" == remove_redundant_parentheses("C+(A*B)")
        assert "A*((A*B)+C)" == remove_redundant_parentheses("A*((A*B)+C)")
        assert "A+((A*B)+C)" == remove_redundant_parentheses("A+((A*B)+C)")
        assert "A+((A*B)+C)" == remove_redundant_parentheses("A+((A*B)+C)")
        assert "A+(A+B)+C" == remove_redundant_parentheses("A+(A+B)+C")
        assert "A+(A*B)+C" == remove_redundant_parentheses("A+(A*B)+C")
        assert "A+(A+B)*C" == remove_redundant_parentheses("A+(A+B)*C")
        assert "A*(A+B)+C" == remove_redundant_parentheses("A*(A+B)+C")
        assert "max{max2{+(5+14+5+12)}}" == remove_redundant_parentheses("max{max2{+(5+14+5+12)}}")

    def __show_exec_res(self, exp_str: str, checker: Callable[[str], bool] = None):
        for _ in range(100):
            exec_roll_exp(exp_str)
        res = exec_roll_exp(exp_str)
        assert res is not None
        output = f"Values: {res.val_list} \tInfo: {res.get_info()} \tType: {res.type} \tExpression: {res.get_exp()}"
        output += f"\nFinal Output: \033[0;33m{res.get_complete_result()}"
        output = f"Origin Exp: \033[0;32m{exp_str} \033[0m\t{output}"

        if checker:
            assert checker(res.get_complete_result()), output
        else:
            print("\t\t--- Check Result ---")
            print(output)

    def __show_exception(self, exp_str: str):
        with pytest.raises(RollDiceError):
            exec_roll_exp(exp_str)
        try:
            exec_roll_exp(exp_str)
        except RollDiceError as e:
            print("\t\t--- Check Exception ---")
            print(f"Origin Exp: \033[0;35m{exp_str} \t\033[0;33m{e.info}")

    def test_basic_roll(self):
        import re
        def extract_val(s: str) -> int:
            match = re.search(r'\[(-?\d+)\]', s)
            if match:
                val = int(match.group(1))
                if s.startswith("-"):
                    return -abs(val)
                return val
            parts = s.split("=")
            return int(parts[-1]) if parts else 0

        self.__show_exec_res("1D20", checker=lambda s: "1D20" in s and 1 <= extract_val(s) <= 20)
        self.__show_exec_res("D", checker=lambda s: "1D20" in s and 1 <= extract_val(s) <= 20)
        self.__show_exec_res("1D", checker=lambda s: "1D20" in s and 1 <= extract_val(s) <= 20)
        self.__show_exec_res("1D4", checker=lambda s: "1D4" in s and 1 <= extract_val(s) <= 4)
        self.__show_exec_res("1", checker=lambda s: "1" == s)
        self.__show_exec_res("+1D20", checker=lambda s: "1D20" in s and 1 <= extract_val(s) <= 20)
        self.__show_exec_res("-1D20", checker=lambda s: "-1D20" in s and extract_val(s) < 0)

        self.__show_exec_res("1-1-1", checker=lambda s: s.endswith("-1"))
        self.__show_exec_res("1+1-1", checker=lambda s: s.endswith("1"))
        self.__show_exec_res("1-1+1", checker=lambda s: s.endswith("1"))
        self.__show_exec_res("5/2+3/2", checker=lambda s: s.endswith("3"))
        self.__show_exec_res("1+2*2", checker=lambda s: s.endswith("5"))
        self.__show_exec_res("1*2+2", checker=lambda s: s.endswith("4"))
        self.__show_exec_res("1-1+1-1", checker=lambda s: s.endswith("0"))
        self.__show_exec_res("1d20", checker=lambda s: "1D20" in s and 1 <= extract_val(s) <= 20)

        # 基础运算错误
        self.__show_exception("1D(20)")
        self.__show_exception("(1)D20")
        self.__show_exception("1(D)20")
        self.__show_exception("(D20)+(1")
        self.__show_exception("((D20)+1))))")
        self.__show_exception("(10D20+5)cs>10")

        # 边界条件
        self.__show_exception(f"1D{roll_config.DICE_TYPE_MAX + 1}")
        self.__show_exception(f"{roll_config.DICE_NUM_MAX + 1}D20")
        self.__show_exception(f"{roll_config.DICE_CONSTANT_MIN - 1}")
        self.__show_exception(f"{roll_config.DICE_CONSTANT_MAX + 1}")
