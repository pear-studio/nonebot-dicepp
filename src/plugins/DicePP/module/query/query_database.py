from typing import List, Dict, Any
import os
import json
import re
import openpyxl
import sqlite3
from openpyxl.comments import Comment

from core.data import custom_data_chunk, DataChunkBase
from core.data import JsonObject, custom_json_object
from utils.time import get_current_date_str

from utils.localdata import read_xlsx, update_xlsx, col_based_workbook_to_dict, create_parent_dir, get_empty_col_based_workbook
#from module.query import QUERY_DATA_FIELD, QUERY_DATA_FIELD_LIST, QUERY_REDIRECT_FIELD, QUERY_REDIRECT_FIELD_LIST

#QIF = QUERY_ITEM_FIELD 太长了还是缩写的好。
#数据库常规六件套
QIF_NAME = "Name"  # 名称（无需唯一）
QIF_NAME_EN = "NameEN"  # 英文名称
QIF_FROM = "From"  # 来源
QIF_CATALOGUE = "Catalogue"  # 分类
QIF_TAG = "Tag"  # 标签列表
QIF_CONTENT = "Content"  # 查询内容
#旧版限定
QIF_KEY = "Key"  # 唯一关键字
QIF_SYNONYM = "Synonym"  # 同义词
QIF_DESCRIPTION = "Description"  # 简述

# 老式梨骰查询表的格式
QIF_OLD = [QIF_KEY, QIF_SYNONYM, QIF_CONTENT, QIF_DESCRIPTION, QIF_CATALOGUE, QIF_TAG]
# 新式梨骰查询表的格式
QIF = [QIF_NAME, QIF_NAME_EN, QIF_FROM, QIF_CATALOGUE, QIF_TAG, QIF_CONTENT]
# 新式梨骰私设表的格式
QIF_HB = [QIF_NAME, QIF_NAME_EN, QIF_CATALOGUE, QIF_TAG, QIF_CONTENT]

QUERY_DATA_FIELD = "名称,英文,来源,分类,标签,内容"
QUERY_DATA_FIELD_LIST = ["名称","英文","来源","分类","标签","内容"]
QUERY_REDIRECT_FIELD = "名称,重定向"
QUERY_REDIRECT_FIELD_LIST = ["名称","重定向"]

# 已连接的数据库DICT
CONNECTED_QUERY_DATABASES: Dict[str, sqlite3.Connection] = {}
DATABASE_CURSOR: Dict[str, sqlite3.Cursor] = {}

def create_empty_sqlite_database(path: str):
    """创建空白查询数据库"""
    try:
        db = sqlite3.connect(path)
        cur = db.cursor()
        cur.execute(
            "CREATE TABLE data (" + ",".join([(field + " TEXT DEFAULT ('')") for field in QUERY_DATA_FIELD_LIST]) + ");")
        cur.execute("CREATE INDEX [From] ON data (来源 ASC);")
        cur.execute("CREATE INDEX Catalogue ON data (分类);")
        cur.execute(
            "CREATE TABLE redirect (" + ",".join([(field + " TEXT DEFAULT ('')") for field in QUERY_REDIRECT_FIELD_LIST]) + ");")
        db.close()
    except PermissionError:
        return False
    return True

def create_query_database(path: str) -> str:
    """创建一个新的查询数据库"""
    create_parent_dir(path)  # 若父文件夹不存在需先创建父文件夹
    if create_empty_sqlite_database(path):
        return f"已创建{path}"
    else:
        return f"创建{path}时遇到错误: 权限不足"

def connect_query_database(path: str) -> str:
    """连接查询数据库"""
    error_info: List[str] = []
    if path.endswith(".db"):
        if not os.path.exists(path):
            # 注意：这里不再“自动创建空库”，避免用户误以为已加载到真实数据
            error_info.append(f"未找到数据库文件: {path}")
            return "\n".join(error_info)
        # 存在数据库则尝试连接数据库
        db = os.path.basename(path)[:-3]
        if db in CONNECTED_QUERY_DATABASES.keys():  # 已加载
            error_info.append("已加载过该数据库。")
            return "\n".join(error_info)
        try:
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            conn.create_function("regexp", 2, regexp)
            CONNECTED_QUERY_DATABASES[db] = conn
            DATABASE_CURSOR[db] = conn.cursor()
        except PermissionError:
            error_info.append(f"读取{path}时遇到错误: 权限不足")
            return "\n".join(error_info)
    elif path.endswith(".db-journal"): #跳过他，无需处理
        return "\n".join(error_info)
    elif path:  # 是文件夹
        if os.path.exists(path):  # 遍历文件夹下所有文件
            try:
                inner_paths = os.listdir(path)
                for inner_path in inner_paths:
                    inner_path = os.path.join(path, inner_path)
                    child_info = connect_query_database(inner_path)
                    if child_info:
                        error_info.append(child_info)
            except FileNotFoundError as e:  # 文件夹不存在
                error_info.append(f"读取{path}时遇到错误: {e}")
        else:  # 创建空文件夹
            create_parent_dir(path)
    return "\n".join(error_info)
    
def disconnect_query_database(db: str) -> None:
    """取消连接查询数据库"""
    CONNECTED_QUERY_DATABASES[db].close()
    del CONNECTED_QUERY_DATABASES[db]
    del DATABASE_CURSOR[db]

def regexp(pattern: str, input: str):
    """SQL用的正则表达式公式函数"""
    p = re.compile(str(pattern),re.I)
    return bool(re.search(p, input or ""))
        
def regexp_normalize(string: str) -> str:
    """用于将正则表达式的任何公式文本改为原义"""
    new_string: str = ""
    for char in string:
        if char in "$()*+.[?\\^{|":
            new_string += "\\" + char
        else:
            new_string += char
    return new_string

def load_data_from_xlsx(wb: openpyxl.Workbook, sql_cur: Any,xlsx_name: str, xlsx_mode: int = 0) -> bool:
    """将数据从xlsx中读取出来"""
    def try_load_data(data: str,default_var: str = "") -> str:
        return str(data).strip() if data else default_var
    
    data_dict: dict
    if xlsx_mode == 0:
        data_dict = col_based_workbook_to_dict(wb, QIF_OLD, [])
    elif xlsx_mode == 1:
        data_dict = col_based_workbook_to_dict(wb, QIF, [])
    elif xlsx_mode == 2:
        sql_cur.execute("DELETE FROM data WHERE 来源 LIKE '私设:" + xlsx_name.replace("'","''") + "'")
        data_dict = col_based_workbook_to_dict(wb, QIF_HB, [])
    edit_cmd_data = []
    edit_cmd_redirect = []
    
    if len(data_dict.keys()) == 0:
        return False
    
    for sheet_name in data_dict.keys():
        # 逐行生成查询条目的新增指令
        sheet_data = data_dict[sheet_name]
        if xlsx_mode == 0:  #老式梨骰查询表
            item_num = len(sheet_data[QIF_KEY])
            for item_index in range(item_num):
                # 获取信息
                item = [try_load_data(sheet_data[QIF_OLD[sub_index]][item_index]) for sub_index in range(6)]
                
                if len(item[0]) == 0:
                    continue
                en_name = ""
                content_lines = (item[2].strip()).splitlines()
                if len(content_lines) > 1:
                    for char in content_lines[0]:
                        if ord(char) < 128:
                            en_name += char
                    en_name = en_name.strip()
                    if len(en_name) == 0:
                        for char in content_lines[1]:
                            if ord(char) < 128:
                                en_name += char
                        item[3] = "\n".join(content_lines[2:])
                    else:
                        item[3] = "\n".join(content_lines[1:])
                tags = ((item[5].strip()).replace(" ","")).split()
                for index in range(len(tags)):
                    if tags[index].startswith("#"):
                        tags[index] = tags[index][1:]
                catas = item[4].split("/")
                book = "未知"
                if len(catas) > 1:
                    book = catas[0]
                    item[4] = catas[1]
                    tags += catas[1:]
                elif len(catas) == 1:
                    book = catas[0]
                    item[4] = ""
                edit_cmd_data.append((item[0],en_name,book,item[4]," ".join(tags),item[3]))
                syns = item[1].split("/")
                for syn in syns:
                    edit_cmd_redirect.append((syn,item[0]))
        elif xlsx_mode == 1:  #新式梨骰查询表
            item_num = len(sheet_data[QIF_NAME])
            for item_index in range(item_num):
                # 获取信息
                item = [try_load_data(sheet_data[QIF[sub_index]][item_index]) for sub_index in range(6)]
                if len(item[0]) == 0:
                    continue
                edit_cmd_data.append((item[0],item[1],item[2],item[3],item[4],item[5]))
        elif xlsx_mode == 2:  #新式梨骰私设表
            item_num = len(sheet_data[QIF_NAME])
            for item_index in range(item_num):
                # 获取信息
                item = [try_load_data(sheet_data[QIF_HB[sub_index]][item_index]) for sub_index in range(5)]
                if len(item[0]) == 0:
                    continue
                edit_cmd_data.append((item[0],item[1],"私设:"+xlsx_name,item[2],item[3],item[4]))
    if len(edit_cmd_data) == 0:
        return False
    sql_cur.executemany('INSERT INTO data VALUES(?,?,?,?,?,?)',edit_cmd_data)
    sql_cur.executemany('INSERT INTO redirect VALUES(?,?)',edit_cmd_redirect)
    return True

def load_data_from_xlsx_to_sqlite(xlsx_path: str, database_path: str, xlsx_mode: int) -> bool:
    """将数据从xlsx中读取出来，存入查询数据库"""
    connect_query_database(database_path)
    db = os.path.basename(database_path)[:-3]
    wb = read_xlsx(xlsx_path)
    xlsx_name = os.path.basename(xlsx_path)[:-5]
    load = False
    if wb:
        load = load_data_from_xlsx(wb,DATABASE_CURSOR[db],xlsx_name,xlsx_mode)
        CONNECTED_QUERY_DATABASES[db].commit()
    return load
