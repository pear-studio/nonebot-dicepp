from typing import List, Tuple, Dict, Optional, Set, Literal, Iterable, Any
import os
import datetime
import openpyxl
import random
# from openpyxl.utils import get_column_letter

from core.bot import Bot
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, MessagePort, PrivateMessagePort, GroupMessagePort, preprocess_msg
from core.localization import LOC_FUNC_DISABLE
from core.config import DATA_PATH
from utils.localdata import read_xlsx, update_xlsx, col_based_workbook_to_dict, create_parent_dir, get_empty_col_based_workbook
from utils.time import get_current_date_raw
from utils.data import yield_deduplicate

LOC_QUERY_RESULT = "query_result"
LOC_QUERY_SINGLE_RESULT = "query_single_result"
LOC_QUERY_MULTI_RESULT = "query_multi_result"
LOC_QUERY_MULTI_RESULT_ITEM = "query_multi_result_item"
LOC_QUERY_MULTI_RESULT_PAGE = "query_multi_result_page"
LOC_QUERY_MULTI_RESULT_PAGE_UNDERFLOW = "query_multi_result_page_underflow"
LOC_QUERY_MULTI_RESULT_PAGE_OVERFLOW = "query_multi_result_page_overflow"
LOC_QUERY_NO_RESULT = "query_no_result"
LOC_QUERY_KEY_NUM_EXCEED = "query_key_num_exceed"

CFG_QUERY_ENABLE = "query_enable"
CFG_QUERY_DATA_PATH = "query_data_path"

QUERY_ITEM_FIELD_KEY = "Key"
QUERY_ITEM_FIELD_SYN = "Synonym"
QUERY_ITEM_FIELD_CONTENT = "Content"
QUERY_ITEM_FIELD_DESC = "Description"
QUERY_ITEM_FIELD_CAT = "Catalogue"
QUERY_ITEM_FIELD_TAG = "Tag"

QUERY_ITEM_FIELD = [QUERY_ITEM_FIELD_KEY, QUERY_ITEM_FIELD_SYN, QUERY_ITEM_FIELD_CONTENT, QUERY_ITEM_FIELD_DESC,
                    QUERY_ITEM_FIELD_CAT, QUERY_ITEM_FIELD_TAG]
QUERY_ITEM_FIELD_COMMENT = {QUERY_ITEM_FIELD_KEY: "查询关键字",
                            QUERY_ITEM_FIELD_SYN: "选填, 关键字同义词, 用/分割",
                            QUERY_ITEM_FIELD_CONTENT: "词条内容",
                            QUERY_ITEM_FIELD_DESC: "选填, 对于词条的简短描述",
                            QUERY_ITEM_FIELD_CAT: "选填, 该词条被归在哪一个目录下, 用/分割父子目录",
                            QUERY_ITEM_FIELD_TAG: "选填, 内容的Tag, 如#核心 #法术",
                            }

QUERY_ITEM_FIELD_DESC_DEFAULT_LEN = 20  # 默认用前多少个字符作为默认Description

MAX_QUERY_KEY_NUM = 5  # 最多能同时用多少个查询关键字
MAX_QUERY_CANDIDATE_NUM = 10  # 详细查询时一页最多能同时展示多少个条目
MAX_QUERY_CANDIDATE_SIMPLE_NUM = 30  # 简略查询时一页最多能同时展示多少个条目
RECORD_RESPONSE_TIME = 60  # 至多响应多久以前的查询指令, 多余的将被清理, 单位为秒
RECORD_CLEAN_FREQ = 50  # 每隔多少次查询指令尝试清理一次查询记录


class QueryItem:
    def __init__(self, uuid: int, key: Tuple[str], content: str, desc: str, catalogue: str, tag: List[str],
                 src_file_uuid: int, src_file_index: int):
        """表示一个可以被查询到的条目"""
        self.uuid = uuid  # 唯一标识符
        self.key = key  # 查询关键字
        self.content = content  # 条目内容
        self.desc = desc  # 条目描述
        self.catalogue = catalogue  # 出自哪一个目录, 如"玩家手册/第一章"
        self.tag = tag  # 条目的Tag列表, 如[战士, 天赋]

        self.src_file_uuid = src_file_uuid  # 用来标志这个条目是从哪个源文件读取的, uuid对应的path从Command实例中找到
        self.src_file_row = src_file_index  # 属于源文件的第几个条目, 从1开始

    def __repr__(self):
        return f"uuid:{self.uuid}, key:{self.key}, desc:{self.desc}"


class QuerySource:
    def __init__(self, uuid: int, path: str, sheet: str):
        """表示一个可以被查询到的条目"""
        self.uuid = uuid  # 唯一标识符
        self.path = path  # 文件路径
        self.sheet = sheet  # 分页名称

    def __repr__(self):
        return f"uuid:{self.uuid}, path:{self.path}, sheet:{self.sheet}"


class QueryRecord:
    def __init__(self, uuid_list: List[int], time: datetime.datetime):
        """记录一次可交互的查询指令"""
        self.uuid_list = uuid_list  # 当前可以选择的条目的uuid列表
        self.time = time  # 更新时间
        self.page = 1  # 当前的页数
        self.mode = 0  # 0代表简易模式, 1代表详细模式


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
        self.query_dict: Dict[str, List[int]] = {}  # key为查询关键字, value为item uuid
        self.item_uuid_dict: Dict[int, QueryItem] = {}  # key为item uuid
        self.src_uuid_dict: Dict[int, QuerySource] = {}  # key为source uuid
        self.record_dict: Dict[MessagePort, QueryRecord] = {}
        self.record_clean_flag: int = 0

        reg_loc = bot.loc_helper.register_loc_text
        reg_loc(LOC_QUERY_RESULT, "{result}", "查询成功时返回的内容, result为single_result或multi_result")
        reg_loc(LOC_QUERY_SINGLE_RESULT, "{keyword}: {tag}\n{content}{cat}",
                "查询找到唯一条目, keyword: 主关键字, content: 词条内容"
                ", cat: 换行+目录*, tag: 标签*, syn: 换行+同义词*  (*如果有则显示, 没有则忽略)")
        reg_loc(LOC_QUERY_MULTI_RESULT, "{multi_results}",
                f"查询找到多个条目, multi_results由多行{LOC_QUERY_MULTI_RESULT_ITEM}构成")
        reg_loc(LOC_QUERY_MULTI_RESULT_ITEM, "{keyword}: {description}{tag}{cat}",
                "查询找到多个条目时单个条目的描述, keyword: 主关键字, description: 简短描述"
                ", cat: 目录*, tag: 标签*, syn: 同义词*  (*如果有则显示, 没有则忽略)")
        reg_loc(LOC_QUERY_MULTI_RESULT_PAGE, "Page{page_cur}/{page_total}, + for next page, - for prev page",
                "搜索结果出现多页时提示, {page_cur}表示当前页, {page_total}表示总页数")
        reg_loc(LOC_QUERY_MULTI_RESULT_PAGE_UNDERFLOW, "This is the first page!", "用户尝试在第一页往前翻页时的提醒")
        reg_loc(LOC_QUERY_MULTI_RESULT_PAGE_OVERFLOW, "This is the final page!", "用户尝试在最后一页往后翻页时的提醒")
        reg_loc(LOC_QUERY_NO_RESULT, "Cannot find result...", "查询失败时的提示")
        reg_loc(LOC_QUERY_KEY_NUM_EXCEED, "Maximum keyword num is {key_num}",
                "用户查询时使用过多关键字时的提示 {key_num}为关键字数量上限")

        bot.cfg_helper.register_config(CFG_QUERY_ENABLE, "1", "查询指令开关")
        bot.cfg_helper.register_config(CFG_QUERY_DATA_PATH, "./QueryData", "查询指令的数据来源, .代表Data文件夹")

    def delay_init(self) -> List[str]:
        # 从本地文件中读取资料
        data_path_list: List[str] = self.bot.cfg_helper.get_config(CFG_QUERY_DATA_PATH)
        for i, path in enumerate(data_path_list):
            if path.startswith("./"):  # 用DATA_PATH作为当前路径
                data_path_list[i] = os.path.join(DATA_PATH, path[2:])
        init_info: List[str] = []
        for data_path in data_path_list:
            self.load_data_from_path(data_path, init_info)
        init_info.append(self.get_state())
        return init_info

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = False
        should_pass: bool = False
        mode: Optional[Literal["query", "search", "select", "flip_page"]] = None
        arg_str: Optional[str] = None

        # 响应交互查询指令
        port = MessagePort(meta.group_id, meta.user_id)
        if port in self.record_dict:
            record = self.record_dict[port]
            if get_current_date_raw() - record.time < datetime.timedelta(seconds=RECORD_RESPONSE_TIME):
                # 选择条目
                try:
                    should_proc = (0 <= int(msg_str) <= len(record.uuid_list))
                    mode, arg_str = "select", msg_str
                except ValueError:
                    pass
                # 翻页
                if not should_proc and msg_str.strip() == "+":
                    should_proc, mode, arg_str = True, "flip_page", "+"
                if not should_proc and msg_str.strip() == "-":
                    should_proc, mode, arg_str = True, "flip_page", "-"
            else:
                del self.record_dict[port]  # 清理过期条目

        # 常规查询指令
        for key in ["查询", "q"]:
            if not should_proc and msg_str.startswith(f".{key}"):
                should_proc, mode, arg_str = True, "query", msg_str[1 + len(key):].strip()
        for key in ["搜索", "s"]:
            if not should_proc and msg_str.startswith(f".{key}"):
                should_proc, mode, arg_str = True, "search", msg_str[1 + len(key):].strip()

        # ToDo: 在线修改本地查询条目

        assert (not should_proc) or mode
        hint = (mode, arg_str)
        return should_proc, should_pass, hint

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        source_port = MessagePort(meta.group_id, meta.user_id)
        mode: Literal["query", "search", "select", "flip_page"] = hint[0]
        arg_str: str = hint[1]
        feedback: str
        show_mode = 0 if meta.group_id else 1

        # 判断功能开关
        try:
            assert (int(self.bot.cfg_helper.get_config(CFG_QUERY_ENABLE)[0]) != 0)
        except AssertionError:
            feedback = self.bot.loc_helper.format_loc_text(LOC_FUNC_DISABLE, func=self.readable_name)
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]

        # 处理指令
        if not arg_str and (mode == "query" or mode == "search"):
            feedback = self.get_state()
        elif mode == "query":
            feedback = self.query_info(arg_str, source_port, search_mode=0, show_mode=show_mode)
        elif mode == "search":
            feedback = self.query_info(arg_str, source_port, search_mode=1, show_mode=show_mode)
        elif mode == "select":
            record = self.record_dict[source_port]
            page_item_num = MAX_QUERY_CANDIDATE_NUM if record.mode != 0 else MAX_QUERY_CANDIDATE_SIMPLE_NUM
            index = int(arg_str) + (record.page-1) * page_item_num
            try:
                uuid = self.record_dict[source_port].uuid_list[index]
                item: QueryItem = self.item_uuid_dict[uuid]
                record.time = get_current_date_raw()  # 更新记录有效期
                feedback = self.format_single_item_feedback(item)
            except IndexError:
                feedback = self.format_loc(LOC_QUERY_NO_RESULT)
        elif mode == "flip_page":
            record = self.record_dict[source_port]
            next_page = (arg_str == "+")
            feedback, cur_page = self.flip_page(record, next_page)
            self.record_dict[source_port].page = cur_page
        else:
            raise NotImplementedError()

        # 尝试清理过期的查询记录
        self.record_clean_flag += 1
        if self.record_clean_flag >= RECORD_CLEAN_FREQ:
            self.record_clean_flag = 0
            self.clean_records()

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword in ["查询", "搜索", "q", "s"]:
            help_str = "查询资料: .查询 查询目标"\
                    "\n查询指令支持部分匹配, 可用/区分多个关键字"\
                    "\n可以用搜索指令来匹配词条内容(而不是仅匹配关键字)"\
                    "\n若有多条可能的结果, 可以通过查询或搜索后直接输入序号查询, 输入+或-可以翻页" \
                    "\n可以用q作为查询(query)的缩写, 或用s作为搜索(search)的缩写" \
                    "\n示例:"\
                    "\n.查询 借机攻击"\
                    "\n.查询 长弓"\
                    "\n.查询 法师/6环"\
                    "\n.搜索 长弓 // 返回所有含有长弓的词条(如物品, 怪物, 魔法物品, 能力)"\
                    "\n.搜索 昏迷/施法时间 //利用法术词条中必然含有施法时间的规律查询和昏迷相关的法术"
            return help_str
        return ""

    def get_description(self) -> str:
        return ".查询 根据关键字查找资料 .搜索 根据关键字和内容查找资料"

    def query_info(self, query_key: str, port: MessagePort, search_mode: int, show_mode: int = 0) -> str:
        """
        查询信息, 返回输出给用户的字符串, 若给出选项将会记录信息以便响应用户之后的快速查询.
        search_mode != 0则使用模糊查找
        """
        # 清空过往记录
        if port in self.record_dict:
            del self.record_dict[port]
        # 分割关键字
        query_key_list = [key.strip() for key in query_key.split("/") if key.strip()]
        if len(query_key_list) > MAX_QUERY_KEY_NUM:  # 关键字数量超出上限
            return self.format_loc(LOC_QUERY_KEY_NUM_EXCEED, key_num=MAX_QUERY_KEY_NUM)
        # 找到搜索候选
        poss_result = self.search_item(query_key_list, search_mode)
        # 处理
        if not poss_result:  # 找不到结果
            return self.format_loc(LOC_QUERY_NO_RESULT)
        elif len(poss_result) == 1:  # 找到唯一结果
            item: QueryItem = self.item_uuid_dict[poss_result[0]]
            feedback = self.format_single_item_feedback(item)
        else:  # len(poss_result) > 1  找到多个结果, 记录当前信息并提示用户选择
            # 记录当前信息以备将来查询
            self.record_dict[port] = QueryRecord(poss_result, get_current_date_raw())
            self.record_dict[port].mode = show_mode
            page_item_num = MAX_QUERY_CANDIDATE_NUM if show_mode != 0 else MAX_QUERY_CANDIDATE_SIMPLE_NUM
            show_result: List[int] = poss_result[:page_item_num]
            items: List[QueryItem] = [self.item_uuid_dict[uuid] for uuid in show_result]
            if show_mode != 0:
                feedback = self.format_multiple_items_feedback(items)
            else:
                feedback = self.format_multiple_items_simple_feedback(items)
            if len(poss_result) > page_item_num:
                feedback += "\n" + self.format_loc(LOC_QUERY_MULTI_RESULT_PAGE, page_cur=1,
                                                   page_total=len(poss_result) // page_item_num + 1)
        feedback = self.format_loc(LOC_QUERY_RESULT, result=feedback)
        return feedback

    def search_item(self, query_key_list: List[str], search_mode: int) -> List[int]:
        poss_result: List[int] = []
        query_key_len: int = sum((len(key) for key in query_key_list))
        if search_mode == 0:  # 仅匹配条目关键字
            for candidate_key in self.query_dict.keys():
                if all(((key in candidate_key) for key in query_key_list)):  # 所有关键字都在candidate_key中出现
                    if query_key_len == len(candidate_key):  # 关键字正好和candidate_key一模一样, 可以直接返回了
                        return self.query_dict[candidate_key]
                    poss_result += self.query_dict[candidate_key]
        else:  # 匹配条目关键字和内容
            for item in self.item_uuid_dict.values():
                candidate_key: str = "/".join(list(item.key) + [item.content])
                candidate_key = preprocess_msg(candidate_key)
                if all(((key in candidate_key) for key in query_key_list)):  # 所有关键字都在content中出现
                    poss_result.append(item.uuid)
        # 去除重复的条目
        poss_result = list(yield_deduplicate(poss_result))
        return poss_result

    def format_single_item_feedback(self, item: QueryItem) -> str:
        item_keyword = item.key[0]
        item_content = item.content
        item_tag = get_tag_string(item.tag)
        item_cat = "\n目录: " + item.catalogue if item.catalogue else ""
        item_syn = "\n同义词: " + get_syn_string(item.key[1:]) if item.key[1:] else ""
        return self.format_loc(LOC_QUERY_SINGLE_RESULT, keyword=item_keyword, content=item_content, tag=item_tag,
                               cat=item_cat, syn=item_syn)

    def format_multiple_items_feedback(self, items: List[QueryItem]):
        multi_results = ""
        for index, item in enumerate(items):
            item_keyword = item.key[0]
            item_desc = item.desc
            item_tag = " " + get_tag_string(item.tag) if item.tag else ""
            item_cat = " 目录: " + item.catalogue if item.catalogue else ""
            item_syn = " 同义词: " + get_syn_string(item.key[1:]) if item.key[1:] else ""
            item_info = self.format_loc(LOC_QUERY_MULTI_RESULT_ITEM, keyword=item_keyword, description=item_desc,
                                        tag=item_tag, cat=item_cat, syn=item_syn)
            multi_results += f"{index}. {item_info}\n"
        feedback = self.format_loc(LOC_QUERY_MULTI_RESULT, multi_results=multi_results.strip())
        return feedback

    @staticmethod
    def format_multiple_items_simple_feedback(items: List[QueryItem]):
        return ", ".join((f"{index}.{item.key[0]}" for index, item in enumerate(items)))

    def flip_page(self, record: QueryRecord, next_page: bool) -> Tuple[str, int]:
        def get_feedback(page) -> str:
            index = (page - 1) * page_item_num
            uuids = record.uuid_list[index:index + page_item_num]
            items = [self.item_uuid_dict[uuid] for uuid in uuids]
            if record.mode != 0:
                return self.format_multiple_items_feedback(items)
            else:
                return self.format_multiple_items_simple_feedback(items)

        cur_page = record.page
        page_item_num = MAX_QUERY_CANDIDATE_NUM if record.mode != 0 else MAX_QUERY_CANDIDATE_SIMPLE_NUM

        total_page = len(record.uuid_list) // page_item_num + 1
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
        if len(record.uuid_list) > page_item_num:
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

    def load_data_from_path(self, path: str, error_info: List[str]) -> None:
        """从指定文件或目录读取信息"""

        def load_data_from_xlsx(wb: openpyxl.Workbook):
            data_dict = col_based_workbook_to_dict(wb, QUERY_ITEM_FIELD, error_info)
            for sheet_name in data_dict.keys():
                sheet_data = data_dict[sheet_name]
                # 生成QuerySource
                qs_uuid = get_query_uuid()
                qs = QuerySource(qs_uuid, path, sheet_name)
                # 逐行生成QueryItem
                qi_list: List[QueryItem] = []
                item_num = len(sheet_data[QUERY_ITEM_FIELD_KEY])
                for item_index in range(item_num):
                    main_key = sheet_data[QUERY_ITEM_FIELD_KEY][item_index]

                    item_key = sheet_data[QUERY_ITEM_FIELD_SYN][item_index]
                    item_key = str(item_key).strip().split("/") if item_key else []  # 用/分隔同义词
                    item_key: List[str] = [main_key] + [syn.strip() for syn in item_key if syn.strip()]
                    item_key_tuple: Tuple[str] = tuple(item_key)

                    item_content = sheet_data[QUERY_ITEM_FIELD_CONTENT][item_index]
                    item_content: str = str(item_content).strip() if item_content else ""

                    item_desc = sheet_data[QUERY_ITEM_FIELD_DESC][item_index]
                    item_desc: str = str(item_desc).strip() if item_desc else ""

                    item_cat = sheet_data[QUERY_ITEM_FIELD_CAT][item_index]
                    item_cat: str = str(item_cat).strip() if item_cat else ""

                    item_tag = sheet_data[QUERY_ITEM_FIELD_TAG][item_index]
                    item_tag = str(item_tag).strip().split("#") if item_tag else []  # 用#分隔Tag
                    item_tag: List[str] = [tag.strip() for tag in item_tag if tag.strip()]

                    if not main_key:
                        # dice_log(f"表格{wb.path}/{sheet_name}第{item_index+2}行缺少key, 该条目未加载")
                        continue
                    if not item_content:
                        error_info.append(f"表格{wb.path}/{sheet_name}第{item_index+2}行缺少content, 该条目未加载")
                        continue
                    if not item_desc:  # 用content的前一部分自动生成desc
                        item_desc = item_content[:QUERY_ITEM_FIELD_DESC_DEFAULT_LEN].replace("\n", " ") + "..."
                    if not item_tag:
                        item_tag = []

                    qi_uuid = get_query_uuid()
                    qi = QueryItem(qi_uuid, item_key_tuple, item_content, item_desc, item_cat, item_tag,
                                   qs_uuid, item_index + 1)
                    qi_list.append(qi)
                # 记录到self字典中
                if qi_list:
                    self.src_uuid_dict[qs_uuid] = qs
                    for item in qi_list:
                        self.item_uuid_dict[item.uuid] = item
                        for k in item.key:
                            k = preprocess_msg(k)  # 进行与输入语句一样的预处理
                            if k not in self.query_dict:
                                self.query_dict[k] = [item.uuid]
                            else:
                                self.query_dict[k].append(item.uuid)

        if path.endswith(".xlsx"):
            if os.path.exists(path):  # 存在文件则读取文件
                try:
                    workbook = read_xlsx(path)
                except PermissionError:
                    error_info.append(f"读取{path}时遇到错误: 权限不足")
                    return
                load_data_from_xlsx(workbook)
            else:  # 创建一个模板文件
                create_parent_dir(path)  # 父文件夹不存在需先创建父文件夹
                workbook = get_empty_col_based_workbook(QUERY_ITEM_FIELD, QUERY_ITEM_FIELD_COMMENT)
                update_xlsx(workbook, path)
                workbook.close()
        elif path:  # 是文件夹
            if os.path.exists(path):  # 遍历文件夹下所有文件
                try:
                    inner_paths = os.listdir(path)
                    for inner_path in inner_paths:
                        inner_path = os.path.join(path, inner_path)
                        self.load_data_from_path(inner_path, error_info)
                except FileNotFoundError as e:  # 文件夹不存在
                    error_info.append(f"读取{path}时遇到错误: {e}")
            else:  # 创建空文件夹
                create_parent_dir(path)

    def get_state(self) -> str:
        feedback: str
        if self.src_uuid_dict:
            feedback = f"已加载{len(self.src_uuid_dict)}个资料库, {len(self.item_uuid_dict)}个查询条目"
        else:
            feedback = f"尚未加载任何资料库"
        return feedback


QUERY_UUID_SET: Set[int] = set()
QUERY_UUID_MIN: int = -(1 << 31)
QUERY_UUID_MAX: int = 1 << 31


def get_query_uuid() -> int:
    iter_num = 0
    uuid = random.randint(QUERY_UUID_MIN, QUERY_UUID_MAX)
    while uuid in QUERY_UUID_SET:
        uuid = random.randint(QUERY_UUID_MIN, QUERY_UUID_MAX)
        iter_num += 1
        if iter_num > 100000:
            raise TimeoutError("获取Query UUID失败!")
    QUERY_UUID_SET.add(uuid)
    return uuid


def get_tag_string(tags: Iterable[str]):
    """获得标签字符串, 输入["a", "b"], 返回"#a #b" """
    return " ".join(["#" + tag for tag in tags])


def get_syn_string(synonym: Iterable[str]):
    """获得同义词字符串, ["a", "b"], 返回"a, b" """
    return ", ".join(synonym)
