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

# 内存监控配置
CFG_MEMORY_MONITOR_ENABLE = "memory_monitor_enable"
DEFAULT_CONFIG[CFG_MEMORY_MONITOR_ENABLE] = "0"
DEFAULT_CONFIG_COMMENT[CFG_MEMORY_MONITOR_ENABLE] = "是否开启内存监控, 0为关闭, 1为开启"

CFG_MEMORY_WARN_PERCENT = "memory_warn_percent"
DEFAULT_CONFIG[CFG_MEMORY_WARN_PERCENT] = "80"
DEFAULT_CONFIG_COMMENT[CFG_MEMORY_WARN_PERCENT] = "内存使用率警告阈值 (百分比), 达到后给Master发送警告"

CFG_MEMORY_RESTART_PERCENT = "memory_restart_percent"
DEFAULT_CONFIG[CFG_MEMORY_RESTART_PERCENT] = "90"
DEFAULT_CONFIG_COMMENT[CFG_MEMORY_RESTART_PERCENT] = "内存使用率重启阈值 (百分比), 达到后自动重启机器人"

CFG_MEMORY_RESTART_MB = "memory_restart_mb"
DEFAULT_CONFIG[CFG_MEMORY_RESTART_MB] = "2048"
DEFAULT_CONFIG_COMMENT[CFG_MEMORY_RESTART_MB] = "内存绝对上限 (MB), 达到后自动重启机器人 (即使百分比未达阈值)"

CFG_HUB_API_URL = "dicehub_api_url"
DEFAULT_CONFIG[CFG_HUB_API_URL] = ""
DEFAULT_CONFIG_COMMENT[CFG_HUB_API_URL] = "DiceHub 网站 API 地址, 如 https://dungeon-toolkit.example.com/api"

CFG_HUB_API_KEY = "dicehub_api_key"
DEFAULT_CONFIG[CFG_HUB_API_KEY] = ""
DEFAULT_CONFIG_COMMENT[CFG_HUB_API_KEY] = "DiceHub 网站分配的 API Key"

CFG_HUB_NAME = "dicehub_name"
DEFAULT_CONFIG[CFG_HUB_NAME] = "未命名"
DEFAULT_CONFIG_COMMENT[CFG_HUB_NAME] = "DiceHub 显示的机器人昵称"

CFG_HUB_ENABLE = "dicehub_enable"
DEFAULT_CONFIG[CFG_HUB_ENABLE] = "1"
DEFAULT_CONFIG_COMMENT[CFG_HUB_ENABLE] = "DiceHub 功能开关, 1为开启, 0为关闭"

# LLM 配置
CFG_LLM_ENABLED = "llm_enabled"
DEFAULT_CONFIG[CFG_LLM_ENABLED] = "false"
DEFAULT_CONFIG_COMMENT[CFG_LLM_ENABLED] = "是否启用 LLM 对话功能, true/false"

CFG_LLM_API_KEY = "llm_api_key"
DEFAULT_CONFIG[CFG_LLM_API_KEY] = ""
DEFAULT_CONFIG_COMMENT[CFG_LLM_API_KEY] = "LLM API 密钥 (如 Moonshot/Kimi 的 API Key)"

CFG_LLM_BASE_URL = "llm_base_url"
DEFAULT_CONFIG[CFG_LLM_BASE_URL] = "https://api.moonshot.cn/v1"
DEFAULT_CONFIG_COMMENT[CFG_LLM_BASE_URL] = "LLM API 基础 URL"

CFG_LLM_MODEL = "llm_model"
DEFAULT_CONFIG[CFG_LLM_MODEL] = "kimi-k2.5"
DEFAULT_CONFIG_COMMENT[CFG_LLM_MODEL] = "LLM 模型名称"

CFG_LLM_PERSONALITY = "llm_personality"
DEFAULT_CONFIG[CFG_LLM_PERSONALITY] = "你是一个 helpful 的助手，回答简洁。"
DEFAULT_CONFIG_COMMENT[CFG_LLM_PERSONALITY] = "LLM 人格设定 (系统提示词)"

CFG_LLM_MAX_CONTEXT = "llm_max_context"
DEFAULT_CONFIG[CFG_LLM_MAX_CONTEXT] = "20"
DEFAULT_CONFIG_COMMENT[CFG_LLM_MAX_CONTEXT] = "最大上下文消息数 (保留最近多少条对话)"

CFG_LLM_TIMEOUT = "llm_timeout"
DEFAULT_CONFIG[CFG_LLM_TIMEOUT] = "10"
DEFAULT_CONFIG_COMMENT[CFG_LLM_TIMEOUT] = "LLM 请求超时时间 (秒)"
