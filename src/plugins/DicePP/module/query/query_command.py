from typing import List, Tuple, Dict, Optional, Set, Literal, Any
import datetime
import math

from plugins.DicePP.core.bot import Bot
from plugins.DicePP.core.command.const import *
from plugins.DicePP.core.command import UserCommandBase, custom_user_command
from plugins.DicePP.core.command import BotCommandBase, BotSendMsgCommand, BotSendForwardMsgCommand
from plugins.DicePP.core.communication import MessageMetaData, MessagePort, PrivateMessagePort, GroupMessagePort
from plugins.DicePP.core.config.basic import Paths
from plugins.DicePP.core.data.query_store import (
    QueryStoreError,
)
from plugins.DicePP.core.query_utils import command_split
from plugins.DicePP.module.common.mode_defs import query_database_for_mode
from plugins.DicePP.utils.time import get_current_date_raw

LOC_QUERY_RESULT = "query_result"
LOC_QUERY_SINGLE_RESULT = "query_single_result"
LOC_QUERY_MULTI_RESULT = "query_multi_result"
LOC_QUERY_MULTI_RESULT_ITEM = "query_multi_result_item"
LOC_QUERY_MULTI_RESULT_PAGE = "query_multi_result_page"
LOC_QUERY_MULTI_RESULT_PAGE_UNDERFLOW = "query_multi_result_page_underflow"
LOC_QUERY_MULTI_RESULT_PAGE_OVERFLOW = "query_multi_result_page_overflow"
LOC_QUERY_NO_RESULT = "query_no_result"
LOC_QUERY_TOO_MUCH_RESULT = "query_too_much_result"
LOC_QUERY_KEY_NUM_EXCEED = "query_key_num_exceed"
LOC_QUERY_CELL_BOOK = "query_cell_book"
LOC_QUERY_CELL_REDIRECT = "query_cell_redirect"
LOC_QUERY_READ_ONLY = "query_read_only"
LOC_QUERY_INVALID_FORMAT = "query_invalid_format"
LOC_QUERY_OUTDATED_CONTENT = "query_outdated_content"

QUERY_ITEM_FIELD_DESC_DEFAULT_LEN = 20  # 默认用前多少个字符作为默认Description
QUERY_SPLIT_LINE_LEN = 20  # 默认如何分割过长查询文本

MAX_QUERY_KEY_NUM = 5  # 最多能同时用多少个查询关键字
MAX_QUERY_CANDIDATE_SIMPLE_NUM = 30  # 简略查询时一页最多能同时展示多少个条目
MAX_QUERY_ITEM_NUM = 1000  # 最多能查询多少条目
RECORD_RESPONSE_TIME = 60  # 至多响应多久以前的查询指令, 多余的将被清理, 单位为秒
RECORD_CLEAN_FREQ = 50  # 每隔多少次查询指令尝试清理一次查询记录

class QueryData:
    def __init__(self,data_str: List[str],redirect_by: str = "",database: str = "DND5E"):
        """单条被查询的数据"""
        self.original_data = data_str
        self.redirect_by = redirect_by
        self.database = database

    def data_extend(self):
        self.data_name = self.original_data[0]
        self.data_name_en = self.original_data[1]
        self.data_from = self.original_data[2]
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
                    self.data[_index].display_name = (
                        self.data[_index].data_name
                        + "("
                        + self.data[_index].data_from
                        + ")"
                    )
        # WIP 当父关键词出现的时候不显示子关键词
        #name_list: list = [_data[0] for _data in self.data]
        #unique_dict

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
        reg_loc(LOC_QUERY_INVALID_FORMAT, "查询格式错误。", "查询参数使用了不支持的格式")
        reg_loc(
            LOC_QUERY_OUTDATED_CONTENT,
            "数据库“{database}”中的词条“{keyword}”包含过时的查询逻辑，暂时无法使用。请让管理员在 Dashboard 中规范这个数据库。",
            "查询结果仍包含旧嵌套查询时返回；database: 数据库名，keyword: 词条名",
        )

    async def delay_init(self) -> List[str]:
        # 从本地文件中读取数据库
        init_info: List[str] = [""]
        await self.bot.db.query.connect_path(str(Paths.CONTENT_QUERIES_DIR))
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
                    should_proc = 0 <= target_index <= record.length
                    mode, arg_str = "select", msg_str
                except ValueError:
                    pass
                # 翻页
                if not should_proc:
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
        # 群聊和私聊模式都统一保存在 group_config；私聊用独立键空间。
        config_key = meta.group_id or f"__user__{meta.user_id}"
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        row = await self.bot.db.group_config.get(config_key)
        database = query_database_for_mode(self.bot.config.default_mode)
        if row and row.data:
            database = row.data.get("query_database", database)
        source_port = MessagePort(meta.group_id, meta.user_id)
        mode: Literal["query", "search", "select", "flip_page", "read_only"] = hint[0]
        arg_str: str = hint[1]
        feedback: str = ""

        if mode in ("select", "flip_page"):
            record = self.record_dict.get(source_port)
            if record is not None and not self.bot.db.query.has_database(record.database):
                del self.record_dict[source_port]
                feedback = (
                    "当前查询数据库未启用。"
                    if self.bot.db.query.is_database_disabled(record.database)
                    else "当前查询数据库未加载。"
                )
                return [BotSendMsgCommand(self.bot.account, feedback, [port])]

        # 处理指令
        if not arg_str and (mode == "query" or mode == "search"):
            feedback = self.get_state()
        elif mode == "query" or mode == "search":
            if not self.bot.db.query.has_database(database):
                feedback = (
                    "当前查询数据库未启用。"
                    if self.bot.db.query.is_database_disabled(database)
                    else "未加载的数据库。"
                )
            else:
                feedback = await self.query_info(
                    database,
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
            index = int(arg_str)
            record.time = get_current_date_raw()  # 更新记录有效期
            if index >= record.length:
                feedback = self.format_loc(LOC_QUERY_NO_RESULT)
            else:
                item = record.data[index]
                result = await self.query_feedback(database, item, source_port)
                feedback = self.format_loc(LOC_QUERY_RESULT, result=result)
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
                    "\n示例:"\
                    "\n.查询 借机攻击"\
                    "\n.检索 长弓"\
                    "\n.检索 火焰 敏捷豁免 d6"
            return help_str
        return ""

    def get_description(self) -> str:
        return ".查询 根据关键字查找资料 .搜索 根据关键字和内容查找资料"

    async def query_info(
        self,
        database: str,
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
                query_keywords,
                search_mode,
            )
        except QueryError as exc:
            return exc.info
        poss_result_num: int = len(poss_result)

        feedback: str = ""
        poss_result_num = len(poss_result)
        # 处理
        if not poss_result or poss_result_num == 0:  # 找不到结果
            return ""
        elif poss_result_num == 1:  # 找到唯一结果
            feedback = await self.query_feedback(database, poss_result[0], port)
        else:  # len(poss_result) > 1  找到多个结果, 记录当前信息并提示用户选择
            # 记录当前信息以备后续选择或翻页
            self.record_dict[port] = QueryRecord(poss_result, database, get_current_date_raw(), poss_result_num)
            page_item_num = MAX_QUERY_CANDIDATE_SIMPLE_NUM
            self.record_dict[port].process_data()
            show_result: List[QueryData] = poss_result[:page_item_num]
            feedback = self.format_items_list_feedback(show_result)
            if poss_result_num > page_item_num:
                feedback += "\n" + self.format_loc(LOC_QUERY_MULTI_RESULT_PAGE, page_cur=1,
                                                   page_total=math.ceil(poss_result_num / page_item_num))
        return feedback
    
    def command_split(self, keywords: str) -> List[str]:
        return command_split(keywords)
    
    async def query_item(
        self,
        database: str,
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
                raise QueryError(self.format_loc(LOC_QUERY_INVALID_FORMAT))
        else:
            return poss_result
        poss_result = await self.search_item(
            database, query_command_list, search_mode,
        )
        return poss_result

    async def search_item(
        self, database: str, query_command_list: List[str],
        search_mode: int = 0,
    ) -> List[QueryData]:
        """搜索合规的对象（适配层：调用 QueryStore.search() + dict→QueryData）。"""
        try:
            result = await self.bot.db.query.search(
                database=database,
                query_tokens=query_command_list,
                fulltext=(search_mode == 1),
                limit=MAX_QUERY_ITEM_NUM,
            )
        except QueryStoreError as exc:
            if str(exc) == "查询格式错误。":
                raise QueryError(self.format_loc(LOC_QUERY_INVALID_FORMAT)) from exc
            raise QueryError(self.format_loc(LOC_QUERY_TOO_MUCH_RESULT)) from exc

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
        item: QueryData,
        port: MessagePort,
    ) -> str:
        """
        生成查询到目标的返回文本，包括处理嵌套查询
        """
        if any(line.startswith("/") for line in item.data_content.splitlines()):
            return self.format_loc(
                LOC_QUERY_OUTDATED_CONTENT,
                database=database,
                keyword=item.data_name,
            )
        return self.format_item_feedback(item)

    def format_item_feedback(self, item: QueryData) -> str:
        # 最基本的单条目返回文本
        item_content = item.data_content if item.data_content else "[内容为空，等待热心小编补充]"
        item_tag = ""
        item_book = self.format_loc(LOC_QUERY_CELL_BOOK, book=item.data_from) if item.data_from else ""
        item_redirect = self.format_loc(LOC_QUERY_CELL_REDIRECT, redirect=item.redirect_by) if item.redirect_by else ""
        return self.format_loc(LOC_QUERY_SINGLE_RESULT, keyword=item.data_name, en_keyword=item.data_name_en, content=item_content, tag=item_tag, book=item_book, redirect=item_redirect)

    def format_item_redirects_feedback(self, item: QueryData) -> str:
        # 最基本的单条目返回文本
        item_content = item.data_content if item.data_content else "[内容为空，等待热心小编补充]"
        item_tag = ""
        item_book = self.format_loc(LOC_QUERY_CELL_BOOK, book=item.data_from) if item.data_from else ""
        item_redirect = self.format_loc(LOC_QUERY_CELL_REDIRECT, redirect=item.redirect_by) if item.redirect_by else ""
        return self.format_loc(LOC_QUERY_SINGLE_RESULT, keyword=item.data_name, en_keyword=item.data_name_en, content=item_content, tag=item_tag, book=item_book, redirect=item_redirect)

    @staticmethod
    def format_items_list_feedback(items: List[QueryData],start_index: int = 0):
        # 多个结果，要求用户从结果中选择其一的返回文本
        return ", ".join((f"{start_index+index}.{item.display_name}" for index, item in enumerate(items)))

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
        page_item_num = MAX_QUERY_CANDIDATE_SIMPLE_NUM

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

