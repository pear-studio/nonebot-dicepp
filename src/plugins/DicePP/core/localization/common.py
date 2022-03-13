from typing import Dict

COMMON_LOCAL_TEXT: Dict[str, str] = {}
COMMON_LOCAL_COMMENT: Dict[str, str] = {}

LOC_GROUP_ONLY_NOTICE = "group_only_notice"
COMMON_LOCAL_TEXT[LOC_GROUP_ONLY_NOTICE] = "This command should only be used in group!"
COMMON_LOCAL_COMMENT[LOC_GROUP_ONLY_NOTICE] = "当用户企图在私聊中执行只能在群聊中执行的命令时返回的提示"

LOC_FRIEND_ADD_NOTICE = "friend_add_notice"
COMMON_LOCAL_TEXT[LOC_FRIEND_ADD_NOTICE] = "Now you are my friend!"
COMMON_LOCAL_COMMENT[LOC_FRIEND_ADD_NOTICE] = "用户成功添加机器人为好友时发送的语句"

LOC_LOGIN_NOTICE = "login_notice"
COMMON_LOCAL_TEXT[LOC_LOGIN_NOTICE] = "Hi~"
COMMON_LOCAL_COMMENT[LOC_LOGIN_NOTICE] = "机器人登录时向Master发送的语句, 若为$则登录时不提示"

LOC_DAILY_UPDATE = "daily_update"
COMMON_LOCAL_TEXT[LOC_DAILY_UPDATE] = "Daily update complete"
COMMON_LOCAL_COMMENT[LOC_DAILY_UPDATE] = "每日更新时发送的语句, 若为$则不提示每日更新"

LOC_FUNC_DISABLE = "func_disable"
COMMON_LOCAL_TEXT[LOC_FUNC_DISABLE] = "Function {func} is disabled by manager"
COMMON_LOCAL_COMMENT[LOC_FUNC_DISABLE] = "某一功能被关闭时发送给用户的提示词, {func}为功能名"

LOC_GROUP_EXPIRE_WARNING = "group_expire_warning"
COMMON_LOCAL_TEXT[LOC_GROUP_EXPIRE_WARNING] = "Anyone needs me?"
COMMON_LOCAL_COMMENT[LOC_GROUP_EXPIRE_WARNING] = "群聊过期前发送的提示"
