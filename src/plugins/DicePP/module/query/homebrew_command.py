from typing import List, Tuple, Dict, Optional, Set, Literal, Iterable, Any
import os
import datetime
#import openpyxl
import sqlite3
import math
#import random
# from openpyxl.utils import get_column_letter

from core.bot import Bot
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand, BotSendForwardMsgCommand
from core.communication import MessageMetaData, MessagePort, PrivateMessagePort, GroupMessagePort, preprocess_msg
from core.localization import LOC_FUNC_DISABLE
from core.config import DATA_PATH, CFG_MASTER, CFG_ADMIN
from module.common import DC_GROUPCONFIG
from module.query import create_empty_sqlite_database, load_data_from_xlsx_to_sqlite
from utils.localdata import read_xlsx, update_xlsx, col_based_workbook_to_dict, create_parent_dir, get_empty_col_based_workbook
from utils.data import yield_deduplicate

LOC_HOMEBREW_LOAD = "homebrew_load"
LOC_HOMEBREW_LOAD_FINISHED = "homebrew_load_finished"
LOC_HOMEBREW_LOAD_FAILED = "homebrew_load_failed"
LOC_HOMEBREW_LOAD_NOFILE = "homebrew_load_nofile"
LOC_HOMEBREW_LOAD_NEW = "homebrew_load_new"
LOC_HOMEBREW_CLEAN_FINISHED = "homebrew_clean_finished"
LOC_HOMEBREW_CLEAN_ALL_FINISHED = "homebrew_clean_all_finished"
LOC_HOMEBREW_TEMPLATE = "homebrew_template"
LOC_HOMEBREW_TEMPLATE_EXISTED = "homebrew_template_existed"
LOC_HOMEBREW_NULL = "homebrew_null"
LOC_HOMEBREW_ON = "homebrew_on"
LOC_HOMEBREW_OFF = "homebrew_off"
LOC_HOMEBREW_IS_ON = "homebrew_is_on"
LOC_HOMEBREW_IS_OFF = "homebrew_is_off"
LOC_HOMEBREW_STATUS_LOADED = "homebrew_status_loaded"
LOC_HOMEBREW_STATUS_NOTHING = "homebrew_status_nothing"

DC_NAME = "霓石精私设查询数据库"

CFG_QUERY_ENABLE = "query_enable"
CFG_QUERY_DATA_PATH = "query_data_path"

@custom_user_command(readable_name="私设指令",
                     priority=2,
                     group_only=True,
                     flag=DPP_COMMAND_FLAG_QUERY,
                     permission_require=1 # 限定群管理/骰管理使用
                     )
class HomebrewCommand(UserCommandBase):
    """
    私设资料库的指令, 以.私设或.hb开头
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        self.homebrew_database: Dict[str] = {}
        self.homebrew_sqlcur: Dict[str] = {}
        self.data_path = os.path.join(DATA_PATH, "QueryData/Homebrew")
        if not os.path.exists(self.data_path):
            create_parent_dir(self.data_path)

        reg_loc = bot.loc_helper.register_loc_text
        reg_loc(LOC_HOMEBREW_LOAD, "私设数据库开始加载......", "私设加载开始的提示")
        reg_loc(LOC_HOMEBREW_LOAD_FINISHED, "私设数据库加载完毕，已载入{num}条私设条目。", "私设加载完成的提示，num是私设条目数量")
        reg_loc(LOC_HOMEBREW_LOAD_FAILED, "私设数据库加载失败，请确保骰娘拥有群文件的上下载权限。", "私设加载失败的提示")
        reg_loc(LOC_HOMEBREW_LOAD_NOFILE, "私设数据库的文件夹为空。", "私设加载失败的提示")
        reg_loc(LOC_HOMEBREW_LOAD_NEW, "本次为首次加载，已创建新数据库。", "私设首次加载的额外提示")
        reg_loc(LOC_HOMEBREW_CLEAN_FINISHED, "私设数据库已清除{name}的全部私设条目。", "私设清除完成的提示，name是私设来源名称")
        reg_loc(LOC_HOMEBREW_CLEAN_ALL_FINISHED, "私设数据库已全部清除。", "私设全部清除完成的提示")
        reg_loc(LOC_HOMEBREW_TEMPLATE, "已上传模板文件。", "私设获取模板文件的提示")
        reg_loc(LOC_HOMEBREW_TEMPLATE_EXISTED, "已存在模板文件，请勿重复询问。", "私设模板文件已存在时的提示")
        reg_loc(LOC_HOMEBREW_NULL, "不存在此私设数据库。", "私设数据库不存在时的提示")
        reg_loc(LOC_HOMEBREW_ON, "私设查询现已开启。", "私设开关的提示（开）")
        reg_loc(LOC_HOMEBREW_OFF, "私设查询现已关闭。", "私设开关的提示（关）")
        reg_loc(LOC_HOMEBREW_IS_ON, "私设查询正常开启中。", "私设开关状态显示（开）")
        reg_loc(LOC_HOMEBREW_IS_OFF, "私设查询未开启。", "私设开关状态显示（关）")
        reg_loc(LOC_HOMEBREW_STATUS_LOADED, "当前群内已载入{num}条私设条目。", "私设已载入条目的状态显示，num是私设条目数量")
        reg_loc(LOC_HOMEBREW_STATUS_NOTHING, "当前群内未载入任何私设，请使用。私设加载 使骰娘从群文件中获取私设文件并加载", "私设未载入条目的状态显示")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = False
        should_pass: bool = False
        mode: Optional[Literal["load","template","help","show","clean","on","off"]] = None
        arg_str: Optional[str] = None
        admin: bool = (meta.user_id in self.bot.cfg_helper.get_config(CFG_MASTER)) or (meta.user_id in self.bot.cfg_helper.get_config(CFG_ADMIN))

        # 常规查询指令
        if admin:
            for key in ["私设", "房规", "homebrew", "hb"]:
                if not should_proc and msg_str.startswith(f".{key}"):
                    arg_str = msg_str[1 + len(key):].strip()
                    should_proc = True
                    if arg_str.startswith("加载") or arg_str.startswith("重载"):
                        mode = "load"
                        arg_str = arg_str[2:].strip()
                    elif arg_str.startswith("模板"):
                        mode = "template"
                    elif arg_str.startswith("帮助"):
                        mode = "help"
                    elif arg_str.startswith("显示"):
                        mode = "show"
                    elif arg_str.startswith("清除"):
                        mode = "clean"
                        arg_str = arg_str[2:].strip()
                    elif arg_str.startswith("开"):
                        mode = "on"
                    elif arg_str.startswith("关"):
                        mode = "off"
                    else:
                        mode = "show"

        assert (not should_proc) or mode
        hint = (mode,arg_str)
        return should_proc, should_pass, hint

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        from module.query.query_database import CONNECTED_QUERY_DATABASES
        port = GroupMessagePort(meta.group_id)
        source_port = meta.group_id
        mode: Optional[Literal["load","template","help","show","clean","on","off"]] = hint[0]
        arg_str: str = hint[1]
        open = self.bot.data_manager.get_data(DC_GROUPCONFIG,[meta.group_id,"query_homebrew"],default_val=False)
        has_database = False
        
        db = "HB" + source_port
        path = self.data_path + "/" + db + ".db"
        if os.path.exists(path):
            has_database = True
            
        feedback: str = ""

        # 判断功能开关
        try:
            assert (int(self.bot.cfg_helper.get_config(CFG_QUERY_ENABLE)[0]) != 0)
        except AssertionError:
            feedback = self.bot.loc_helper.format_loc_text(LOC_FUNC_DISABLE, func=self.readable_name)
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]

        # 处理指令
        if mode == "load":
            if not has_database:
                create_empty_sqlite_database(path)
                feedback += "\n" + self.format_loc(LOC_HOMEBREW_LOAD_NEW)
            feedback += self.format_loc(LOC_HOMEBREW_LOAD)
            if len(arg_str) > 0:
                if load_data_from_xlsx_to_sqlite(DATA_PATH + "/ExcelData/" + arg_str + ".xlsx", path, 2):
                    feedback += "\n加载了 " + arg_str + ".xlsx\n"
                    feedback += self.format_loc(LOC_HOMEBREW_LOAD_FINISHED,num=self.show_homebrews_count_by_from(db,arg_str))
                else:
                    feedback += "\n加载失败。"
        elif mode == "template":
            feedback = "没写好呢"
        elif mode == "help":
            feedback = "没写好呢"
        elif mode == "clean":
            if not has_database:
                feedback = self.format_loc(LOC_HOMEBREW_NULL)
            else:
                feedback += self.clean_homebrews(db,arg_str)
        elif mode == "on":
            self.bot.data_manager.set_data(DC_GROUPCONFIG,[meta.group_id,"query_homebrew"],True)
            feedback = self.format_loc(LOC_HOMEBREW_ON)
        elif mode == "off":
            self.bot.data_manager.set_data(DC_GROUPCONFIG,[meta.group_id,"query_homebrew"],False)
            feedback = self.format_loc(LOC_HOMEBREW_OFF)
        else:
            if has_database:
                feedback = self.show_homebrew_status(db)
            else:
                feedback = self.format_loc(LOC_HOMEBREW_STATUS_NOTHING)

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]
        
    def show_homebrew_status(self, db: str) -> str:
        from module.query.query_database import CONNECTED_QUERY_DATABASES, DATABASE_CURSOR
        feedback = "已加载以下私设:"
        if db in CONNECTED_QUERY_DATABASES.keys():
            cursor = DATABASE_CURSOR[db]
            cursor.execute("select 来源, count(名称) from data group by 来源")
            for val in cursor:
                feedback += "\n" + val[0][3:] + " " + str(val[1]) + "个条目"
            return feedback
        else:
            return f"未加载 {db} 的私设条目。"
        
    def show_homebrew_count(self, db: str) -> str:
        from module.query.query_database import CONNECTED_QUERY_DATABASES, DATABASE_CURSOR
        if db in CONNECTED_QUERY_DATABASES.keys():
            cursor = DATABASE_CURSOR[db]
            cursor.execute("select max(rowid) from data")
            return f"已加载 {db} 的 {str(cursor.fetchone()[0])} 条私设条目。"
        else:
            return f"未加载 {db} 的私设条目。"
    
    def show_homebrews_count_by_from(self, db: str, name: str) -> int:
        from module.query.query_database import CONNECTED_QUERY_DATABASES, DATABASE_CURSOR
        result: int = 0
        if db in CONNECTED_QUERY_DATABASES.keys():
            cursor = DATABASE_CURSOR[db]
            cursor.execute("select count(名称) from data where 来源 like '私设:" + name + "'")
            result = int(cursor.fetchone()[0])
        else:
            result = 0
        return result
    
    def clean_homebrews(self, db: str, name: str = "") -> str:
        from module.query.query_database import CONNECTED_QUERY_DATABASES, DATABASE_CURSOR
        if name == "":
            return "你必须指定一个私设来源才能进行此操作"
        if db in CONNECTED_QUERY_DATABASES.keys():
            cursor = DATABASE_CURSOR[db]
            cursor.execute("delete from data where 来源 like '私设:" + name + "'")
            CONNECTED_QUERY_DATABASES[db].commit()
            return self.format_loc(LOC_HOMEBREW_CLEAN_FINISHED,name = name)
        else:
            return f"未加载 {db} 私设条目。"

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword in ["私设", "房规", "homebrew", "hb"]:
            help_str = "私设: .私设 私设数据库管理系统"\
                    "\n一套创建独属于本群组的查询数据库"\
                    "\n这些内容会作为一部分加入到.查询指令的结果中"\
                    "\n.私设 加载 从群文件的特定文件中加载私设"\
                    "\n.私设 模板 获取一个模板文件（还会顺带创建文件夹）"\
                    "\n.私设 帮助 获取私设格式与详细帮助"\
                    "\n.私设 显示 显示当前已有的私设"\
                    "\n.私设 开 开启私设模式（允许查询）"\
                    "\n.私设 关 关闭私设模式"
            return help_str
        return ""

    def get_description(self) -> str:
        return ".私设 私设数据库管理系统"