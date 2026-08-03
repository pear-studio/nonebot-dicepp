from typing import List, Tuple, Dict, Optional, Set, Literal, Any
import os
import datetime
#import openpyxl
import math
#import random
# from openpyxl.utils import get_column_letter

from plugins.DicePP.core.bot import Bot
from plugins.DicePP.core.command.const import *
from plugins.DicePP.core.command import UserCommandBase, custom_user_command
from plugins.DicePP.core.command import BotCommandBase, BotSendMsgCommand, BotSendForwardMsgCommand
from plugins.DicePP.core.communication import MessageMetaData, MessagePort, PrivateMessagePort, GroupMessagePort
from plugins.DicePP.core.localization import LOC_FUNC_DISABLE
from plugins.DicePP.core.config.basic import Paths
from plugins.DicePP.core.data.query_store import (
    QueryStoreError,
)
from plugins.DicePP.core.query_utils import command_split
from plugins.DicePP.utils.time import get_current_date_raw

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
LOC_QUERY_READ_ONLY = "query_read_only"

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
RECORD_CLEAN_FREQ = 50  # 每隔多少次查询指令尝试清理一次查询记录

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

class QueryError(Exception):
    """
    因为查询产生的异常, 说明操作失败的原因, 应当在上一级捕获
    """
    def __init__(self, info: str):
        self.info = info

    def __str__(self):
        return f"[Query] [Error] {self.info}"


def _is_legacy_query_management_command(msg_str: str) -> bool:
    """识别已停用的 Bot 端资料管理入口。"""
    lowered = msg_str.lower()
    if lowered.startswith((".重定向", ".redirect", ".数据库", ".database")):
        return True
    # 旧编辑入口只在完整命令名下生效；`.q`/`.s` 的英文查询词不应被误判。
    for key in ("查询", "query", "搜索", "检索", "search"):
        prefix = f".{key}"
        if lowered.startswith(prefix):
            argument = lowered[len(prefix):].strip()
            first_token = argument.split(maxsplit=1)[0] if argument else ""
            return first_token in ("编辑", "edit", "创建", "create")
    return False


@custom_user_command(
    readable_name="查询资料只读提示",
    priority=-1,
    group_only=False,
    flag=DPP_COMMAND_FLAG_QUERY,
)
class QueryLegacyManagementCommand(UserCommandBase):
    """在 `.r` 等宽前缀命令之前拦截旧资料管理指令。"""

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        return _is_legacy_query_management_command(msg_str), False, None

    async def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        feedback = self.bot.loc_helper.format_loc_text(LOC_QUERY_READ_ONLY)
        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        return ""

    def get_description(self) -> str:
        return ""


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
        reg_loc(
            LOC_QUERY_READ_ONLY,
            "Bot 端资料管理已停用，当前仅支持只读查询。",
            "用户尝试使用已停用的 Bot 端资料管理指令时的提示",
        )

        #已弃用，请使用mode_command那边的CFG。

    async def delay_init(self) -> List[str]:
        # 从本地文件中读取数据库
        data_path_list: List[str] = [self.bot.config.query.data_path]
        init_info: List[str] = [""]
        for i, path in enumerate(data_path_list):
            if path.startswith("./"):  # ./开头路径相对于 Paths.CONTENT_DIR 解析
                data_path_list[i] = str(Paths.CONTENT_DIR / path[2:])
        for data_path in data_path_list:
            await self.bot.db.query.connect_path(data_path)
        init_info[0] = self.get_state()
        return init_info

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = False
        should_pass: bool = False
        mode: Optional[Literal["query", "search", "select", "flip_page", "read_only"]] = None
        arg_str: str = ""

        # 响应交互查询指令
        port = MessagePort(meta.group_id, meta.user_id)
        if port in self.record_dict:
            record = self.record_dict[port]
            msg_word = msg_str.strip()
            if get_current_date_raw() - record.time < datetime.timedelta(seconds=RECORD_RESPONSE_TIME):
                try:
                    target_index: int = int(msg_str)
                    should_proc = (
                        0 <= target_index <= record.length
                        if record.filter_mode == 0
                        else 0 <= target_index <= record.cata_length
                    )
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

        # 旧管理入口仍由本命令接住，避免被当作查询词。
        if not should_proc and _is_legacy_query_management_command(msg_str):
            should_proc, mode = True, "read_only"

        # 常规查询指令
        for key in ["查询", "query", "q"]:
            if not should_proc and msg_str.startswith(f".{key}"):
                arg_str = msg_str[1 + len(key):].strip()
                mode = "query"
                should_proc = True
        for key in ["搜索", "检索", "search", "s"]:
            if not should_proc and msg_str.startswith(f".{key}"):
                arg_str = msg_str[1 + len(key):].strip()
                mode = "search"
                should_proc = True
        assert (not should_proc) or mode
        hint = (mode, arg_str)
        return should_proc, should_pass, hint

    async def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        # 检测是否为群内
        if meta.group_id:
            port = GroupMessagePort(meta.group_id)
            row = await self.bot.db.group_config.get(meta.group_id)
            database = self.bot.config.mode.default
            if row and row.data:
                database = row.data.get("query_database", self.bot.config.mode.default)
        else:
            port = PrivateMessagePort(meta.user_id)
            # 私聊优先使用用户私设的 query_database（支持私聊切换模式），回退到全局私聊默认
            user_row = await self.bot.db.user_stat.get(meta.user_id)
            database = None
            if user_row and user_row.data:
                database = user_row.data.get("query_database")
            if not database:
                database = self.bot.config.query.private_database
        source_port = MessagePort(meta.group_id, meta.user_id)
        mode: Literal["query", "search", "select", "flip_page", "read_only"] = hint[0]
        arg_str: str = hint[1]
        feedback: str = ""

        # 私设查询库
        query_homebrew = False
        if meta.group_id:
            group_row = await self.bot.db.group_config.get(meta.group_id)
            if group_row and group_row.data:
                query_homebrew = group_row.data.get("query_homebrew", False)
        if meta.group_id and query_homebrew:
            homebrew_database = "HB" + meta.group_id
            if not self.bot.db.query.has_database(homebrew_database):
                homebrew_path: str = os.path.join(self.bot.data_path, "QueryHomebrew", homebrew_database + ".db")
                if os.path.exists(homebrew_path):
                    await self.bot.db.query.connect_path(homebrew_path)
                else:
                    homebrew_database = ""
        else:
            homebrew_database = ""

        # 判断功能开关
        if not self.bot.config.query.enable:
            feedback = self.bot.loc_helper.format_loc_text(LOC_FUNC_DISABLE, func=self.readable_name)
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]

        # 处理指令
        if not arg_str and (mode == "query" or mode == "search"):
            feedback = self.get_state()
        elif mode == "query" or mode == "search":
            if not self.bot.db.query.has_database(database):
                feedback = "未加载的数据库。"
            else:
                feedback = await self.query_info(
                    database,
                    homebrew_database,
                    arg_str,
                    source_port,
                    search_mode=(0 if mode == "query" else 1),
                )
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
                    item = record.data[index]
                    result = await self.query_feedback(database, homebrew_database, item, source_port)
                    feedback = self.format_loc(LOC_QUERY_RESULT, result=result)
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
        elif mode == "read_only":
            feedback = self.format_loc(LOC_QUERY_READ_ONLY)
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
                    "\nBot 端资料管理已停用，当前仅支持只读查询"\
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

    async def query_info(
        self,
        database: str,
        homebrew_database: str,
        query_keywords: str,
        port: MessagePort,
        search_mode: int,
    ) -> str:
        """
        查询信息, 返回输出给用户的字符串, 若给出选项将会记录信息以便响应用户之后的快速查询.
        search_mode != 0则使用全文查找
        """
        # 清空过往记录
        if port in self.record_dict:
            del self.record_dict[port]
        # 找到搜索候选
        try:
            poss_result = await self.query_item(
                database,
                homebrew_database,
                query_keywords,
                search_mode,
            )
        except QueryError:
            return self.format_loc(LOC_QUERY_TOO_MUCH_RESULT)
        poss_result_num: int = len(poss_result)

        feedback: str = ""
        poss_result_num = len(poss_result)
        # 处理
        if not poss_result or poss_result_num == 0:  # 找不到结果
            return ""
        elif poss_result_num == 1:  # 找到唯一结果
            feedback = await self.query_feedback(database, homebrew_database, poss_result[0], port)
        else:  # len(poss_result) > 1  找到多个结果, 记录当前信息并提示用户选择
            # 记录当前信息以备后续选择或翻页
            self.record_dict[port] = QueryRecord(poss_result, database, get_current_date_raw(), poss_result_num)
            page_item_num = MAX_QUERY_CANDIDATE_SIMPLE_NUM
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
    
    def command_split(self, keywords: str) -> List[str]:
        return command_split(keywords)
    
    async def query_item(
        self,
        database: str,
        homebrew_database: str,
        query_keywords: str,
        search_mode: int = 0,
    ) -> List[QueryData]:
        poss_result: List[QueryData] = []
        if not self.bot.db.query.has_database(database):
            return poss_result
        query_command_list: List[str] = []
        if len(query_keywords) > 0:
            query_command_list = self.command_split(query_keywords)
            if len(query_command_list) == 0:
                return poss_result
        else:
            return poss_result
        poss_result = await self.search_item(
            database, query_command_list, search_mode, homebrew_database,
        )
        return poss_result

    async def search_item(
        self, database: str, query_command_list: List[str],
        search_mode: int = 0, homebrew_database: str = "",
    ) -> List[QueryData]:
        """搜索合规的对象（适配层：调用 QueryStore.search() + dict→QueryData）。"""
        databases = [database]
        if homebrew_database:
            databases.append(homebrew_database)

        try:
            result = await self.bot.db.query.search(
                databases=databases,
                query_tokens=query_command_list,
                fulltext=(search_mode == 1),
                limit=MAX_QUERY_ITEM_NUM,
            )
        except QueryStoreError:
            raise QueryError("匹配条目过多，无法查询")

        results: List[QueryData] = []
        for r in result["results"]:
            qd = QueryData(
                [r["name"], r["name_en"], r["source"], r["catalogue"], r["tag"], r["content"]],
                redirect_by=r.get("redirect_by", ""),
                database=database,
            )
            qd.data_extend()
            results.append(qd)
        return results

    async def query_feedback(
        self,
        database: str,
        homebrew_database: str,
        item: QueryData,
        port: MessagePort,
    ) -> str:
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
                    extra_items = await self.query_item(database, homebrew_database, command)
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
        dbs = self.bot.db.query.list_databases()
        if dbs:
            feedback = f"已载入{len(dbs)}个数据库!"
        else:
            feedback = f"尚未加载任何数据库"
        return feedback

