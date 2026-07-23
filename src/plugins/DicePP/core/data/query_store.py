import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import aiosqlite

from plugins.DicePP.core.config.basic import Paths
from plugins.DicePP.utils import col_based_workbook_to_dict, create_parent_dir, read_xlsx


# 查询资料库表结构
QUERY_DATA_FIELD_LIST = ["名称", "英文", "来源", "分类", "标签", "内容"]
QUERY_REDIRECT_FIELD_LIST = ["名称", "重定向"]
QUERY_DATA_FIELD = ",".join(QUERY_DATA_FIELD_LIST)
QUERY_REDIRECT_FIELD = ",".join(QUERY_REDIRECT_FIELD_LIST)

# xlsx 解析映射
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


class QueryStoreError(RuntimeError):
    """查询数据库操作异常"""


class QueryStore:
    """
    统一管理 query 模块的 SQLite 连接与异步读写。

    该类替代了旧实现的 CONNECTED_QUERY_DATABASES / DATABASE_CURSOR 全局字典，
    并提供少量高层方法用于连接、写入与分页查询。
    """

    def __init__(self, base_dir: Optional[str] = None):
        self._base_dir = base_dir or str(Paths.CONTENT_QUERIES_DIR)
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
                await conn.execute("PRAGMA journal_mode=WAL;")
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
        conn = None
        try:
            create_parent_dir(path)
            conn = await aiosqlite.connect(path)
            await conn.execute("PRAGMA journal_mode=WAL;")
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
            return True
        except PermissionError:
            return False
        finally:
            if conn is not None:
                await conn.close()

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

    # ── 共享搜索逻辑 ──────────────────────────────────────────

    @staticmethod
    def _generate_search_sql_regexp(
        command_list: List[str],
        prefix: str = "名称",
    ) -> Tuple[str, List[str]]:
        result: List[str] = []
        for command in command_list:
            if command.startswith("-") and len(command) > 1:
                result.append(f"^(?!.*{regexp_normalize(command[1:])})")
            elif command.startswith("=") and len(command) > 1:
                result.append(f"^{regexp_normalize(command[1:])}$")
            elif len(command) > 0:
                result.append(regexp_normalize(command))

        pattern = "|".join(result)
        return f"{prefix} regexp ?", [pattern]

    @staticmethod
    def _generate_search_sql_in(
        command_list: List[str],
        prefix: str = "来源",
    ) -> Tuple[str, List[str]]:
        words: List[str] = []
        anti_words: List[str] = []
        for command in command_list:
            if command.startswith("-") and len(command) > 1:
                anti_words.append(command[1:])
            elif command.startswith("=") and len(command) > 1:
                words.append(command[1:])
            elif len(command) > 0:
                words.append(command)

        clauses: List[str] = []
        params: List[str] = []

        if words:
            placeholders = ",".join(["?"] * len(words))
            clauses.append(f"{prefix} in ({placeholders})")
            params.extend(words)

        if anti_words:
            placeholders = ",".join(["?"] * len(anti_words))
            clauses.append(f"{prefix} not in ({placeholders})")
            params.extend(anti_words)

        if not clauses:
            return "1=0", []

        if len(clauses) == 1:
            return clauses[0], params

        return f"({clauses[0]} OR {clauses[1]})", params

    @staticmethod
    def _generate_search_conditions(
        condition_list: Dict[tuple, List[List[str]]],
    ) -> Tuple[str, List[Any]]:
        sql_fragments: List[str] = []
        params: List[Any] = []

        for key_list in condition_list.keys():
            cmd_groups = condition_list[key_list]
            if len(cmd_groups) == 0:
                continue

            key_sql_parts: List[str] = []
            key_params: List[Any] = []

            if "全部" in key_list:
                for command in cmd_groups:
                    sql_part, part_params = QueryStore._generate_search_sql_regexp(
                        command, "名称||英文||来源||分类||标签||内容"
                    )
                    key_sql_parts.append(sql_part)
                    key_params.extend(part_params)
            elif key_list == ("分类",):
                for command in cmd_groups:
                    sql_part, part_params = QueryStore._generate_search_sql_in(command, "分类")
                    key_sql_parts.append(sql_part)
                    key_params.extend(part_params)
            else:
                for command in cmd_groups:
                    sql_part, part_params = QueryStore._generate_search_sql_regexp(
                        command, "||".join(key_list)
                    )
                    key_sql_parts.append(sql_part)
                    key_params.extend(part_params)

            if len(key_sql_parts) != 0:
                sql_fragments.append("(" + " AND ".join(key_sql_parts) + ")")
                params.extend(key_params)

        return " AND ".join(sql_fragments), params

    async def _search_single_db(
        self,
        db_name: str,
        query_tokens: List[str],
        fulltext: bool,
        limit: int,
        offset: int,
    ) -> List[Dict[str, str]]:
        """在单个数据库中执行搜索，返回 dict 列表（含 redirect 解析与去重）。"""
        sql_search_command_prefix: str = "Select * From data Where "
        sql_redirect_command_prefix: str = "Select * From redirect Where "

        condition_list: Dict[tuple, List[List[str]]] = {}
        complete_name: str = ""
        complete_name_en: str = ""
        can_single_query: bool = True

        for query_command in query_tokens:
            cmd_target = ["名称", "英文"]
            if query_command[0] == "#":
                cmd_target = ["来源", "分类", "标签"]
                query_command = query_command[1:]
                can_single_query = False
            elif query_command[0] == "&":
                cmd_target = ["分类"]
                query_command = query_command[1:]
                can_single_query = False
            elif fulltext:
                cmd_target = ["全部"]
                can_single_query = False

            command_list = [command for command in query_command.split("/")]
            if not any(command_list):
                continue

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

        # 主查询（SQL 层 LIMIT 减少 I/O）
        sql_condition, params = self._generate_search_conditions(condition_list)
        sql_limit = limit * 2  # 预留余量给 redirect 追加结果
        rows = await self.fetchall(
            db_name,
            sql_search_command_prefix + sql_condition + f" LIMIT {sql_limit}",
            params,
        )
        results: List[Dict[str, str]] = []
        for _data in rows:
            results.append({
                "name": _data[0], "name_en": _data[1], "source": _data[2],
                "catalogue": _data[3], "tag": _data[4], "content": _data[5],
                "redirect_by": "",
            })

        # 处理重定向
        redirect_condition_list: Dict[tuple, List[List[str]]] = {
            ("名称",): []
        }
        sql_condition_list: Dict[tuple, List[List[str]]] = {
            ("名称",): []
        }
        for key_list in condition_list.keys():
            if "全部" in key_list or "名称" in key_list:
                redirect_condition_list[("名称",)] += condition_list[key_list]
            else:
                sql_condition_list[key_list] = condition_list[key_list]

        if len(redirect_condition_list[("名称",)]) != 0:
            redirect_result: List[List[str]] = []
            sql_condition, params = self._generate_search_conditions(redirect_condition_list)
            redirect_rows = await self.fetchall(
                db_name,
                sql_redirect_command_prefix + sql_condition,
                params,
            )
            for _data in redirect_rows:
                redirect_result.append([_data[0], _data[1]])

            for _redirect in redirect_result:
                sql_condition_list[("名称",)] = [[_redirect[1]]]
                sql_condition, params = self._generate_search_conditions(sql_condition_list)
                redirected_rows = await self.fetchall(
                    db_name,
                    sql_search_command_prefix + sql_condition,
                    params,
                )
                for _data in redirected_rows:
                    results.append({
                        "name": _data[0], "name_en": _data[1], "source": _data[2],
                        "catalogue": _data[3], "tag": _data[4], "content": _data[5],
                        "redirect_by": _redirect[0],
                    })

        # 去重 + 精确匹配优先
        dupe_list: Set[str] = set()
        new_results: List[Dict[str, str]] = []
        found_equal: bool = False
        for r in results:
            hash_word = r["name"] + "#" + r["source"] + "#" + r["catalogue"]
            if can_single_query:
                if complete_name != "" and r["name"] == complete_name:
                    if not found_equal:
                        dupe_list.clear()
                        new_results.clear()
                    found_equal = True
                    if hash_word not in dupe_list:
                        dupe_list.add(hash_word)
                        new_results.append(r)
                elif complete_name_en != "" and r["name"].lower() == complete_name_en:
                    if not found_equal:
                        dupe_list.clear()
                        new_results.clear()
                    found_equal = True
                    if hash_word not in dupe_list:
                        dupe_list.add(hash_word)
                        new_results.append(r)
            if not found_equal:
                if hash_word not in dupe_list:
                    dupe_list.add(hash_word)
                    new_results.append(r)

        return new_results[offset:offset + limit]

    async def search(
        self,
        databases: List[str],
        query_tokens: List[str],
        fulltext: bool = False,
        limit: int = 5,
        offset: int = 0,
        max_total: int = 1000,
    ) -> dict:
        """搜索多个资料库，返回 dict。

        Returns:
            {"results": [...], "total": int}

        Raises:
            QueryStoreError: total > max_total 或数据库未加载
        """
        if not query_tokens:
            return {"results": [], "total": 0}

        if not databases:
            return {"results": [], "total": 0}

        for db in databases:
            if not self.has_database(db):
                raise QueryStoreError(f"数据库未加载: {db}")

        # 主库搜索
        all_results = await self._search_single_db(
            databases[0], query_tokens, fulltext, max_total + 1, 0,
        )

        # 私设库合并（私设覆盖同名主库条目）
        for hb_db in databases[1:]:
            hb_results = await self._search_single_db(
                hb_db, query_tokens, fulltext, max_total + 1, 0,
            )
            for hb in hb_results[::-1]:
                for main in all_results[::-1]:
                    if hb["name"] == main["name"]:
                        all_results.remove(main)
                if hb["content"].strip():
                    all_results.append(hb)

        total = len(all_results)
        if total > max_total:
            raise QueryStoreError(f"result count {total} exceeds max_total={max_total}")

        return {"results": all_results[offset:offset + limit], "total": total}

    async def get_database_info(self, db_name: str) -> Optional[dict]:
        """获取资料库元数据，未加载则返回 None。"""
        if not self.has_database(db_name):
            return None
        row = await self.fetchone(db_name, "SELECT COUNT(*) FROM data")
        rows = row[0] if row else 0
        cat_rows = await self.fetchall(db_name, "SELECT DISTINCT 分类 FROM data")
        categories = [r[0] for r in cat_rows if r[0]]
        return {"name": db_name, "rows": rows, "categories": categories}

