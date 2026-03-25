DPP_COMMAND_PRIORITY_DEFAULT = 1 << 10  # 默认优先级
DPP_COMMAND_PRIORITY_USUAL_LOWER_BOUND = -(1 << 10)  # 能被.bot off屏蔽掉的指令都要在这之后响应
DPP_COMMAND_PRIORITY_MASTER = 1 << 11  # Master指令优先级
DPP_COMMAND_PRIORITY_TRIVIAL = 1 << 12  # 次要指令优先级

DPP_COMMAND_FLAG_DEFAULT = 0  # 命令所属的标志位
DPP_COMMAND_FLAG_ROLL = 1 << 1  # 掷骰指令
DPP_COMMAND_FLAG_DND = 1 << 2  # DND相关指令
DPP_COMMAND_FLAG_CHAR = 1 << 3  # 角色卡相关指令
DPP_COMMAND_FLAG_QUERY = 1 << 4  # 查询相关指令
DPP_COMMAND_FLAG_DRAW = 1 << 5  # 抽卡相关指令
DPP_COMMAND_FLAG_FUN = 1 << 6  # 娱乐相关指令
DPP_COMMAND_FLAG_MANAGE = 1 << 7  # 管理相关指令
DPP_COMMAND_FLAG_CHAT = 1 << 8  # 自定义聊天指令
DPP_COMMAND_FLAG_HELP = 1 << 9  # 帮助指令
DPP_COMMAND_FLAG_MACRO = 1 << 10  # 宏指令
DPP_COMMAND_FLAG_INFO = 1 << 11  # 查看用户信息相关指令
DPP_COMMAND_FLAG_HUB = 1 << 12  # Hub相关指令
DPP_COMMAND_FLAG_BATTLE = 1 << 13  # 战斗相关指令

DPP_COMMAND_FLAG_SET_STD = DPP_COMMAND_FLAG_ROLL | DPP_COMMAND_FLAG_DND | DPP_COMMAND_FLAG_CHAR | DPP_COMMAND_FLAG_QUERY | DPP_COMMAND_FLAG_BATTLE
DPP_COMMAND_FLAG_SET_EXT_0 = DPP_COMMAND_FLAG_FUN | DPP_COMMAND_FLAG_CHAT
DPP_COMMAND_FLAG_SET_HIDE_IN_STAT = DPP_COMMAND_FLAG_HUB | DPP_COMMAND_FLAG_MANAGE | DPP_COMMAND_FLAG_CHAT | DPP_COMMAND_FLAG_INFO

DPP_COMMAND_FLAG_DICT = {
    DPP_COMMAND_FLAG_ROLL: "掷骰",
    DPP_COMMAND_FLAG_DND: "DND",
    DPP_COMMAND_FLAG_BATTLE: "战斗",
    DPP_COMMAND_FLAG_CHAR: "角色卡",
    DPP_COMMAND_FLAG_QUERY: "查询",
    DPP_COMMAND_FLAG_DRAW: "抽卡",
    DPP_COMMAND_FLAG_FUN: "娱乐",
    DPP_COMMAND_FLAG_MANAGE: "管理",
    DPP_COMMAND_FLAG_CHAT: "聊天",
    DPP_COMMAND_FLAG_HELP: "帮助",
    DPP_COMMAND_FLAG_MACRO: "宏",
    DPP_COMMAND_FLAG_INFO: "用户信息",
    DPP_COMMAND_FLAG_HUB: "Hub",
}

DPP_COMMAND_CLUSTER_DEFAULT = 0  # 命令所属的功能群
DPP_COMMAND_CLUSTER_DICT = {
    DPP_COMMAND_CLUSTER_DEFAULT: "Default",
}

# ---------------------------------------------------------------------------
# 全局共享 Flag 命名表（Task 1.3）
# 仅收录跨命令具有相同含义且可互换的 flags/options。
# 命令私有 flags 不在此列，由各命令适配层在注册时自行声明。
# 判定标准：若同一 flag 在两个或更多命令中含义相同且可互换，则纳入此表。
# ---------------------------------------------------------------------------
GLOBAL_FLAG_QUIET = "q"         # 安静模式，减少回显输出（长参数 --quiet）
GLOBAL_FLAG_HELP = "help"       # 输出帮助（长参数 --help）
GLOBAL_KWARG_GROUP = "group"    # 强制群维度操作（长参数 --group=<group_id>）

# ---------------------------------------------------------------------------
# 解析错误码常量（Task 4.1）
# 供 CommandTextParser 和命令适配层使用，统一错误分类
# ---------------------------------------------------------------------------

# 前缀/格式错误
PARSE_ERR_PREFIX_MISMATCH = "PREFIX_MISMATCH"       # 前缀不匹配
PARSE_ERR_UNKNOWN_FLAG = "UNKNOWN_FLAG"              # 未识别的 flag
PARSE_ERR_INVALID_ARG_TYPE = "INVALID_ARG_TYPE"     # 参数类型非法（如期望数字但输入文字）
PARSE_ERR_ARG_OUT_OF_RANGE = "ARG_OUT_OF_RANGE"     # 参数超出允许范围
PARSE_ERR_TOO_MANY_ARGS = "TOO_MANY_ARGS"           # 参数过多
PARSE_ERR_MISSING_REQUIRED_ARG = "MISSING_REQUIRED_ARG"  # 缺少必需参数
PARSE_WARN_KWARG_MISSING_VALUE = "KWARG_MISSING_VALUE"   # 键值参数缺少值（可恢复）
PARSE_WARN_COMPAT_CONFLICT = "COMPAT_CONFLICT"           # 兼容映射产生冲突（可恢复）
PARSE_WARN_COMPAT_RULE_ERROR = "COMPAT_RULE_ERROR"       # 兼容规则执行异常（可恢复）

GLOBAL_FLAG_TABLE = {
    GLOBAL_FLAG_QUIET:  {"long": "--quiet",  "desc": "安静模式，减少回显输出"},
    GLOBAL_FLAG_HELP:   {"long": "--help",   "desc": "输出命令帮助"},
}

GLOBAL_KWARG_TABLE = {
    GLOBAL_KWARG_GROUP: {"long": "--group",  "desc": "强制群维度操作，值为群 ID"},
}
