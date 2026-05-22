"""查询工具函数 — 供 QueryCommand 和 persona 工具共享。

契约:
- command_split("") / command_split("  ") → 返回 []；command_split("# / &") → 返回 ["/"]（`/` 非特殊前缀，作为普通 token 收集）
- 此模块为独立函数集合，零依赖。
修改时需同时通过 tests/module/query/ 和 persona 工具测试验证。
"""
from typing import List


def command_split(keywords: str) -> List[str]:
    """将用户输入的查询文本解析为 token 列表。

    支持:
    - 空格分隔的关键词
    - # 前缀的标签/来源搜索
    - & 前缀的分类搜索
    - 双引号精确匹配
    - / 分隔的 OR 关键词
    """
    result_list: List[str] = []
    collect_words: str = ""
    prefix: str = ""
    fine_mode: bool = False
    for key in keywords:
        if not fine_mode and collect_words == "" and key == "\"":
            fine_mode = True
        elif fine_mode and key == "\"":
            fine_mode = False
            if collect_words != "":
                result_list.append(prefix + collect_words)
                prefix = ""
        elif not fine_mode and key in ["#", "&"]:
            if collect_words.strip():
                result_list.append(prefix + collect_words.strip())
            collect_words = ""
            prefix = key
        elif not fine_mode and key in [" "]:
            if collect_words.strip():
                result_list.append(prefix + collect_words.strip())
            collect_words = ""
            prefix = ""
        else:
            collect_words += key
    if fine_mode:
        result_list.append(prefix + collect_words)
    elif collect_words.strip():
        result_list.append(prefix + collect_words.strip())
    return result_list
