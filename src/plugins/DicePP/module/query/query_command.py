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
from core.data import DC_USER_DATA
from module.common import DC_GROUPCONFIG
from module.query import create_empty_sqlite_database, load_data_from_xlsx_to_sqlite, QUERY_DATA_FIELD, QUERY_DATA_FIELD_LIST, QUERY_REDIRECT_FIELD, QUERY_REDIRECT_FIELD_LIST
from utils.localdata import read_xlsx, update_xlsx, col_based_workbook_to_dict, create_parent_dir, get_empty_col_based_workbook
from utils.time import get_current_date_raw
from utils.data import yield_deduplicate

from module.query.query_database import CONNECTED_QUERY_DATABASES, DATABASE_CURSOR, create_query_database, connect_query_database, disconnect_query_database, regexp_normalize

LOC_QUERY_RESULT = "query_result"
LOC_QUERY_SINGLE_RESULT = "query_single_result"
LOC_QUERY_MULTI_RESULT = "query_multi_result"
LOC_QUERY_MULTI_RESULT_ITEM = "query_multi_result_item"
LOC_QUERY_MULTI_RESULT_PAGE = "query_multi_result_page"
LOC_QUERY_MULTI_RESULT_PAGE_UNDERFLOW = "query_multi_result_page_underflow"
LOC_QUERY_MULTI_RESULT_PAGE_OVERFLOW = "query_multi_result_page_overflow"
LOC_QUERY_MULTI_RESULT_CATALOGUE = "query_multi_result_catalogue"
LOC_QUERY_NO_RESULT = "query_no_result"
LOC_QUERY_TOO_MUCH_RESULT = "query_too_much_result"
LOC_QUERY_KEY_NUM_EXCEED = "query_key_num_exceed"
LOC_QUERY_CELL_BOOK = "query_cell_book"
LOC_QUERY_CELL_REDIRECT = "query_cell_redirect"

CFG_QUERY_ENABLE = "query_enable"
CFG_QUERY_DATA_PATH = "query_data_path"
CFG_QUERY_PRIVATE_DATABASE = "query_private_database"

QUERY_ITEM_FIELD_DESC_DEFAULT_LEN = 20  # 默认用前多少个字符作为默认Description
QUERY_SPLIT_LINE_LEN = 20  # 默认如何分割过长查询文本

MAX_QUERY_KEY_NUM = 5  # 最多能同时用多少个查询关键字
MAX_QUERY_CANDIDATE_NUM = 10  # 详细查询时一页最多能同时展示多少个条目
MAX_QUERY_CANDIDATE_SIMPLE_NUM = 30  # 简略查询时一页最多能同时展示多少个条目
MAX_QUERY_ITEM_NUM = 1000  # 最多能查询多少条目
RECORD_RESPONSE_TIME = 60  # 至多响应多久以前的查询指令, 多余的将被清理, 单位为秒
RECORD_EDIT_RESPONSE_TIME = 600  # 至多响应多久以前的编辑指令, 多余的将被清理, 单位为秒
RECORD_CLEAN_FREQ = 50  # 每隔多少次查询指令尝试清理一次查询记录

QUERY_DELETE_MAGICWORD = "DELETE"  # 删除查询条目必须回复的密文

class QueryData:
    def __init__(self,data_str: List[str],redirect_by: str = "",database: str = "DND5E"):
        """单条被查询的数据"""
        self.original_data = data_str
        self.hash_word = self.original_data[0]+"#"+self.original_data[2]+"#"+self.original_data[3]
        self.redirect_by = redirect_by
        self.database = database

    def data_extend(self):
        self.data_name = self.original_data[0]
        self.data_name_en = self.original_data[1]
        self.data_from = self.original_data[2]
        self.data_catalogue = self.original_data[3]
        self.data_tag = self.original_data[4]
        self.data_content = self.original_data[5]
        # 处理显示数据
        self.display_name = self.data_name if len(self.data_name) > 0 else self.data_name_en
        if ":" in self.data_name:
            name_data: List[str] = self.data_name.split(":")
            self.display_prefix = name_data[:-1]
            self.last_name = name_data[-1]
        else:
            self.display_prefix = []
            self.last_name = self.data_name
    
    def get(self,data_index: int) -> str:
        if data_index == 0:
            return self.data_name
        elif data_index == 1:
            return self.data_name_en
        elif data_index == 2:
            return self.data_from
        elif data_index == 3:
            return self.data_catalogue
        elif data_index == 4:
            return self.data_tag
        elif data_index == 5:
            return self.data_content
    
    def get_shortcut(self,data_index: int) -> str:
        result: str = ""
        if data_index == 0:
            result = self.data_name
        elif data_index == 1:
            result = self.data_name_en
        elif data_index == 2:
            result = self.data_from
        elif data_index == 3:
            result = self.data_catalogue
        elif data_index == 4:
            result = self.data_tag
        elif data_index == 5:
            result = self.data_content
        
        length: int = len(result) 
        if length > 8:
            return result[:6].strip() + "..."
        elif length == 0:
            return "<空>"
        else:
            return result
    
    def set(self,data_index: int,data_new: str):
        if data_index == 0:
            self.data_name = data_new
        elif data_index == 1:
            self.data_name_en = data_new
        elif data_index == 2:
            self.data_from = data_new
        elif data_index == 3:
            self.data_catalogue = data_new
        elif data_index == 4:
            self.data_tag = data_new
        elif data_index == 5:
            self.data_content = data_new
    
    def to_tuple(self) -> Tuple[str]:
        return (self.data_name,self.data_name_en,self.data_from,self.data_catalogue,self.data_tag,self.data_content)
    
    def replace_semicolon_for_insert(self) -> str:
        return "VALUES('{0}','{1}','{2}','{3}','{4}','{5}')".format(
                self.data_name.replace("'","''"),
                self.data_name_en.replace("'","''"),
                self.data_from.replace("'","''"),
                self.data_catalogue.replace("'","''"),
                self.data_tag.replace("'","''"),
                self.data_content.replace("'","''"))

    def origin_check(self) -> str:
        checks: list = []
        index: int = 0
        for field in QUERY_DATA_FIELD_LIST:
            if len(self.original_data[index]) > 0:
                checks.append(field + " Like '" + self.original_data[index].replace("'","''") + "'")
            else:
                checks.append(field + " NOT Like '_%'")
            index += 1
            if index >= 4:
                break
        return "WHERE ({0})".format(" AND ".join(checks))

class QueryRecord:
    def __init__(self,data: List[QueryData] ,database: str , time: datetime.datetime, length: int):
        """记录一次可交互的查询指令"""
        self.data = data  # 数据
        self.database = database
        self.time = time  # 更新时间
        self.length = length  # 长度
        self.page = 1  # 当前的页数
        self.mode = 0  # 0代表仅显示名称, 1代表显示名称和简单描述
        self.filter_mode = 0  # 0代表直接显示, 1代表分类显示

        self.edit_flag = False  # 编辑模式
        self.editing = False  # 编辑中
        self.edit_new = False  # 新建条目模式
        self.edit_index = -1  # 正在编辑的内容的index
    
    def process_data(self):
        # 处理一下数据使得数据更易于查看
        prefix_finding: bool = True
        prefixs: List[str] = []
        should_delete_prefix: bool = False
        for _data in self.data:
            # 获得所有人都有的前缀
            if prefix_finding:
                if len(_data.display_prefix) > 0:
                    prefixs = _data.display_prefix
                    prefix_finding = False
                    should_delete_prefix = True
                else:
                    should_delete_prefix = False
                    break
            else:
                while(len(prefixs) > 0 and not _data.data_name.startswith(":".join(prefixs)+":")):
                    prefixs.pop()
                if len(prefixs) == 0:
                    should_delete_prefix = False
                    break
        #消除完全相同的前缀
        index: int = 0
        if should_delete_prefix:
            prefix_length = len(":".join(prefixs))+1
            for _data in self.data:
                self.data[index].display_name = self.data[index].data_name[prefix_length:]
                index += 1
        # 让重名条目的名称获得差分
        dupe_dict: dict = {}
        index = 0
        for _data in self.data:
            if _data.data_name in dupe_dict.keys():
                dupe_dict[_data.data_name].append(index)
            else:
                dupe_dict[_data.data_name] = [index]
            index += 1
        for _dupe in dupe_dict.values():
            if len(_dupe) > 1:
                for _index in _dupe:
                    self.data[_index].display_name = self.data[_index].data_name + "(" + self.data[_index].data_from+self.data[_index].data_catalogue + ")"
        # WIP 当父关键词出现的时候不显示子关键词
        #name_list: list = [_data[0] for _data in self.data]
        #unique_dict

    def create_catalogue_list(self):
        self.catalogue_list = {}  # 分类列表与对应数量
        self.cata_length = 0  # 分类数量
        
        index: int = 0
        for _data in self.data:
            cata: str = _data.data_catalogue if len(_data.data_catalogue) != 0 else "杂项"
            if not cata in self.catalogue_list:
                self.catalogue_list[cata] = 1
                self.cata_length += 1
            else:
                self.catalogue_list[cata] = self.catalogue_list[cata] + 1
            index += 1
        # 分类数量为1,就没有必要分类了
        if len(self.catalogue_list) == 1:
            self.filter_mode = 0

    def select_catalogue(self,catalogue: str):
        new_data: List[QueryData] = []
        
        for _data in self.data:
            cata: str = _data.data_catalogue if len(_data.data_catalogue) != 0 else "杂项"
            if cata == catalogue:
                new_data.append(_data)
        
        self.filter_mode = 0
        self.data = new_data
        self.origin_data = new_data
        self.length = len(new_data)
        self.page = 1
        self.process_data()

    def choose_edit_target(self,index: int) -> str:
        if index < self.length:
            self.data = [self.data[index]]
            self.page = 1
            self.length = 1
            self.editing = True
            self.edit_index = -1
            feedback = []
            data = self.data[0]
            index = 0
            for field in QUERY_DATA_FIELD_LIST:
                feedback.append(str(index) + "." + field + ": " +  data.get_shortcut(index))
                index += 1
            return "查询编辑: " + self.data[0].data_name + "\n   " + "\n   ".join(feedback) + "\n   +.保存并退出\n   -.取消本次编辑"
        else:
            return "超出范围"

    def edit_data(self, input_data: str) -> str:
        data_new: str = input_data.strip()
        if data_new == "-" or not data_new:
            data_new = ""
        else:
            if self.edit_index == 0 or self.edit_index == 1 or self.edit_index == 7:
                data_new = ((data_new.replace("：",":")).replace("（","(")).replace("）",")")
            elif self.edit_index == 6:
                data_new = data_new.replace("\n","")
        if self.edit_index == 0 and data_new == "":
            return "内容不能为空，请重新输入。"
        self.data[0].set(self.edit_index,data_new)
        self.edit_index = -1
        return "编辑完成，您可以根据上表继续您的编辑。"

    def edit_commit(self, database, cursor):
        data = self.data[0]
        if self.edit_new:
            cursor.execute("INSERT INTO data " + data.replace_semicolon_for_insert())
        else:
            cursor.execute("UPDATE data SET 名称 = ?,英文 = ?,来源 = ?,分类 = ?,标签 = ?,内容 = ? " + data.origin_check(),data.to_tuple())
            # 如果改变了名称，就顺带着改编一下重定向
            if data.data_name != data.original_data[0] and data.original_data[0] != "":
                cursor.execute("UPDATE redirect SET 重定向 = ? WHERE 重定向 == ?",(data.data_name,data.original_data[0]))
        database.commit()
    
    def delete(self, database, cursor):
        data = self.data[0]
        cursor.execute("DELETE FROM data" + data.origin_check())
        database.commit()

class QueryError(Exception):
    """
    因为查询产生的异常, 说明操作失败的原因, 应当在上一级捕获
    """
    def __init__(self, info: str):
        self.info = info

    def __str__(self):
        return f"[Query] [Error] {self.info}"

@custom_user_command(readable_name="查询指令",
                     priority=2,
                     group_only=False,
                     flag=DPP_COMMAND_FLAG_QUERY)
class QueryCommand(UserCommandBase):
    """
    查询资料库的指令, 以.查询或.q开头
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        #self.query_dict: Dict[str, List[int]] = {}  # key为查询关键字, value为item uuid
        #self.item_uuid_dict: Dict[int, QueryItem] = {}  # key为item uuid
        #self.src_uuid_dict: Dict[int, QuerySource] = {}  # key为source uuid
        self.record_dict: Dict[MessagePort, QueryRecord] = {}
        #CONNECTED_QUERY_DATABASES: Dict[str] = {}
        self.record_clean_flag: int = 0

        reg_loc = bot.loc_helper.register_loc_text
        reg_loc(LOC_QUERY_RESULT, "{result}", "查询成功时返回的内容, result为single_result或multi_result")
        reg_loc(LOC_QUERY_SINGLE_RESULT, "{keyword} {en_keyword}{tag}\n{content}{book}{redirect}",
                "查询找到唯一条目, keyword: 条目名称, en_keyword: 条目英文名, content: 词条内容"
                ", book: 来源*, redirect: 重定向自*, tag: 换行+标签*  (*如果有则显示, 没有则忽略)")
        reg_loc(LOC_QUERY_MULTI_RESULT_CATALOGUE, "请选择一个分类",
                "查询找到多个条目时选择分类的文本提示")
        reg_loc(LOC_QUERY_MULTI_RESULT_PAGE, "{page_cur}/{page_total}页, -上一页/下一页+",
                "搜索结果出现多页时提示, {page_cur}表示当前页, {page_total}表示总页数")
        reg_loc(LOC_QUERY_MULTI_RESULT_PAGE_UNDERFLOW, "已经是最前一页了!", "用户尝试在第一页往前翻页时的提醒")
        reg_loc(LOC_QUERY_MULTI_RESULT_PAGE_OVERFLOW, "已经是最后一页了!", "用户尝试在最后一页往后翻页时的提醒")
        reg_loc(LOC_QUERY_NO_RESULT, "未能查询到内容...", "查询失败时的提示")
        reg_loc(LOC_QUERY_TOO_MUCH_RESULT, "查询到过多内容...", "查询到过多内容时的提示")
        reg_loc(LOC_QUERY_KEY_NUM_EXCEED, "关键词数量上限{key_num}个",
                "用户查询时使用过多关键字时的提示 {key_num}为关键字数量上限")
        reg_loc(LOC_QUERY_CELL_BOOK, "\n来源：{book}",
                "来源展示格式，book: 来源*")
        reg_loc(LOC_QUERY_CELL_REDIRECT, "\n重定向自：{redirect}",
                "重定向展示格式，redirect: 重定向自*")

        bot.cfg_helper.register_config(CFG_QUERY_ENABLE, "1", "查询指令开关")
        bot.cfg_helper.register_config(CFG_QUERY_DATA_PATH, "./QueryData", "查询指令的数据来源，已弃用，请勿修改")
        bot.cfg_helper.register_config(CFG_QUERY_PRIVATE_DATABASE, "DND5E2014", "查询指令私聊时默认使用的数据库，群聊使用数据库以群配置为准")
        #已弃用，请使用mode_command那边的CFG。

    def delay_init(self) -> List[str]:
        # 从本地文件中读取数据库
        data_path_list: List[str] = self.bot.cfg_helper.get_config(CFG_QUERY_DATA_PATH)
        init_info: List[str] = [""]
        for i, path in enumerate(data_path_list):
            if path.startswith("./"):  # 用DATA_PATH作为当前路径
                data_path_list[i] = os.path.join(DATA_PATH, path[2:])
        for data_path in data_path_list:
            connect_query_database(data_path)
        init_info[0] = self.get_state()
        return init_info

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = False
        should_pass: bool = False
        mode: Optional[Literal["query", "search", "select", "flip_page", "editing", "new", "feedback", "redirect"]] = None
        arg_str: str = ""
        show_mode: int = 0

        # 响应交互查询指令
        port = MessagePort(meta.group_id, meta.user_id)
        if port in self.record_dict:
            record = self.record_dict[port]
            msg_word = msg_str.strip()
            if get_current_date_raw() - record.time < datetime.timedelta(seconds=RECORD_EDIT_RESPONSE_TIME if record.edit_flag else RECORD_RESPONSE_TIME):
                # 选择条目
                if record.editing:
                    if record.edit_index == -1:
                        if msg_word == "+":  # 结束编辑并保存
                            if not record.data[0].data_name and not record.data[0].data_name_en:
                                mode, arg_str = "feedback", "查询条目的名称或英文不能为空！"
                            else:
                                database = record.data[0].database
                                self.record_dict[port].edit_commit(CONNECTED_QUERY_DATABASES[database],DATABASE_CURSOR[database])
                                del self.record_dict[port]
                                mode, arg_str = "feedback", "已结束本次编辑并保存"
                            should_proc = True
                        elif msg_word == "-":  # 取消编辑
                            del self.record_dict[port]
                            mode, arg_str = "feedback", "已取消本次编辑"
                            should_proc = True
                        elif msg_word == QUERY_DELETE_MAGICWORD:  # 删除条目
                            if not record.edit_new:
                                database = record.data[0].database
                                self.record_dict[port].delete(CONNECTED_QUERY_DATABASES[database],DATABASE_CURSOR[database])
                                del self.record_dict[port]
                                mode, arg_str = "feedback", "接收到密文，已删除该条目"
                                should_proc = True
                        else:  # 开始编辑
                            try:
                                target_index: int = int(msg_word)
                                should_proc = (0 <= target_index <= 5)
                                mode, arg_str = "editing", meta.plain_msg
                                should_proc = True
                            except ValueError:
                                pass
                    else:
                        mode, arg_str = "feedback", record.edit_data(str(meta.plain_msg))
                        should_proc = True
                else:
                    try:
                        target_index: int = int(msg_str)
                        should_proc = (0 <= target_index <= record.length) if record.filter_mode == 0 else (0 <= target_index <= record.cata_length)
                        mode, arg_str = "select", msg_str
                    except ValueError:
                        pass
                    # 翻页
                    if record.filter_mode == 0 and not should_proc:
                        if msg_word == "+":
                            should_proc, mode, arg_str = True, "flip_page", "+"
                        elif msg_word == "-":
                            should_proc, mode, arg_str = True, "flip_page", "-"
            else:
                del self.record_dict[port]  # 清理过期条目

        # 常规查询指令
        for key in ["查询", "query", "q"]:
            if not should_proc and msg_str.startswith(f".{key}"):
                arg_str = msg_str[1 + len(key):].strip()
                should_proc, mode= True, "query"
        for key in ["搜索", "检索", "search", "s"]:
            if not should_proc and msg_str.startswith(f".{key}"):
                arg_str = msg_str[1 + len(key):].strip()
                should_proc, mode= True, "search"
        # 重定向相关
        for key in ["重定向", "redirect"]:
            if not should_proc and msg_str.startswith(f".{key}"):
                should_proc, mode, arg_str = True, "redirect", (meta.plain_msg.split(key,1)[1]).strip()
        # 数据库相关
        for key in ["数据库", "database"]:
            if not should_proc and msg_str.startswith(f".{key}"):
                should_proc, mode, arg_str = True, "database", msg_str[1 + len(key):].strip()
        
        # 数据库子指令解析不依赖权限：低权限用户也应得到明确的“无权限”反馈，而不是帮助文本
        if mode == "database":
            if arg_str.startswith("加载"):
                arg_str = arg_str[2:].strip()
                show_mode = 1  # 加载模式
            elif arg_str.startswith("load"):
                arg_str = arg_str[4:].strip()
                show_mode = 1  # 加载模式
            elif arg_str.startswith("卸载"):
                arg_str = arg_str[2:].strip()
                show_mode = 2  # 卸载模式
            elif arg_str.startswith("disload"):
                arg_str = arg_str[7:].strip()
                show_mode = 2  # 卸载模式
            elif arg_str.startswith("创建"):
                arg_str = arg_str[2:].strip()
                show_mode = 3  # 创建模式
            elif arg_str.startswith("create"):
                arg_str = arg_str[6:].strip()
                show_mode = 3  # 创建模式
            elif arg_str.startswith("导入"):
                arg_str = arg_str[2:].strip()
                show_mode = 4  # 导入模式
            elif arg_str.startswith("import"):
                arg_str = arg_str[6:].strip()
                show_mode = 4  # 导入模式
            elif arg_str.startswith("列表"):
                arg_str = arg_str[2:].strip()
                show_mode = 5  # 显示模式
            elif arg_str.startswith("list"):
                arg_str = arg_str[4:].strip()
                show_mode = 5  # 显示模式

        if meta.permission >= 3:# 需要3级权限（骰管理/骰主）才能编辑资料库
            if mode == "redirect":
                if arg_str.startswith("删除"):
                    arg_str = arg_str[2:].strip()
                    show_mode = 9 # 删除模式
                elif arg_str.startswith("delete"):
                    arg_str = arg_str[6:].strip()
                    show_mode = 9 # 删除模式
                elif arg_str.startswith("创建"):
                    arg_str = arg_str[2:].strip()
                    show_mode = 8 # 创建模式
                elif arg_str.startswith("create"):
                    arg_str = arg_str[6:].strip()
                    show_mode = 8 # 创建模式
            elif mode == "query" or mode == "search":
                if arg_str.startswith("编辑"):
                    arg_str = arg_str[2:].strip()
                    show_mode = 9 # 编辑模式
                elif arg_str.startswith("edit"):
                    arg_str = arg_str[4:].strip()
                    show_mode = 9 # 编辑模式
                elif arg_str.startswith("创建"):
                    arg_str = (meta.plain_msg.split("创建",1)[1]).strip()
                    show_mode = 9 # 编辑模式
                    should_proc, mode= True, "new"
                elif arg_str.startswith("create"):
                    arg_str = (meta.plain_msg.split("创建",1)[1]).strip()
                    show_mode = 9 # 编辑模式
                    should_proc, mode= True, "new"
        assert (not should_proc) or mode
        hint = (mode, arg_str, show_mode)
        return should_proc, should_pass, hint

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        # 检测是否为群内
        if meta.group_id:
            port = GroupMessagePort(meta.group_id)
            database = self.bot.data_manager.get_data(DC_GROUPCONFIG,[meta.group_id,"query_database"],default_val="DND5E")
        else:
            port = PrivateMessagePort(meta.user_id)
            # 私聊优先使用用户私设的 query_database（支持私聊切换模式），回退到全局私聊默认
            database = self.bot.data_manager.get_data(DC_USER_DATA, [meta.user_id, "query_database"], default_val=None)
            if not database:
                database = self.bot.cfg_helper.get_config(CFG_QUERY_PRIVATE_DATABASE)[0]
        source_port = MessagePort(meta.group_id, meta.user_id)
        mode: Literal["query", "search", "select", "flip_page", "editing", "new", "feedback", "redirect"] = hint[0]
        arg_str: str = hint[1]
        show_mode: int = hint[2] #if meta.group_id else 1
        feedback: str = ""

        # 私设查询库
        if meta.group_id and self.bot.data_manager.get_data(DC_GROUPCONFIG,[meta.group_id,"query_homebrew"],default_val=False):
            homebrew_database = "HB" + meta.group_id
            if homebrew_database not in CONNECTED_QUERY_DATABASES:
                homebrew_path:str = DATA_PATH + "/QueryData/Homebrew/" + homebrew_database + ".db"
                if os.path.exists(homebrew_path):
                    connect_query_database(homebrew_path)
                else:
                    homebrew_database = ""
        else:
            homebrew_database = ""

        # 判断功能开关
        try:
            assert (int(self.bot.cfg_helper.get_config(CFG_QUERY_ENABLE)[0]) != 0)
        except AssertionError:
            feedback = self.bot.loc_helper.format_loc_text(LOC_FUNC_DISABLE, func=self.readable_name)
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]

        # 处理指令
        if not arg_str and (mode == "query" or mode == "search"):
            feedback = self.get_state()
        elif mode == "query" or mode == "search":
            if database not in CONNECTED_QUERY_DATABASES.keys():
                feedback = "未加载的数据库。"
            else:
                feedback = self.query_info(database, homebrew_database, arg_str, source_port, search_mode=( 0 if mode == "query" else 1), show_mode=show_mode)
                if feedback:
                    feedback = self.format_loc(LOC_QUERY_RESULT, result = feedback)
                else:
                    feedback = self.format_loc(LOC_QUERY_NO_RESULT)
        elif mode == "select":
            record = self.record_dict[source_port]
            if record.filter_mode == 0:
                page_item_num = MAX_QUERY_CANDIDATE_NUM if record.mode != 0 else MAX_QUERY_CANDIDATE_SIMPLE_NUM
                index = int(arg_str) # + (record.page-1) * page_item_num
                record.time = get_current_date_raw()  # 更新记录有效期
                if index >= record.length:
                    feedback = self.format_loc(LOC_QUERY_NO_RESULT)
                else:
                    if record.edit_flag:
                        feedback = record.choose_edit_target(index)
                    else:
                        item = record.data[index]
                        feedback = self.format_loc(LOC_QUERY_RESULT, result = self.query_feedback(database, homebrew_database,item, source_port))
            else:
                index = int(arg_str)
                if index >= len(record.catalogue_list.keys()):
                    feedback = self.format_loc(LOC_QUERY_NO_RESULT)
                else:
                    record.select_catalogue(list(record.catalogue_list.keys())[index])
                    page_item_num = MAX_QUERY_CANDIDATE_NUM if record.mode != 0 else MAX_QUERY_CANDIDATE_SIMPLE_NUM
                    record.time = get_current_date_raw()  # 更新记录有效期
                    show_result: List[QueryData] = record.data[:page_item_num]
                    if record.length == 0:
                        self.format_loc(LOC_QUERY_NO_RESULT)
                    elif record.length == 1:
                        feedback = self.format_loc(LOC_QUERY_RESULT, result = self.format_item_feedback(show_result[0]))
                    else:
                        feedback = self.format_loc(LOC_QUERY_RESULT, result = self.format_items_list_feedback(show_result))
                    if record.length > page_item_num:
                        feedback += "\n" + self.format_loc(LOC_QUERY_MULTI_RESULT_PAGE, page_cur=1,
                                                           page_total=record.length // page_item_num + 1)
        elif mode == "flip_page":
            record = self.record_dict[source_port]
            next_page = (arg_str == "+")
            feedback, cur_page = self.flip_page(record, next_page)
            self.record_dict[source_port].page = cur_page
        elif mode == "editing":
            try:
                index: int = int(arg_str)
                self.record_dict[source_port].edit_index = index
                feedback = self.record_dict[source_port].data[0].get(index)
                if not feedback:
                    feedback = "[原文为空]"
                else:
                    feedback = feedback.replace("''","'")
            except ValueError:
                feedback = "编辑失败，未知原因"
                pass
        elif mode == "new":
            data = ["","","","","","","",""]
            index: int = 0
            for _data in arg_str.split("#"):
                data[index] = _data.strip()
                index += 1
                if index >= 8:
                    break
            query_data: QueryData = QueryData(data,database=database)
            query_data.data_extend()
            self.record_dict[source_port] = QueryRecord([query_data],database,get_current_date_raw(), 1)
            self.record_dict[source_port].mode = show_mode
            self.record_dict[source_port].edit_flag = True
            feedback = self.record_dict[source_port].choose_edit_target(0)
            self.record_dict[source_port].edit_new = True
        elif mode == "redirect":
            if show_mode == 9:# 删除重定向
                data = DATABASE_CURSOR[database].execute("SELECT {0} FROM redirect WHERE 名称 Like '{1}'".format(QUERY_REDIRECT_FIELD,arg_str.replace("'","''")))
                query_data: list = []
                for _data in data:
                    query_data.append(_data[1])
                if len(query_data) > 0:
                    DATABASE_CURSOR[database].execute("DELETE FROM redirect WHERE 名称 Like '{0}'".format(arg_str.replace("'","''")))
                    CONNECTED_QUERY_DATABASES[database].commit()
                    feedback = "已删除重定向: " + arg_str + " -> " + "/".join(query_data)
                else:
                    feedback = self.format_loc(LOC_QUERY_NO_RESULT)
            elif show_mode == 8:# 创建重定向
                if "=" in arg_str:
                    arg = arg_str.split("=")
                    name_list = [name.strip() for name in arg[0].split("/")]
                    redirect_list = [redirect.strip() for redirect in arg[1].split("/")]
                    cmd: list[tuple] = []
                    if len(name_list) == 1 or len(redirect_list) == 1:
                        for name in name_list:
                            for redirect in redirect_list:
                                if name != redirect and name != "" and redirect != "":
                                    cmd.append((name,redirect))
                        if len(cmd) != 0:
                            DATABASE_CURSOR[database].executemany("INSERT INTO redirect VALUES(?,?)",cmd)
                            CONNECTED_QUERY_DATABASES[database].commit()
                            feedback = "已创建重定向: " + "/".join(name_list) + " -> " + "/".join(redirect_list)
                        else:
                            feedback = "无法创建重定向：内容有误"
                    else:
                        feedback = "无法创建重定向：无法创建N->N的重定向"
                else:
                    feedback = "无法创建重定向：错误的格式"
            elif len(arg_str) > 0:
                feedback = ""
                # 显示所有 重定向 → XX
                data = DATABASE_CURSOR[database].execute("SELECT {0} FROM redirect WHERE 名称 == '{1}'".format(QUERY_REDIRECT_FIELD,arg_str.replace("'","''")))
                query_data: list = []
                for _data in data:
                    query_data.append(_data[1])
                if len(query_data) > 0:
                    feedback += "以下是 " + arg_str + " 可重定向到的目标条目：\n" + " , ".join(query_data)
                # 显示所有 XX → 重定向
                data = DATABASE_CURSOR[database].execute("SELECT {0} FROM redirect WHERE 重定向 == '{1}'".format(QUERY_REDIRECT_FIELD,arg_str.replace("'","''")))
                query_data: list = []
                for _data in data:
                    query_data.append(_data[0])
                if len(query_data) > 0:
                    feedback += "以下是重定向到 " + arg_str + " 的同义词：\n" + " , ".join(query_data)
                if len(feedback) == 0:
                    feedback = self.format_loc(LOC_QUERY_NO_RESULT)
            else:
                feedback = "重定向删改"\
                    "。重定向创建 [名称]=[对象] 创建一个1->1的重定向"\
                    "。重定向创建 [名称]/[名称]/...=[对象] 创建一组N->1的重定向"\
                    "。重定向创建 [名称]=[对象]/[对象]/... 创建一组1->N的消歧义重定向"\
                    "。重定向删除 [名称] 删除一个重定向"\
                    "。重定向 [名称/对象] 查阅已有的重定向"
        elif mode == "database":
            if arg_str.strip() == "" and show_mode != 5:
                show_mode = 0
            # 非管理员/骰主不允许进行数据库管理（加载/卸载/创建/导入），但允许查看列表
            if show_mode in (1, 2, 3, 4) and meta.permission < 3:
                feedback = "权限不足：需要3级权限（骰管理/骰主）才能管理查询数据库。"
                return [BotSendMsgCommand(self.bot.account, feedback, [port])]
            if show_mode == 1:# 加载数据库
                database = arg_str.strip().upper()
                database_file_path = DATA_PATH+"/QueryData/"+database+".db"
                if database in CONNECTED_QUERY_DATABASES.keys():
                    feedback = f"{database}.db 已经被加载过了，无需再次加载。"
                else:
                    feedback = connect_query_database(database_file_path)
                    if len(feedback) == 0:
                        feedback = f"已载入 {database}.db。"
            elif show_mode == 2:# 卸载数据库
                database = arg_str.strip().upper()
                database_file_path = DATA_PATH+"/QueryData/"+database+".db"
                if database in CONNECTED_QUERY_DATABASES.keys():
                    disconnect_query_database(database)
                    feedback = f"已卸载 {database}.db，您现在可以手动删除对应数据库来防止重启后被再次自动加载。"
                elif os.path.exists(database_file_path):
                    feedback = f"未加载 {database}.db。"
                else:
                    feedback = f"未找到文件{database}.db。"
            elif show_mode == 3:# 创建数据库
                database = arg_str.strip().upper()
                database_file_path = DATA_PATH+"/QueryData/"+database+".db"
                if database in CONNECTED_QUERY_DATABASES.keys():
                    feedback = f" {database}.db 已经处于加载状态，无法再次创建。"
                elif os.path.exists(database_file_path):
                    feedback = f"文件{database}.db 已存在，请使用 。数据库加载 进行加载。"
                else:
                    create_query_database(database_file_path)
                    feedback = connect_query_database(database_file_path)
                    if len(feedback) == 0:
                        feedback = f"已创建并载入{database}.db。"
            elif show_mode == 4:# 导入数据库
                arg_list = arg_str.split(" ")
                if len(arg_list) != 3:
                    feedback = f"请输入正确的指令。"
                else:
                    database = arg_list[0].strip().upper()
                    xlsx_mode = arg_list[1].strip()
                    file_path = arg_list[2].strip()
                    if xlsx_mode in ["0","1","2"]:
                        database_file_path = DATA_PATH+"/QueryData/"+database+".db"
                        if database in CONNECTED_QUERY_DATABASES.keys():
                            load_dir = DATA_PATH+"/ExcelData/"+file_path
                            if os.path.isdir(load_dir):
                                try:
                                    for inner_path,inner_dirs,file_names in os.walk(load_dir):
                                        for file_name in file_names:
                                            if file_name.endswith(".xlsx"):
                                                inner_full_path = os.path.join(inner_path, file_name)
                                                load_data_from_xlsx_to_sqlite(inner_full_path, database_file_path,int(xlsx_mode)) # 0 旧版梨骰数据
                                    feedback += f"已将 ExcelData/{file_path}下的全部xlsx文件载入至 {database}.db。"
                                except FileNotFoundError as e:  # 文件夹不存在
                                    feedback += f"读取 ExcelData/{file_path} 时遇到错误: {e}"
                            else:
                                file_full_path = DATA_PATH+"/ExcelData/"+file_path
                                if os.path.exists(file_full_path):
                                    try:
                                        load_data_from_xlsx_to_sqlite(file_full_path, database_file_path,int(xlsx_mode)) # 0 旧版梨骰数据
                                        feedback += f"已将 ExcelData/{file_path}文件载入至 {database}.db。"
                                    except FileNotFoundError as e:  # 文件夹不存在
                                        feedback += f"读取 ExcelData/{file_path} 时遇到错误: {e}"
                                elif os.path.exists(file_full_path+".xlsx"):
                                    file_full_path = file_full_path+".xlsx"
                                    try:
                                        load_data_from_xlsx_to_sqlite(file_full_path, database_file_path,int(xlsx_mode)) # 0 旧版梨骰数据
                                        feedback += f"已将 ExcelData/{file_path}文件载入至 {database}.db。"
                                    except FileNotFoundError as e:  # 文件夹不存在
                                        feedback += f"读取 ExcelData/{file_path} 时遇到错误: {e}"
                                else:
                                    feedback += f"你输入的 ExcelData/{file_path} 既不是文件夹也不是xlsx文件"
                        else:
                            feedback = f"未加载 {database}.db，请先创建或加载后再进行此操作。"
                    else:
                        feedback = f"请输入正确的模式值：\n0:旧版梨骰查询资料表\n1:新式梨骰查询表\n2:新式梨骰私设表"
            elif show_mode == 5:# 显示数据库
                feedback = "目前已加载以下数据库（不包含私设数据库）：\n  -"+"\n  -".join([key for key in CONNECTED_QUERY_DATABASES.keys() if not key.startswith("HB")])
            else:
                feedback = "数据库编辑"\
                    "。数据库创建 [名称] 创建一个新的数据库\n"\
                    "。数据库列表 查看全部已加载的数据库\n"\
                    "。数据库加载 [名称] 加载一个已有数据库\n"\
                    "。数据库卸载 [名称] 卸载一个已有数据库\n"\
                    "。数据库导入 [名称] [模式] [文件相对路径(需后缀)] \n将ExcelData的一个xlsx文件/文件夹中的全部xlsx导入数据库，模式0为旧版梨骰查询资料表，1为新式梨骰查询表，2为新式梨骰私设表"
        elif mode == "feedback":
            feedback = arg_str
        else:
            raise NotImplementedError()

        # 尝试清理过期的查询记录
        self.record_clean_flag += 1
        if self.record_clean_flag >= RECORD_CLEAN_FREQ:
            self.record_clean_flag = 0
            self.clean_records()
        
        #分割显示查询结果
        command = []
        feedback_superlines = feedback.split("\n\n")
        for superline in feedback_superlines:
            superline_lines = superline.split("\n")
            if len(superline_lines) >= 20:
                #command = [self.send_forward_msg_group(self.bot, self.bot.account, meta.group_id, "超长查询", feedback.split("\n\n"))]
                superline_length = len(superline_lines)
                for index in range(0,math.ceil(superline_length/20)):
                    content: str = ""
                    if (index + 1) * QUERY_SPLIT_LINE_LEN >= superline_length:
                        content = "\n".join(superline_lines[index*QUERY_SPLIT_LINE_LEN:superline_length])
                    else:
                        content = "\n".join(superline_lines[index*QUERY_SPLIT_LINE_LEN:(index + 1)*QUERY_SPLIT_LINE_LEN])
                    if len(content) > 0:
                        command.append(content)
            elif len(superline) > 0:
                command.append(superline)
        if len(command) >= 4:
            return [BotSendForwardMsgCommand(self.bot.account, "查询系统", command, [port])]
        elif len(command) >= 1:
            return [BotSendMsgCommand(self.bot.account, line, [port]) for line in command]
        else:
            return []

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword in ["查询", "搜索", "检索", "q", "s"]:
            help_str = "查询资料: .查询 查询目标"\
                    "\n查询指令支持部分匹配, 可用空格区分多个关键字"\
                    "\n可以用搜索指令来匹配词条内容(而不是仅匹配关键字)"\
                    "\n若有多条可能的结果, 可以通过查询或搜索后直接输入序号查询, 输入+或-可以翻页" \
                    "\n可以用q作为查询(query)的缩写, 或用s作为检索(search)的缩写" \
                    "\n你还可以使用#来进行仅标签、来源、类型的包含搜索，单个tag可以用/进行多tag的或搜索" \
                    "\n&同理，只不过&为分类，且为相等搜索。" \
                    "\n示例:"\
                    "\n.查询 借机攻击"\
                    "\n.查询 #法师 #6环"\
                    "\n.查询 &法术 #PHB/XGE/TCE"\
                    "\n.查询 #战士 &子职业 #PHB/XGE/TCE"\
                    "\n.检索 长弓"\
                    "\n.检索 火焰 敏捷豁免 d6 #3环 #塑能"
            return help_str
        return ""

    def get_description(self) -> str:
        return ".查询 根据关键字查找资料 .搜索 根据关键字和内容查找资料"

    def query_info(self, database: str, homebrew_database: str, query_keywords: str, port: MessagePort, search_mode: int, show_mode: int = 0) -> str:
        """
        查询信息, 返回输出给用户的字符串, 若给出选项将会记录信息以便响应用户之后的快速查询.
        search_mode != 0则使用全文查找
        """
        # 清空过往记录
        if port in self.record_dict:
            del self.record_dict[port]
        # 编辑模式
        if show_mode == 9:
            edit_flag = True
            show_mode = 0
        else:
            edit_flag = False
        # 找到搜索候选
        try:
            poss_result = self.query_item(database, homebrew_database if not edit_flag else "", query_keywords, search_mode)
        except QueryError:
            return self.format_loc(LOC_QUERY_TOO_MUCH_RESULT)
        poss_result_num: int = len(poss_result)

        feedback: str = ""
        poss_result_num = len(poss_result)
        # 处理
        if not poss_result or poss_result_num == 0:  # 找不到结果
            return ""
        elif poss_result_num == 1:  # 找到唯一结果
            if edit_flag:
                self.record_dict[port] = QueryRecord(poss_result, database, get_current_date_raw(), poss_result_num)
                self.record_dict[port].show_mode = show_mode
                self.record_dict[port].edit_flag = edit_flag
                feedback = self.record_dict[port].choose_edit_target(0)
            else:
                feedback = self.query_feedback(database,homebrew_database,poss_result[0],port)
        else:  # len(poss_result) > 1  找到多个结果, 记录当前信息并提示用户选择
            # 记录当前信息以备将来查询或编辑
            self.record_dict[port] = QueryRecord(poss_result, database, get_current_date_raw(), poss_result_num)
            self.record_dict[port].show_mode = show_mode
            self.record_dict[port].edit_flag = edit_flag
            page_item_num = MAX_QUERY_CANDIDATE_NUM if show_mode != 0 else MAX_QUERY_CANDIDATE_SIMPLE_NUM
            filter_mode: int = 1 if (poss_result_num >= page_item_num) else 0
            self.record_dict[port].filter_mode = filter_mode
            
            #处理分类
            if self.record_dict[port].filter_mode == 1:
                self.record_dict[port].create_catalogue_list()
            else:
                self.record_dict[port].process_data()
            #以分类模式显示结果
            if self.record_dict[port].filter_mode == 1:
                show_result: List[QueryData] = []
                for key,num in self.record_dict[port].catalogue_list.items():
                    show_result.append(key + " (" + str(num) + ")")
                feedback = self.format_loc(LOC_QUERY_MULTI_RESULT_CATALOGUE) + "\n" + self.format_catalogues_list_feedback(show_result)
            #直接显示结果
            else:
                show_result: List[QueryData] = poss_result[:page_item_num]
                feedback = self.format_items_list_feedback(show_result)
                if poss_result_num > page_item_num:
                    feedback += "\n" + self.format_loc(LOC_QUERY_MULTI_RESULT_PAGE, page_cur=1,
                                                       page_total=poss_result_num // page_item_num + 1)
        return feedback
    
    def command_split(self,keywords: str) -> List[str]:
        """
        处理一遍指令，将更加规范的文本传递给查询
        """
        result_list: List[str] = []
        collect_words: str = ""
        prefix: str = ""
        fine_mode: bool = False
        for key in keywords:
            if not fine_mode and collect_words == "" and key == "\"":
                fine_mode = True
            elif fine_mode and key == "\"":
                fine_mode = False
                if collect_words != "":
                    result_list.append(prefix + collect_words)
                    prefix = ""
            elif not fine_mode and key in ["#","&"]:
                if collect_words.strip():
                    result_list.append(prefix + collect_words.strip())
                collect_words = ""
                prefix = key
            elif not fine_mode and key in [" "]:
                if collect_words.strip():
                    result_list.append(prefix + collect_words.strip())
                collect_words = ""
                prefix = ""
            else:
                collect_words += key
        if fine_mode:
            result_list.append(prefix + collect_words)
        elif collect_words.strip():
            result_list.append(prefix + collect_words.strip())
        return result_list
    
    def query_item(self, database: str, homebrew_database: str, query_keywords: str,search_mode: int = 0) -> List[QueryData]:
        """
        查询的实际处理,会同时处理普通数据库与房规数据库
        """
        poss_result: List[QueryData] = []
        # 如果库不存在直接撤
        if database not in CONNECTED_QUERY_DATABASES.keys():
            return poss_result
        # 分割关键字，这里不检测超出，因为全部由数据库决定
        query_command_list:List[str] = []
        if len(query_keywords) > 0:
            query_command_list = self.command_split(query_keywords)
            if len(query_command_list) == 0:
                return poss_result
        else:
            return poss_result
        # 找到搜索候选
        poss_result = self.search_item(database, query_command_list, search_mode)
        # 找到私设候选（如果开的话）
        if homebrew_database != "":
            homebrew_result = self.search_item(homebrew_database, query_command_list, search_mode)
            for homebrew in homebrew_result[::-1]:
                if len(poss_result) > 0:
                    for poss in poss_result[::-1]:
                        if homebrew.data_name == poss.data_name:
                            poss_result.remove(poss)
                if len(homebrew.data_content.strip()) == 0:
                    homebrew_result.remove(homebrew)
            poss_result = poss_result + homebrew_result
        
        return poss_result

    '''
    def search_item(self,database: str, query_command_list: List[str], search_mode: int = 0) -> List[QueryData]:
        """
        搜索合规的对象
        """
        sql_search_command: str = "Select {0} From {1} Where ".format(QUERY_DATA_FIELD, "data")
        sql_redirect_command: str = "Select {0} From {1} Where ".format(QUERY_REDIRECT_FIELD, "redirect")
        query_sqlcur = DATABASE_CURSOR[database]
        cursor: sqlite3.Cursor
        query_result: List[QueryData] = []
        result_length: int = 0
        # 分割查询指令
        name: str = ""
        condition_list: List[str] = []
        redirect_name_list: List[str] = []
        redirect_condition_list: List[str] = []
        for query_command in query_command_list:
            command_list = [command for command in query_command.split("/")]
            func = ""
            # 预处理指令
            if len(command_list) > 0:
                if len(command_list[0]) == 0:
                    command_list = [query_command]
                    continue
                elif command_list[0][0] in ("#","&"):
                    func = query_command[0]
                    command_list[0] = command_list[0][1:]
            # 添加条件指令
            if len(command_list) > 0:
                if func == "#":
                    cmd = self.generate_search_sql_regexp(command_list,"lower(replace(replace(来源 || '#' || 分类 || '#' || 标签,' ','#'),x'0A','|'))")
                    condition_list.append(cmd)
                    redirect_condition_list.append(cmd)
                elif func == "&":
                    cmd = self.generate_search_sql_in(command_list,"分类")
                    condition_list.append(cmd)
                    redirect_condition_list.append(cmd)
                else:
                    if search_mode == 0:
                        condition_list.append(self.generate_search_sql_regexp(command_list,"lower(replace(replace(名称 || '#' || 英文,' ','#'),x'0A','|'))"))
                        redirect_name_list.append(self.generate_search_sql_regexp(command_list,"名称"))
                        name = name + "".join(command_list)
                    else:
                        condition_list.append(self.generate_search_sql_regexp(command_list,"lower(replace(replace(名称 || 英文 || 来源 || 分类 || 标签 || 内容,' ',''),x'0A','|'))"))
                        redirect_name_list.append(self.generate_search_sql_regexp(command_list,"名称"))
        # 正常查询
        if len(condition_list) > 0:
            sql_condition = sql_search_command + " AND ".join(condition_list)
            # print(sql_condition)
            cursor = query_sqlcur.execute(sql_condition)
            for _data in cursor:
                query_result.append(QueryData(_data))
                result_length += 1
                if result_length > MAX_QUERY_ITEM_NUM:
                    raise QueryError("匹配条目过多，无法查询")
        # 处理重定向
        if len(redirect_name_list) > 0:
            redirect_result: List[Tuple[str]] = []
            sql_condition = sql_redirect_command + " AND ".join(redirect_name_list)
            cursor = query_sqlcur.execute(sql_condition)
            for _data in cursor:
                redirect_result.append(_data)
            if len(redirect_condition_list) > 0:
                sql_condition = " AND " + " AND ".join(redirect_condition_list)
            else:
                sql_condition = ""
            for _redirect in redirect_result:
                cursor = query_sqlcur.execute(sql_search_command + " 名称 = '" + _redirect[1].replace("'","''") + "'"+ sql_condition)
                for _data in cursor:
                    query_result.append(QueryData(_data,_redirect[0]))
                    result_length += 1
                    if result_length > MAX_QUERY_ITEM_NUM:
                        raise QueryError("匹配条目过多，无法查询")
        # 去除重复的条目，并寻找直接确认者,或者同名确认者
        dupe_list: List[str] = []
        new_query_result: List[QueryData] = []
        found_equal: bool = False
        for query_data in query_result:
            if query_data.original_data[0] == name and name != "":
                if not found_equal:
                    dupe_list.clear()
                    new_query_result.clear()
                if query_data.hash_word not in dupe_list:
                    dupe_list.append(query_data.hash_word)
                    new_query_result.append(query_data)
                found_equal = True
            elif not found_equal:
                if query_data.hash_word not in dupe_list:
                    dupe_list.append(query_data.hash_word)
                    new_query_result.append(query_data)
        # 生成额外数据供使用
        for query_data in query_result:
            query_data.data_extend()
        # 查询结束
        return new_query_result
    '''
    def search_item(self,database: str, query_command_list: List[str], search_mode: int = 0) -> List[QueryData]:
        """
        搜索合规的对象
        """
        sql_search_command_prefix: str = "Select * From data Where " #查询指令前缀
        sql_redirect_command_prefix: str = "Select * From redirect Where " #查询指令前缀
        sql_command_suffix: str = "" #" COLLATE NOCASE" #查询指令后缀
        query_sqlcur = DATABASE_CURSOR[database] # 指针
        cursor: sqlite3.Cursor
        query_result: List[QueryData] = []
        result_length: int = 0
        use_redirect: bool = True
        # 分割指令
        # 获取查询指令列表
        complete_name: str = ""
        complete_name_en: str = ""
        can_single_query: bool = True
        condition_type: List[str] = ["名称","英文","来源","分类","标签","内容","全部"]
        condition_list: Dict[str, List[List[str]]] = {}
        # 预处理指令
        for query_command in query_command_list:
            # 确认指令对象
            cmd_target = ["名称","英文"]
            if query_command[0]  == "#":
                cmd_target = ["来源","分类","标签"]
                query_command = query_command[1:]
                can_single_query = False
            elif query_command[0]  == "&":
                cmd_target = ["分类"]
                query_command = query_command[1:]
                can_single_query = False
                '''
            elif "=" in query_command:
                left_word, right_word = query_command.split("=")
                can_single_query = False
                if len(left_word) != 0 and len(right_word) != 0:
                    new_cmd_target = []
                    for word in left_word.split("/"):
                        if word not in condition_type:
                            new_cmd_target.append(word)
                        else:
                            break
                    if len(new_cmd_target) != 0:
                        cmd_target = new_cmd_target
                        query_command = right_word
                '''
            elif search_mode == 1:
                cmd_target = ["全部"]
                can_single_query = False
            command_list = [command for command in query_command.split("/")]
            if len(command_list) == 0:
                continue
            # 添加条件指令
            if tuple(cmd_target) not in condition_list.keys():
                condition_list[tuple(cmd_target)] = []
            condition_list[tuple(cmd_target)].append(command_list)
            if len(command_list) == 1:
                if "名称" in cmd_target:
                    complete_name += command_list[0]
                if "英文" in cmd_target:
                    if complete_name_en != "":
                        complete_name_en += " " + command_list[0]
                    else:
                        complete_name_en += command_list[0]
            else:
                complete_name = ""
                complete_name_en = ""
        # 防止空查询
        condition_size = 0
        for key_list in condition_list.keys():
            condition_size += len(condition_list[key_list])
        if condition_size == 0:
            return []
        # 正常查询
        sql_condition = self.generate_search_conditions(condition_list)
        #print(sql_condition)
        cursor = query_sqlcur.execute(sql_search_command_prefix + sql_condition + sql_command_suffix)
        for _data in cursor:
            query_result.append(QueryData(_data,database=database))
            result_length += 1
            if result_length > MAX_QUERY_ITEM_NUM:
                raise QueryError("匹配条目过多，无法查询")
        # 处理重定向
        if use_redirect:
            redirect_condition_list: Dict[tuple, List[List[str]]] = {
                ("名称",) : []
            }
            sql_condition_list: Dict[tuple, List[List[str]]] = {
                ("名称",) : []
            }
            for key_list in condition_list.keys():
                if "全部" in key_list or "名称" in key_list:
                    redirect_condition_list[("名称",)] += condition_list[key_list]
                else:
                    sql_condition_list[key_list] = condition_list[key_list]
            if len(redirect_condition_list[("名称",)]) != 0:
                redirect_result: List[List[str]] = []
                sql_condition = self.generate_search_conditions(redirect_condition_list)
                cursor = query_sqlcur.execute(sql_redirect_command_prefix + sql_condition + sql_command_suffix)
                for _data in cursor:
                    redirect_result.append([_data[0],_data[1]])
                if len(redirect_condition_list) > 0:
                    for _redirect in redirect_result:
                        sql_condition_list[("名称",)] = [[_redirect[1]]]
                        sql_condition = self.generate_search_conditions(sql_condition_list)
                        cursor = query_sqlcur.execute(sql_search_command_prefix + sql_condition+ sql_command_suffix)
                        for _data in cursor:
                            query_result.append(QueryData(_data,_redirect[0],database))
                            result_length += 1
                            if result_length > MAX_QUERY_ITEM_NUM:
                                raise QueryError("匹配条目过多，无法查询")
        # 去除重复的条目，并寻找直接确认者,或者同名确认者
        dupe_list: List[str] = []
        new_query_result: List[QueryData] = []
        found_equal: bool = False
        for query_data in query_result:
            if can_single_query:
                if complete_name != "" and query_data.original_data[0] == complete_name:
                    if not found_equal:
                        dupe_list.clear()
                        new_query_result.clear()
                    found_equal = True
                    if query_data.hash_word not in dupe_list:
                        dupe_list.append(query_data.hash_word)
                        new_query_result.append(query_data)
                elif complete_name_en != "" and query_data.original_data[0].lower() == complete_name_en:
                    if not found_equal:
                        dupe_list.clear()
                        new_query_result.clear()
                    found_equal = True
                    if query_data.hash_word not in dupe_list:
                        dupe_list.append(query_data.hash_word)
                        new_query_result.append(query_data)
            if not found_equal:
                if query_data.hash_word not in dupe_list:
                    dupe_list.append(query_data.hash_word)
                    new_query_result.append(query_data)
        # 生成额外数据供使用
        for query_data in query_result:
            query_data.data_extend()
        # 查询结束
        return new_query_result

    def query_feedback(self, database: str, homebrew_database: str, item: QueryData, port: MessagePort) -> str:
        """
        生成查询到目标的返回文本，包括处理嵌套查询
        """
        item_lines = item.data_content.splitlines()
        # 处理嵌套查询
        sub_query_items = []
        for index in range(len(item_lines)):
            if item_lines[index].startswith("/"):
                try:
                    command = item_lines[index][1:].lower()
                    extra_command = []
                    # 有一些专用的附加指令
                    if "|" in command:
                        extra_command = command.split("|")
                        command = extra_command.pop(0)
                    extra_items = self.query_item(database, homebrew_database, command)
                    item_lines[index] = "[ " + self.format_items_list_feedback(extra_items,len(sub_query_items)) + " ]"
                    # 处理专用的附加指令
                    for excmd in extra_command:
                        excmd = excmd.strip()
                        # 清理查询条目文本中的特定文本
                        if excmd.startswith("clear") and len(excmd) > 5:
                            item_lines[index] = item_lines[index].replace(excmd[5:].strip(),"")
                        # 展示实际内容
                        elif excmd.startswith("show"):
                            word_limit = 200
                            if len(excmd) > 4:
                                # 只展示部分内容
                                word_limit = int(excmd[4:].strip())
                            new_str:List[str] = []
                            for _index in range(len(extra_items)):
                                if len(extra_items[_index].data_content) > word_limit:
                                    new_str.append(str(len(sub_query_items) + _index) + "." + extra_items[_index].display_name + " : " + extra_items[_index].data_content[:word_limit].replace("\n"," ")+"...")
                                else:
                                    new_str.append(str(len(sub_query_items) + _index) + "." + extra_items[_index].display_name + " : " + extra_items[_index].data_content.replace("\n"," "))
                            item_lines[index] = "\n".join(new_str)
                    sub_query_items += extra_items
                except QueryError:
                    item_lines[index] = self.format_loc(LOC_QUERY_TOO_MUCH_RESULT)
            else:
                item_lines[index] = item_lines[index].strip()
        if len(sub_query_items) > 0:
            # 记录嵌套查询内容
            if port in self.record_dict:
                del self.record_dict[port]
            self.record_dict[port] = QueryRecord(sub_query_items, database, get_current_date_raw(), len(sub_query_items))
        item.data_content = "\n".join(item_lines)
        return self.format_item_feedback(item)

    def generate_search_conditions(self, condition_list: Dict[tuple,List[List[str]]]) -> str:
        results = []
        for key_list in condition_list.keys():
            if len(condition_list[key_list]) != 0:
                    key_results = []
                    if "全部" in key_list:
                        for command in condition_list[key_list]:
                            key_results.append(self.generate_search_sql_regexp(command,"名称||英文||来源||分类||标签||内容"))
                    elif key_list == ["分类"]:
                        for command in condition_list[key_list]:
                            key_results.append(self.generate_search_sql_in(command,"分类"))
                    else:
                        for command in condition_list[key_list]:
                            key_results.append(self.generate_search_sql_regexp(command,"||".join(key_list)))
                    if len(key_results) != 0:
                        results.append("(" + " AND ".join(key_results) + ")")
        return " AND ".join(results)
    
    def generate_search_sql_regexp(self, command_list: List[str], prefix: str = "名称") -> str:
        # 生成正则表达式的condition
        result: List[str] = []
        for command in command_list:
            if command.startswith("-") and len(command) > 1:
                result.append("^(?!.*" + regexp_normalize(command[1:].replace("'","''")) + ")")
            elif command.startswith("=") and len(command) > 1:
                result.append("^" + regexp_normalize(command[1:].replace("'","''")) + "$")
            elif len(command) > 0:
                result.append(regexp_normalize(command.replace("'","''")))
        return prefix + " regexp '" + "|".join(result) + "'"
    
    def generate_search_sql_in(self, command_list: List[str], prefix: str = "来源") -> str:
        # 生成列表中检测的condition
        result: str = ""
        words: List[str] = []
        anti_words: List[str] = []
        for command in command_list:
            if command.startswith("-") and len(command) > 1:
                anti_words.append("'" + command[1:].replace("'","''") + "'")
            elif command.startswith("=") and len(command) > 1:
                words.append("'" + command[1:].replace("'","''") + "'")
            elif len(command) > 0:
                words.append("'" + command.replace("'","''") + "'")
        result = prefix + " in (" + ",".join(words) + ")"
        if len(anti_words) > 0:
            result = "(" + result + "or" + prefix + " not in (" + ",".join(anti_words) + ")"
        return result

    def format_item_feedback(self, item: QueryData) -> str:
        # 最基本的单条目返回文本
        item_content = item.data_content if item.data_content else "[内容为空，等待热心小编补充]"
        item_tag = "\n" + item.data_tag if (item.data_tag and not item.data_tag.startswith("/")) else ""
        item_book = self.format_loc(LOC_QUERY_CELL_BOOK, book=item.data_from) if item.data_from else ""
        item_redirect = self.format_loc(LOC_QUERY_CELL_REDIRECT, redirect=item.redirect_by) if item.redirect_by else ""
        return self.format_loc(LOC_QUERY_SINGLE_RESULT, keyword=item.data_name, en_keyword=item.data_name_en, content=item_content, tag=item_tag, book=item_book, redirect=item_redirect)

    def format_item_redirects_feedback(self, item: QueryData) -> str:
        # 最基本的单条目返回文本
        item_content = item.data_content if item.data_content else "[内容为空，等待热心小编补充]"
        item_tag = "\n" + item.data_tag if (item.data_tag and not item.data_tag.startswith("/")) else ""
        item_book = self.format_loc(LOC_QUERY_CELL_BOOK, book=item.data_from) if item.data_from else ""
        item_redirect = self.format_loc(LOC_QUERY_CELL_REDIRECT, redirect=item.redirect_by) if item.redirect_by else ""
        return self.format_loc(LOC_QUERY_SINGLE_RESULT, keyword=item.data_name, en_keyword=item.data_name_en, content=item_content, tag=item_tag, book=item_book, redirect=item_redirect)

    @staticmethod
    def format_items_list_feedback(items: List[QueryData],start_index: int = 0):
        # 多个结果，要求用户从结果中选择其一的返回文本
        return ", ".join((f"{start_index+index}.{item.display_name}" for index, item in enumerate(items)))

    @staticmethod
    def format_catalogues_list_feedback(catalogues: List[str],start_index: int = 0):
        # 过多结果，要求用户从分类中选择其一的返回文本
        return "\n".join((f"{start_index+index}.{item}" for index, item in enumerate(catalogues)))

    def flip_page(self, record: QueryRecord, next_page: bool) -> Tuple[str, int]:
        def get_feedback(page) -> str:
            start_index = (page - 1) * page_item_num
            end_index = start_index + page_item_num
            #uuids = record.uuid_list[index:index + page_item_num]
            #items = [self.item_uuid_dict[uuid] for uuid in uuids]
            items = []
            index: int = 0
            for item in record.data:
                if index >= start_index and index <= end_index:
                    items.append(item)
                index += 1
            return self.format_items_list_feedback(items,start_index)

        cur_page = record.page
        page_item_num = MAX_QUERY_CANDIDATE_NUM if record.mode != 0 else MAX_QUERY_CANDIDATE_SIMPLE_NUM

        total_page = record.length // page_item_num + 1
        if not next_page:
            if cur_page == 1:
                feedback = self.format_loc(LOC_QUERY_MULTI_RESULT_PAGE_UNDERFLOW)
            else:
                cur_page = cur_page - 1
                feedback = get_feedback(cur_page)
        else:
            if cur_page == total_page:
                feedback = self.format_loc(LOC_QUERY_MULTI_RESULT_PAGE_OVERFLOW)
            else:
                cur_page = cur_page + 1
                feedback = get_feedback(cur_page)
        if record.length > page_item_num:
            feedback += "\n" + self.format_loc(LOC_QUERY_MULTI_RESULT_PAGE, page_cur=cur_page, page_total=total_page)
        return feedback, cur_page

    def clean_records(self):
        """清理过期的查询指令"""
        invalid_ports: Set[MessagePort] = set()
        for port, record in self.record_dict.items():
            if get_current_date_raw() - record.time > datetime.timedelta(seconds=RECORD_RESPONSE_TIME):
                invalid_ports.add(port)
        for port in invalid_ports:
            del self.record_dict[port]

    def get_state(self) -> str:
        feedback: str
        if CONNECTED_QUERY_DATABASES:
            feedback = f"已载入{len(CONNECTED_QUERY_DATABASES)}个数据库!"
        else:
            feedback = f"尚未加载任何数据库"
        return feedback

