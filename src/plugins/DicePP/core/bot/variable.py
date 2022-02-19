import json
from typing import Union
import re

from core.data import JsonObject, custom_json_object

VAR_DEP_PATTERN: re.Pattern = re.compile(r"%(.+)%")


@custom_json_object
class BotVariable(JsonObject):
    """
    用户自定义的变量, 代表一个数字, 可以依赖其他变量
    """
    def serialize(self) -> str:
        json_dict = {"name": self.name, "val": self.val}
        return json.dumps(json_dict)

    def deserialize(self, json_str: str) -> None:
        json_dict = json.loads(json_str)
        self.initialize(json_dict["name"], json_dict["val"])

    def __init__(self):
        self.name = "VAR"
        self.val: Union[int, str] = 0
        self.dep = []
        self.is_num: bool = True

    def initialize(self, name: str, val: Union[int, str]):
        self.name = name
        self.val = val
        if isinstance(val, int):
            return
        # 是字符串, 要依赖其他变量
        assert isinstance(val, str)
        self.is_num = False

        def handle_dep(match):
            self.dep.append(match.group(1))
            return match.group(0)
        self.val = VAR_DEP_PATTERN.sub(handle_dep, val)

    def __repr__(self):
        return f"Var({self.name} = {self.val})"
