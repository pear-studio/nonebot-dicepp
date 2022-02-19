import html

from utils.string import to_english_str


def preprocess_msg(msg_str: str) -> str:
    """
    预处理消息字符串
    """
    msg_str = to_english_str(msg_str)  # 转换中文标点
    msg_str = msg_str.lower().strip()  # 转换小写, 去掉前后空格
    msg_str = html.unescape(msg_str)   # html实体转义: &#36; -> $
    return msg_str
