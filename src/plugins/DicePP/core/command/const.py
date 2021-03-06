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
