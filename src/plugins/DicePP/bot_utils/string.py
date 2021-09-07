from typing import List, Iterable


def to_english_str(input_str: str) -> str:
    """
    将字符串中的中文符号转为英文
    """
    if type(input_str) != str:
        raise ValueError(f'ChineseToEnglishSymbol: Input {input_str} must be str type')
    output_str = input_str
    output_str = output_str.replace('。', '.')
    output_str = output_str.replace('，', ',')
    output_str = output_str.replace('＋', '+')
    output_str = output_str.replace('➕', '+')
    output_str = output_str.replace('－', '-')
    output_str = output_str.replace('➖', '-')
    output_str = output_str.replace('＝', '=')
    output_str = output_str.replace('＃', '#')
    output_str = output_str.replace('：', ':')
    output_str = output_str.replace('；', ';')
    output_str = output_str.replace('（', '(')
    output_str = output_str.replace('）', ')')
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
