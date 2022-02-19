import json
from typing import Dict, List
import re

from core.data import JsonObject, custom_json_object

MACRO_COMMAND_SPLIT = "%%"
MACRO_PARSE_LIMIT = 500  # 宏展开以后的长度限制


@custom_json_object
class BotMacro(JsonObject):
    """
    用户自定义的宏, 相当于字符串替换
    宏的定义方法:
        [关键字][参数列表, 形如(参数1,参数2,...), 可选][空格][目标字符串]
        目标字符串中与参数同名的字符串将在使用宏时被替换为给定的参数
        在定义时给定参数就必须在使用时给出, 否则不会被认定为宏
        用{MACRO_COMMAND_SPLIT}来表示指令分隔符, {MACRO_COMMAND_SPLIT}左右的空格和换行将会被忽略
        注意:
            第一个空格的位置非常关键, 用来区分替换前的内容和替换后的内容
            参数名字不要重名, 宏可以嵌套, 但不会处理递归(即不可重入), 先定义的宏会先处理
        示例:
        一颗D20 .rd20
        掷骰两次(表达式,原因) .r 表达式 原因 {MACRO_COMMAND_SPLIT} .r 表达式 原因
    宏的使用方法:
        [关键字][用:分隔给定参数]
        输入: 一颗D20 这是一颗d20  ->  等同于:  .rd20 这是一颗d20
        输入: 掷骰两次:d20+2:某种原因  -> 等同于: 执行指令.r d20+2 某种原因 + 执行指令.r d20+2 某种原因
    """
    def serialize(self) -> str:
        json_dict = {"raw": self.raw, "split": self.command_split}
        return json.dumps(json_dict)

    def deserialize(self, json_str: str) -> None:
        json_dict = json.loads(json_str)
        self.initialize(json_dict["raw"], json_dict["split"])

    def __init__(self):
        self.raw: str = ""  # 定义宏时的字符串
        self.key: str = ""  # 宏的关键字
        self.args: List[str] = []  # 宏的参数, 为空则不需要参数
        self.target: str = ""  # 将宏关键字替换为的对象
        self.command_split: str = ""
        self.pattern: re.Pattern = re.compile("")

    def initialize(self, raw: str, command_split: str):
        self.raw = raw  # 定义宏时的字符串
        self.command_split = command_split
        # 解析定义字符串
        if self.raw.find(" ") == -1:
            raise ValueError("宏定义中缺少空格")
        key_args, target = self.raw.split(" ", 1)
        key_args: str = key_args.strip()
        target: str = target.strip()
        if key_args.endswith(")"):
            par_index = key_args.find("(")
            if par_index == -1:
                raise ValueError("参数列表缺少左括号!")
            self.key, self.args = key_args[:par_index], key_args[par_index+1:-1].split(",")
        else:
            self.key, self.args = key_args, []

        for arg in self.args:
            target = target.replace(arg, "{"+arg+"}")
        target = target.replace(MACRO_COMMAND_SPLIT, self.command_split)
        re_pattern = ":".join([self.key] + ["(.*)"]*len(self.args))
        self.pattern = re.compile(re_pattern)
        self.target = target

    def process(self, input_str: str):
        def handle_macro(match):
            res = self.target
            if self.args:
                kwargs: Dict[str, str] = {}
                for i in range(len(self.args)):
                    kwargs[self.args[i]] = match.group(i+1)
                res = res.format(**kwargs)
            return res

        return self.pattern.sub(handle_macro, input_str)

    def __repr__(self):
        return f"Macro({self.key}, Args:{self.args} -> {self.target})"
