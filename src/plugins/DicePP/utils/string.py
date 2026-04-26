from typing import List, Iterable


def estimate_tokens(text: str) -> float:
    """基于字符统计的 token 估算策略，为性能考虑不引入真实 tokenizer。
    中文字符按 1 token，其余按每 4 字符 1 token。
    所有调用方均通过本函数估算，调整策略时只需修改此处即可。
    """
    cn_chars = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other_chars = len(text) - cn_chars
    return cn_chars + other_chars / 4


def to_english_str(input_str: str) -> str:
    """
    将字符串中的中文符号与全角字符转为英文
    """
    if type(input_str) != str:
        raise ValueError(f'ChineseToEnglishSymbol: Input {input_str} must be str type')
    output_str = ""
    for character in input_str:
        code: int = ord(character)
        if code == 12288: # 全角空格 变 普通空格
            code = 32
        elif code == 12290: # 中文句号 变 英文句号
            code = 46
        elif code >= 65281 and code <= 65374: # 剩下的全角火星文全部位移回半角
            code -= 65248
        output_str += chr(code)
    """
    。，＋－＝＃：；（）ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｙｗｘｙｚ等
    """
    return output_str


def match_substring(substring: str, str_list: Iterable[str]) -> List[str]:
    """
    在一个字符串列表中找到所有包含输入字符串的字符串并返回
    Args:
        substring: 目标字符串
        str_list: 待匹配字符串列表

    Returns:
        res_list: 所有匹配成功的字符串
    """
    return [s for s in str_list if s.find(substring) != -1]
