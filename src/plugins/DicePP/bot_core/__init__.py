from bot_core.common import DC_META, DC_USER_DATA, DC_GROUP_DATA, DC_NICKNAME, DC_MACRO, DC_VARIABLE
from bot_core.common import DCP_META_ONLINE, DCP_META_ONLINE_PERIOD, DCP_META_ONLINE_LAST
from bot_core.common import DCP_META_MSG, DCP_META_MSG_TOTAL_NUM, DCP_META_MSG_TODAY_NUM, DCP_META_MSG_LAST_NUM
from bot_core.common import DCP_META_CMD, DCP_META_CMD_TOTAL_NUM, DCP_META_CMD_TODAY_NUM, DCP_META_CMD_LAST_NUM
from bot_core.common import NICKNAME_ERROR

from bot_core.message import MessageMetaData, MessageSender
from bot_core.notice import NoticeData, GroupIncreaseNoticeData, FriendAddNoticeData
from bot_core.request import RequestData, FriendRequestData, JoinGroupRequestData, InviteGroupRequestData
from bot_core.macro import BotMacro, MACRO_COMMAND_SPLIT, MACRO_PARSE_LIMIT
from bot_core.variable import BotVariable

from bot_core.dicebot import Bot
