import random


class LocalizationText:
    def __init__(self, key: str, default_text: str = "", comment: str = ""):
        self.key = key
        self.loc_texts: list = [default_text] if default_text else []
        self.comment = comment

    def add(self, text: str) -> None:
        """
        增加一个可选择的本地化字符串, 调用get可以随机返回一个可选择的本地化字符串
        """
        self.loc_texts.append(text)

    def get(self) -> str:
        """
        返回一个可选择的本地化字符串, 若没有可用的本地化字符串, 返回空字符串
        """
        return random.choice(self.loc_texts) if self.loc_texts else ""
