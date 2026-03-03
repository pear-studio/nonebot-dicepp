import unittest
from typing import Callable

import pytest
from module.roll import roll_config
from module.roll.expression import parse_roll_exp, exec_roll_exp, RollExpression, preprocess_roll_exp
from module.roll.roll_utils import match_outer_parentheses, remove_redundant_parentheses, RollDiceError
from module.roll.result import RollResult


@pytest.mark.unit
class MyTestCase(unittest.TestCase):
    def test_utils(self):
        self.assertEqual(match_outer_parentheses("()"), 1)
        self.assertEqual(match_outer_parentheses("(ABC)"), 4)
        self.assertEqual(match_outer_parentheses("(A()A)"), 5)
        self.assertEqual(match_outer_parentheses("(AA)))"), 3)
        self.assertEqual(match_outer_parentheses("()ABC"), 1)
        self.assertEqual(match_outer_parentheses("(1+2)+1"), 4)
        self.assertEqual(match_outer_parentheses("ABC"), -1)
        self.assertEqual(match_outer_parentheses(""), -1)
        self.assertEqual(match_outer_parentheses("ABC()"), -1)
        self.assertRaises(ValueError, match_outer_parentheses, "(((")
        self.assertRaises(ValueError, match_outer_parentheses, "(A(A(A))")

        self.assertEqual("", remove_redundant_parentheses("()"))
        self.assertEqual("ABC", remove_redundant_parentheses("(ABC)"))
        self.assertEqual("ABC", remove_redundant_parentheses("((ABC))"))
        self.assertEqual("1+2", remove_redundant_parentheses("(1)+(2)"))
        self.assertEqual("1+2", remove_redundant_parentheses("(1+2)"))
        self.assertEqual("(1+2)+2", remove_redundant_parentheses("(1+2)+2"))
        self.assertEqual("(1+2)*2", remove_redundant_parentheses("(1+2)*2"))
        self.assertEqual("(A+B)*C", remove_redundant_parentheses("(A+B)*C"))
        self.assertEqual("(A*B)+C", remove_redundant_parentheses("(A*B)+C"))
        self.assertEqual("C*(A+B)", remove_redundant_parentheses("C*(A+B)"))
        self.assertEqual("C+(A*B)", remove_redundant_parentheses("C+(A*B)"))
        self.assertEqual("A*((A*B)+C)", remove_redundant_parentheses("A*((A*B)+C)"))
        self.assertEqual("A+((A*B)+C)", remove_redundant_parentheses("A+((A*B)+C)"))
        self.assertEqual("A+((A*B)+C)", remove_redundant_parentheses("A+((A*B)+C)"))
        self.assertEqual("A+(A+B)+C", remove_redundant_parentheses("A+(A+B)+C"))
        self.assertEqual("A+(A*B)+C", remove_redundant_parentheses("A+(A*B)+C"))
        self.assertEqual("A+(A+B)*C", remove_redundant_parentheses("A+(A+B)*C"))
        self.assertEqual("A*(A+B)+C", remove_redundant_parentheses("A*(A+B)+C"))
        self.assertEqual("max{max2{+(5+14+5+12)}}", remove_redundant_parentheses("max{max2{+(5+14+5+12)}}"))

    def __show_exec_res(self, exp_str: str, checker: Callable[[str], bool] = None):
        for _ in range(100):
            exec_roll_exp(exp_str)
        res = exec_roll_exp(exp_str)
        self.assertIsNotNone(res)
        output = f"Values: {res.val_list} \tInfo: {res.get_info()} \tType: {res.type} \tExpression: {res.get_exp()}"
        output += f"\nFinal Output: \033[0;33m{res.get_complete_result()}"
        output = f"Origin Exp: \033[0;32m{exp_str} \033[0m\t{output}"

        if checker:
            self.assertTrue(checker(res.get_complete_result()), output)
        else:
            print("\t\t--- Check Result ---")
            print(output)

    def __show_exception(self, exp_str: str):
        self.assertRaises(RollDiceError, exec_roll_exp, exp_str)
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
        self.__show_exec_res("3D20")
        self.__show_exec_res("D", checker=lambda s: "1D20" in s and 1 <= extract_val(s) <= 20)
        self.__show_exec_res("1D", checker=lambda s: "1D20" in s and 1 <= extract_val(s) <= 20)
        self.__show_exec_res("1D4", checker=lambda s: "1D4" in s and 1 <= extract_val(s) <= 4)
        self.__show_exec_res("1", checker=lambda s: "1" == s)
        self.__show_exec_res("+1D20", checker=lambda s: "1D20" in s and 1 <= extract_val(s) <= 20)
        self.__show_exec_res("-1D20", checker=lambda s: "-1D20" in s and extract_val(s) < 0)

        self.__show_exec_res("1D20+1")
        self.__show_exec_res("1D20-1")
        self.__show_exec_res("3D20*2")
        self.__show_exec_res("3D20/2")
        self.__show_exec_res("1+1D20")
        self.__show_exec_res("1-1D20")
        self.__show_exec_res("2*3D20")
        self.__show_exec_res("2/3D20")

        self.__show_exec_res("1-1-1", checker=lambda s: s.endswith("-1"))
        self.__show_exec_res("1+1-1", checker=lambda s: s.endswith("1"))
        self.__show_exec_res("1-1+1", checker=lambda s: s.endswith("1"))
        self.__show_exec_res("5/2+3/2", checker=lambda s: s.endswith("3"))
        self.__show_exec_res("1+2*2", checker=lambda s: s.endswith("5"))
        self.__show_exec_res("1*2+2", checker=lambda s: s.endswith("4"))
        self.__show_exec_res("1-1+1-1", checker=lambda s: s.endswith("0"))
        self.__show_exec_res("1+1-1+1")

        self.__show_exec_res("1d20", checker=lambda s: "1D20" in s and 1 <= extract_val(s) <= 20)
        self.__show_exec_res("d20＋1")

        self.__show_exec_res("2D20k1")
        self.__show_exec_res("1D20K2")
        self.__show_exec_res("4D20k2kl1")
        self.__show_exec_res("4D20r<10")
        self.__show_exec_res("4D20x<10")
        self.__show_exec_res("4D20xo<10")
        self.__show_exec_res("4D20r<10x>10")
        self.__show_exec_res("4D20x>10r<10")
        self.__show_exec_res("D20cs>5")
        self.__show_exec_res("10D20cs>10")
        self.__show_exec_res("5+10D20cs>10+5")
        self.__show_exec_res("10D20kl5cs>10")

        # 带括号
        self.__show_exec_res("(1+2)")
        self.__show_exec_res("(1+2)*2")
        self.__show_exec_res("(D20)*2")
        self.__show_exec_res("(1+D20)*2")
        self.__show_exec_res("((1+D20))*2")
        self.__show_exec_res("(D20)")
        self.__show_exec_res("(D20)*(D20)")

        # 优势与劣势
        self.__show_exec_res("D20优势")
        self.__show_exec_res("D20劣势+1")
        self.__show_exec_res("D20劣势+1+D优势")

        # 抗性与易伤
        self.__show_exec_res("D20+2抗性")
        self.__show_exec_res("5抗性")
        self.__show_exec_res("2D4+D20易伤")

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

    def test_d20_state(self):
        self.__show_exec_res("1D20")
        self.__show_exec_res("2D20KL1")
        self.__show_exec_res("2D20K1")
        self.__show_exec_res("1D20K3")
        self.__show_exec_res("1D4+1D20+20")
        self.__show_exec_res("2D20")
        self.__show_exec_res("4D20K3")
        self.__show_exec_res("D20+D20")


if __name__ == '__main__':
    unittest.main()
