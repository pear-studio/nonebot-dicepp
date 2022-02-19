from typing import Dict

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
