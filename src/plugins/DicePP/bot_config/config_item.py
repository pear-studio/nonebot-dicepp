import copy
from typing import List


class ConfigItem:
    def __init__(self, key: str, default_content: str = "", comment: str = ""):
        self.key = key
        self.contents: list = [default_content] if default_content else []
        self.comment = comment

    def add(self, text: str) -> None:
        """
        为当前配置增加一个参数, 调用get可以返回所有参数
        """
        self.contents.append(text)

    def get(self) -> List[str]:
        """
        返回一个可选择的本地化字符串, 若没有可用的本地化字符串, 返回空字符串
        """
        return copy.copy(self.contents)
