import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import aiosqlite

from core.config import DATA_PATH
from utils import col_based_workbook_to_dict, create_parent_dir, read_xlsx


# 查询资料库表结构（与 module/query/query_database.py 保持一致）
QUERY_DATA_FIELD_LIST = ["名称", "英文", "来源", "分类", "标签", "内容"]
QUERY_REDIRECT_FIELD_LIST = ["名称", "重定向"]
QUERY_DATA_FIELD = ",".join(QUERY_DATA_FIELD_LIST)
QUERY_REDIRECT_FIELD = ",".join(QUERY_REDIRECT_FIELD_LIST)

# xlsx 解析映射（与 module/query/query_database.py 保持一致）
QIF_NAME = "Name"
QIF_NAME_EN = "NameEN"
QIF_FROM = "From"
QIF_CATALOGUE = "Catalogue"
QIF_TAG = "Tag"
QIF_CONTENT = "Content"

QIF_KEY = "Key"
QIF_SYNONYM = "Synonym"
QIF_DESCRIPTION = "Description"

QIF_OLD = [QIF_KEY, QIF_SYNONYM, QIF_CONTENT, QIF_DESCRIPTION, QIF_CATALOGUE, QIF_TAG]
QIF = [QIF_NAME, QIF_NAME_EN, QIF_FROM, QIF_CATALOGUE, QIF_TAG, QIF_CONTENT]
QIF_HB = [QIF_NAME, QIF_NAME_EN, QIF_CATALOGUE, QIF_TAG, QIF_CONTENT]


def regexp(pattern: str, input: str) -> bool:
    """SQLite REGEXP / regexp function 入口。"""
    p = re.compile(str(pattern), re.I)
    return bool(re.search(p, input or ""))


def regexp_normalize(string: str) -> str:
    """将正则表达式的特殊字符转义成“原义文本”。"""
    new_string: str = ""
    for char in string:
        if char in "$()*+.[?\\^{|":
            new_string += "\\" + char
        else:
            new_string += char
    return new_string


class QueryStore:
    """
    统一管理 query 模块的 SQLite 连接与异步读写。

    该类替代了旧实现的 CONNECTED_QUERY_DATABASES / DATABASE_CURSOR 全局字典，
    并提供少量高层方法用于连接、写入与分页查询。
    """

    def __init__(self, base_dir: Optional[str] = None):
        self._base_dir = base_dir or os.path.join(DATA_PATH, "QueryData")
        self._conns: Dict[str, aiosqlite.Connection] = {}

    def _db_name_from_path(self, path: str) -> str:
        # 约定：xxx.db -> xxx
        return os.path.basename(path)[:-3]

    def _is_db_file(self, path: str) -> bool:
        return path.endswith(".db")

    async def connect_path(self, path: str) -> str:
        """
        连接单个 .db 或目录（递归加载其中的 .db）。

        返回值用于兼容旧实现的提示格式（错误信息用换行拼接）。
        """
        error_info: List[str] = []
        if path.endswith(".db-journal"):
            return "\n".join(error_info)

        if self._is_db_file(path):
            if not os.path.exists(path):
                error_info.append(f"未找到数据库文件: {path}")
                return "\n".join(error_info)

            db_name = self._db_name_from_path(path)
            if db_name in self._conns:
                error_info.append("已加载过该数据库。")
                return "\n".join(error_info)

            try:
                conn = await aiosqlite.connect(path)
                await conn.create_function("regexp", 2, regexp)
                self._conns[db_name] = conn
            except PermissionError:
                error_info.append(f"读取{path}时遇到错误: 权限不足")
            return "\n".join(error_info)

        # 目录：递归遍历
        if path and os.path.isdir(path):
            try:
                inner_paths = os.listdir(path)
                for inner_path in inner_paths:
                    child = os.path.join(path, inner_path)
                    child_info = await self.connect_path(child)
                    if child_info:
                        error_info.append(child_info)
            except FileNotFoundError as e:
                error_info.append(f"读取{path}时遇到错误: {e}")
            return "\n".join(error_info)

        # 非空但非有效路径：兼容旧逻辑，认为这是“创建空文件夹”
        if path and not os.path.exists(path):
            create_parent_dir(path)
        return "\n".join(error_info)

    def has_database(self, db_name: str) -> bool:
        return db_name in self._conns

    def list_databases(self) -> List[str]:
        return list(self._conns.keys())

    async def disconnect_database(self, db_name: str) -> None:
        conn = self._conns.get(db_name)
        if conn is None:
            return
        await conn.close()
        del self._conns[db_name]

    async def close_all(self) -> None:
        for db_name in list(self._conns.keys()):
            await self.disconnect_database(db_name)

    async def create_empty_database(self, path: str) -> bool:
        """创建一个空白查询数据库。"""
        try:
            create_parent_dir(path)
            conn = await aiosqlite.connect(path)
            await conn.execute(
                "CREATE TABLE data ("
                + ",".join([f"{field} TEXT DEFAULT ('')" for field in QUERY_DATA_FIELD_LIST])
                + ");"
            )
            await conn.execute("CREATE INDEX [From] ON data (来源 ASC);")
            await conn.execute("CREATE INDEX Catalogue ON data (分类);")
            await conn.execute(
                "CREATE TABLE redirect ("
                + ",".join([f"{field} TEXT DEFAULT ('')" for field in QUERY_REDIRECT_FIELD_LIST])
                + ");"
            )
            await conn.commit()
            await conn.close()
            return True
        except PermissionError:
            return False

    async def _get_conn(self, db_name: str) -> aiosqlite.Connection:
        conn = self._conns.get(db_name)
        if conn is None:
            raise RuntimeError(f"query database not loaded: {db_name}")
        return conn

    async def execute(
        self,
        db_name: str,
        sql: str,
        params: Sequence[Any] = (),
        *,
        commit: bool = False,
    ) -> aiosqlite.Cursor:
        conn = await self._get_conn(db_name)
        cur = await conn.execute(sql, tuple(params))
        if commit:
            await conn.commit()
        return cur

    async def fetchall(
        self,
        db_name: str,
        sql: str,
        params: Sequence[Any] = (),
    ) -> List[tuple]:
        conn = await self._get_conn(db_name)
        cur = await conn.execute(sql, tuple(params))
        rows = await cur.fetchall()
        return rows

    async def fetchone(
        self,
        db_name: str,
        sql: str,
        params: Sequence[Any] = (),
    ) -> Optional[tuple]:
        conn = await self._get_conn(db_name)
        cur = await conn.execute(sql, tuple(params))
        row = await cur.fetchone()
        return row

    async def executemany(
        self,
        db_name: str,
        sql: str,
        params_seq: Iterable[Sequence[Any]],
        *,
        commit: bool = False,
    ) -> None:
        conn = await self._get_conn(db_name)
        await conn.executemany(sql, params_seq)
        if commit:
            await conn.commit()

    def _prepare_insert_data(
        self,
        wb: Any,
        xlsx_name: str,
        xlsx_mode: int,
    ) -> Tuple[List[tuple], List[tuple]]:
        """将 xlsx Workbook 转成 data/redirect 的 INSERT 参数。"""

        def try_load_data(data: Any, default_var: str = "") -> str:
            return str(data).strip() if data else default_var

        if xlsx_mode == 0:
            data_dict = col_based_workbook_to_dict(wb, QIF_OLD, [])
        elif xlsx_mode == 1:
            data_dict = col_based_workbook_to_dict(wb, QIF, [])
        elif xlsx_mode == 2:
            data_dict = col_based_workbook_to_dict(wb, QIF_HB, [])
        else:
            data_dict = {}

        edit_cmd_data: List[tuple] = []
        edit_cmd_redirect: List[tuple] = []

        if len(data_dict.keys()) == 0:
            return [], []

        for sheet_name in data_dict.keys():
            sheet_data = data_dict[sheet_name]
            if xlsx_mode == 0:
                item_num = len(sheet_data[QIF_KEY])
                for item_index in range(item_num):
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

                    tags = ((item[5].strip()).replace(" ", "")).split()
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

                    edit_cmd_data.append((item[0], en_name, book, item[4], " ".join(tags), item[3]))

                    syns = item[1].split("/")
                    for syn in syns:
                        edit_cmd_redirect.append((syn, item[0]))

            elif xlsx_mode == 1:
                item_num = len(sheet_data[QIF_NAME])
                for item_index in range(item_num):
                    item = [try_load_data(sheet_data[QIF[sub_index]][item_index]) for sub_index in range(6)]
                    if len(item[0]) == 0:
                        continue
                    edit_cmd_data.append((item[0], item[1], item[2], item[3], item[4], item[5]))

            elif xlsx_mode == 2:
                item_num = len(sheet_data[QIF_NAME])
                for item_index in range(item_num):
                    item = [try_load_data(sheet_data[QIF_HB[sub_index]][item_index]) for sub_index in range(5)]
                    if len(item[0]) == 0:
                        continue
                    edit_cmd_data.append((item[0], item[1], "私设:" + xlsx_name, item[2], item[3], item[4]))

        return edit_cmd_data, edit_cmd_redirect

    async def load_data_from_xlsx_to_sqlite(
        self,
        xlsx_path: str,
        database_path: str,
        xlsx_mode: int,
    ) -> bool:
        """
        将 xlsx 写入某个 query 数据库。

        与旧实现的行为一致：不自动创建数据库文件；调用方需确保数据库已存在/已加载。
        """
        db_name = self._db_name_from_path(database_path)
        if not self.has_database(db_name):
            # 复用旧行为：如果没加载则尝试连接（连接失败后仍会抛异常给上层）
            await self.connect_path(database_path)

        wb = read_xlsx(xlsx_path)
        if not wb:
            return False

        xlsx_name = os.path.basename(xlsx_path)[:-5]

        # 私设模式下：先删除同一来源的旧记录
        if xlsx_mode == 2:
            # 来源字符串里含单引号时要做 SQL 转义（旧逻辑用 replace；这里保持一致）
            escaped = xlsx_name.replace("'", "''")
            await self.execute(
                db_name,
                "DELETE FROM data WHERE 来源 LIKE ?",
                (f"私设:{escaped}%",
                ),
                commit=True,
            )

        edit_cmd_data, edit_cmd_redirect = self._prepare_insert_data(wb, xlsx_name, xlsx_mode)
        if len(edit_cmd_data) == 0:
            return False

        # 批量写入（最后一次 commit）
        await self.executemany(
            db_name,
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            edit_cmd_data,
            commit=False,
        )
        if xlsx_mode in (0,):  # 老式会生成 redirect
            await self.executemany(
                db_name,
                "INSERT INTO redirect VALUES(?,?)",
                edit_cmd_redirect,
                commit=False,
            )
        await (await self._get_conn(db_name)).commit()
        return True

