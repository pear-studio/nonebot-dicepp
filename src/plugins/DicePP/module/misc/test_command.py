from typing import List, Tuple, Any
from nonebot.adapters.onebot.v11 import Bot, Event, Message, MessageSegment, GroupMessageEvent,PrivateMessageEvent
from core.bot import Bot
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand, BotSendFileCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort
import os

LOC_JSON_INVITE_UNABLE = "json_invite_unable"

@custom_user_command(readable_name="新功能测试指令",
                     priority=0,
                     flag=DPP_COMMAND_FLAG_FUN)
class NewTestCommand(UserCommandBase):
    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_JSON_INVITE_UNABLE, "暂时不支持邀请链接邀请加群，请考虑让其他人（比如群管理）直接邀请加群。具体信息可以去群861919492询问。", "私聊使用JSON格式邀请链接导致骰娘无法处理时返回的文本")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        '''
        key: str = "test"
        if msg_str.startswith("."+key):
            return True, False, msg_str[len(key)+1:].strip()
        '''
        if meta.raw_msg.startswith("[CQ:json") and "com.tencent.qun.invite" in meta.raw_msg:
            return True, False, ""
        return False, False, ""
    
    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        return[BotSendMsgCommand(self.bot.account, self.format_loc(LOC_JSON_INVITE_UNABLE), [port])]
        '''
        path = "D:\\DicePPβ\\骰娘本体\\"+str(hint).replace("/","\\")
        if os.path.exists(path):
            return [BotSendFileCommand(self.bot.account, path, os.path.basename(path), [port]), BotSendMsgCommand(self.bot.account, "文件已发送", [port])]

        return[BotSendMsgCommand(self.bot.account, "未找到文件："+path, [port])]
        '''
        
        """
        async def get_group_files():
            file_list, folder_list = await self.bot.proxy.bot.call_api("get_group_root_files", group_id=int(meta.group_id))
            feedbacks = []
            for folder in folder_list:
                sub_feedback = []
                subfile_list = await self.bot.proxy.bot.call_api("get_group_files_by_folder", group_id=int(meta.group_id),folder_id=folder.folder_id)
                feedback.append(folder.folder_name+"\n".join([" + "+file.file_name for file in subfile_list]))
            feedback += [file.file_name for file in file_list]
            return [BotSendMsgCommand(self.bot.account, "本群有以下文件：\n"+"\n".join(feedbacks), [port])]
        self.bot.register_task(get_group_files, timeout=60, timeout_callback=lambda: [BotSendMsgCommand(self.bot.account, "获取超时!", [port])])
        return [BotSendMsgCommand(self.bot.account, "开始获取...", [port])]
        """
        
    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        return ""

    def get_description(self) -> str:
        return "测试指令"