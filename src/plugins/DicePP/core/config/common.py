from typing import Dict, List

from core.config.declare import BOT_AGREEMENT

DEFAULT_CONFIG: Dict[str, str] = {}
DEFAULT_CONFIG_COMMENT: Dict[str, str] = {}

# 默认配置
CFG_MASTER = "master"
DEFAULT_CONFIG[CFG_MASTER] = ""
DEFAULT_CONFIG_COMMENT[CFG_MASTER] = "Master账号, 权限最高, 可以有多个Master"

CFG_ADMIN = "admin"
DEFAULT_CONFIG[CFG_ADMIN] = ""
DEFAULT_CONFIG_COMMENT[CFG_ADMIN] = "管理员账号, 拥有次高权限, 可以有多个管理员"

CFG_FRIEND_TOKEN = "friend_token"
DEFAULT_CONFIG[CFG_FRIEND_TOKEN] = ""
DEFAULT_CONFIG_COMMENT[CFG_FRIEND_TOKEN] = "用户申请好友时在验证中输入参数中的文本之一骰娘才会通过, 若字符串为空则通过所有的好友验证"

CFG_GROUP_INVITE = "group_invite"
DEFAULT_CONFIG[CFG_GROUP_INVITE] = "1"
DEFAULT_CONFIG_COMMENT[CFG_GROUP_INVITE] = "好友邀请加群时是否同意, 0为总是拒绝, 1为总是同意"

CFG_AGREEMENT = "agreement"
DEFAULT_CONFIG[CFG_AGREEMENT] = BOT_AGREEMENT
DEFAULT_CONFIG_COMMENT[CFG_AGREEMENT] = "使用协议"

CFG_COMMAND_SPLIT = "command_split"  # \\ 来分割多条指令
DEFAULT_CONFIG[CFG_COMMAND_SPLIT] = "\\\\"
DEFAULT_CONFIG_COMMENT[CFG_COMMAND_SPLIT] = "分割多条指令的关键字, 默认为 \\\\"

CFG_DATA_EXPIRE = "data_expire"
DEFAULT_CONFIG[CFG_DATA_EXPIRE] = "0"
DEFAULT_CONFIG_COMMENT[CFG_DATA_EXPIRE] = "是否定期清除过期数据与退出群聊, 0为不清理, 1为清理"

CFG_USER_EXPIRE_DAY = "user_expire_day"
DEFAULT_CONFIG[CFG_USER_EXPIRE_DAY] = "60"
DEFAULT_CONFIG_COMMENT[CFG_USER_EXPIRE_DAY] = "用户在多少天内没有使用过指令则清除相关数据"

CFG_GROUP_EXPIRE_DAY = "group_expire_day"
DEFAULT_CONFIG[CFG_GROUP_EXPIRE_DAY] = "14"
DEFAULT_CONFIG_COMMENT[CFG_GROUP_EXPIRE_DAY] = "群聊在多少天内没有使用过指令则清除相关数据并退群"

CFG_GROUP_EXPIRE_WARNING = "group_expire_warning_time"
DEFAULT_CONFIG[CFG_GROUP_EXPIRE_WARNING] = "1"
DEFAULT_CONFIG_COMMENT[CFG_GROUP_EXPIRE_WARNING] = f"清除相关数据并退群之前进行几次警告, 如{CFG_GROUP_EXPIRE_DAY}为14, {CFG_GROUP_EXPIRE_WARNING}为2, " \
                                                   f"则14天内群内没有人使用指令就会在第15天提示1次, 第16天提示1次然后退群. (提示词在localization中配置)"

CFG_WHITE_LIST_GROUP = "white_list_group"
DEFAULT_CONFIG[CFG_WHITE_LIST_GROUP] = ""
DEFAULT_CONFIG_COMMENT[CFG_WHITE_LIST_GROUP] = f"可填多个单元格, 或用;在同一个单元格分隔不同的群号, 列表中的群不会被自动清除信息或退群"

CFG_WHITE_LIST_USER = "white_list_user"
DEFAULT_CONFIG[CFG_WHITE_LIST_USER] = ""
DEFAULT_CONFIG_COMMENT[CFG_WHITE_LIST_USER] = f"可填多个单元格, 或用;在同一个单元格分隔不同的账号, 列表中的账号不会被自动清除信息"


def preprocess_white_list(raw_list: List[str]) -> List[str]:
    result_list: List[str] = []
    for raw_str in raw_list:
        for item in raw_str.split(";"):
            if item.strip():
                result_list.append(item.strip())
    return result_list
