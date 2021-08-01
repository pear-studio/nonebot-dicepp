from typing import Dict
COMMON_LOCAL_TEXT: Dict[str, str] = {}
COMMON_LOCAL_COMMENT: Dict[str, str] = {}

LOC_GROUP_ONLY_NOTICE = "group_only_notice"
COMMON_LOCAL_TEXT[LOC_GROUP_ONLY_NOTICE] = "This command should only be used in group!"
COMMON_LOCAL_COMMENT[LOC_GROUP_ONLY_NOTICE] = "当用户企图在私聊中执行只能在群聊中执行的命令时返回的提示"
LOC_FRIEND_ADD_NOTICE = "friend_add_notice"
COMMON_LOCAL_TEXT[LOC_FRIEND_ADD_NOTICE] = "Now you are my friend!"
COMMON_LOCAL_COMMENT[LOC_FRIEND_ADD_NOTICE] = "用户成功添加骰娘为好友时发送的语句"
