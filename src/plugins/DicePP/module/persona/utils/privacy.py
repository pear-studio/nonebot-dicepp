"""隐私/敏感数据处理工具函数"""


def mask_sensitive_string(value: str, prefix_len: int = 3, suffix_len: int = 3) -> str:
    """脱敏显示敏感字符串

    Args:
        value: 原始字符串
        prefix_len: 前缀保留字符数
        suffix_len: 后缀保留字符数

    Returns:
        脱敏后的字符串，如 "sk-***456"
    """
    if not value or len(value) < prefix_len + suffix_len + 1:
        return "未设置"
    masked_len = len(value) - prefix_len - suffix_len
    return f"{value[:prefix_len]}{'*' * masked_len}{value[-suffix_len:]}"
