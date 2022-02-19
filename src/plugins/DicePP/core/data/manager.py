"""
负责管理数据的类
若要定义新的数据类型, 请参考DataChunk中的模块
"""

import os
import copy
import asyncio
from json import JSONDecodeError
from typing import Tuple, List, Dict, Any, Optional, Callable

from utils.logger import dice_log
from utils.localdata import update_json_async, read_json

from core.config import DATA_PATH as ROOT_DATA_PATH

from core.data.data_chunk import DATA_CHUNK_TYPES, DataChunkBase


class DataManager:
    """
    负责管理持久化数据的类
    所有的持久化数据都应该通过DataManager的实例来获取/修改/更新
    初始化时会自动根据DataChunk中的内容来生成数据格式
    """

    def __init__(self, data_path: str):
        """
        Args:
            data_path: 存放所有持久化数据的文件目录
        """
        self.dataPath = data_path
        if not os.path.exists(data_path):
            os.makedirs(data_path)
            dice_log(f"[DataManager] [Init] 创建文件夹: {data_path.replace(ROOT_DATA_PATH, '~')}")

        self.__dataChunks: Dict[str, DataChunkBase] = {}
        self.load_data()

    def get_data(self, target: str, path: List[str],
                 default_val: Optional[Any] = None, default_gen: Optional[Callable[[], Any]] = None,
                 get_ref: bool = False) -> Any:
        """
        从DataManager中取得数据, 若该数据不存在, 则用defaultVal创建该数据并返回
        如果不指定defaultVal, 访问不存在的数据将会抛出一个异常
        如果path为空列表会返回dataChunk的root, 即所有数据, 注意返回的是拷贝, 开销可能比较大
        Args:
            target(str): 目标DataChunk的名字, 通过identifier定义
            path(Tuple[str]): 路径节点
            default_val(Optional[Any]): 数据默认值, 如果给出默认值, 在访问不存在的数据时会自动创建该数据, 否则抛出异常
            default_gen(Optional[Callable[]]): 数据默认值生成器, 如果有数据默认值, 则以默认值优先, 否则调用生成器得到默认值
            get_ref(bool): 返回数据的拷贝还是引用, 默认返回拷贝, 返回引用容易污染数据
        Returns:
            data(Any): 取得的数据
        """
        if len(path) > 1 and not path[-1]:
            raise DataManagerError(f"[GetData] 叶子结点的名称不能为空 完整路径: {path}")

        data_chunk = self.__get_data_chunk(target)
        strict_check = data_chunk.strict_check
        parent_node = data_chunk.root
        cur_node = parent_node
        for i in range(len(path)):
            is_last = (i == len(path) - 1)
            # 当前层级的缺省值
            if is_last:
                if default_val is None and default_gen is not None:
                    default_val_cur = default_gen()
                else:
                    default_val_cur = default_val
            else:
                # 如果当前节点不是最终目标节点且缺省值不为None, 则用空字典作为缺省值
                default_val_cur = dict() if (default_val is not None or default_gen is not None) else None

            # 获取当前节点的内容
            cur_path = path[i]
            if type(parent_node) is not dict:
                raise DataManagerError(f"[GetData] 尝试获取的路径非终端节点不是字典类型! 类型: {type(parent_node)}")

            if cur_path not in parent_node:  # 不存在则用缺省值设置
                if default_val_cur is None:
                    raise DataManagerError(f"[GetData] 尝试在不给出默认值的情况下访问不存在的路径! 路径: {path}")
                parent_node[cur_path] = default_val_cur
                data_chunk.dirty = True
            cur_node = parent_node[cur_path]
            if strict_check:  # 检查是否与默认值拥有相同类型
                if default_val_cur is not None and type(cur_node) != type(default_val_cur):
                    raise DataManagerError(f"[GetData] 无法通过严格的类型检查,"
                                           f" {type(cur_node)} != {type(default_val_cur)}\n"
                                           f"路径: {path} 当前节点: {path[i]} 已有值:{cur_node}")
            parent_node = cur_node

        if get_ref:
            return cur_node
        else:  # 默认返回拷贝
            return copy.deepcopy(cur_node)

    def set_data(self, target: str, path: List[str], new_val: Any) -> None:
        """
        设置DataManager中保存的数据
        Args:
            target(str): 目标DataChunk的名字, 通过identifier定义
            path(Tuple[str]): 路径节点
            new_val(Any): 要设置的数据值
        """
        if len(path) > 1 and not path[-1]:
            raise DataManagerError(f"[SetData] 叶子结点的名称不能为空 完整路径: {path}")

        data_chunk = self.__get_data_chunk(target)
        strict_check = data_chunk.strict_check
        parent_node = data_chunk.root
        for i in range(len(path)):
            # 如果当前节点不是最终目标节点, 则用空字典作为缺省值
            is_last = (i == len(path) - 1)
            if is_last:
                new_val_cur = new_val
            else:
                new_val_cur = dict()  # 中间节点

            # 获取当前节点的内容
            cur_path = path[i]

            if type(parent_node) is not dict:
                raise DataManagerError(f"[SetData] 尝试获取的路径非终端节点不是字典类型! {type(parent_node)}")

            if strict_check and cur_path in parent_node:  # 检查已有值是否与新值拥有相同类型
                cur_node = parent_node[cur_path]
                if type(cur_node) != type(new_val_cur):
                    raise DataManagerError(f"[SetData] 无法通过严格的类型检查, {type(cur_node)} != {type(new_val_cur)}\n"
                                           f"路径: {path} 当前节点: {path[i]} 已有值:{cur_node} 新值:{new_val_cur}")

            # 节点不存在或已经是目标节点值和新值不符合
            if (cur_path not in parent_node) or (is_last and parent_node[cur_path] != new_val_cur):
                data_chunk.dirty = True
                parent_node[cur_path] = new_val_cur

            parent_node = parent_node[cur_path]  # 继续访问下一节点
        return

    def delete_data(self, target: str, path: List[str], force_delete: bool = False, ignore_miss: bool = True) -> Any:
        """
        从DataManager中删除数据, 若该数据不存在且ignore_miss为False, 则会抛出异常
        如果path为空列表会清除该dataChunk的所有数据, 仅当force_delete为True时生效, 否则抛出异常
        Args:
            target(str): 目标DataChunk的名字, 通过identifier定义
            path(Tuple[str]): 路径节点
            force_delete(bool): 是否允许删除所有数据
            ignore_miss(bool): 想删除的数据不存在时是否抛出异常
        Returns:
            data(Any): 被删除的数据
        """
        data_chunk = self.__get_data_chunk(target)
        parent_node = data_chunk.root
        cur_node = parent_node

        if not path:
            if force_delete:
                data_chunk.root = {}
                return cur_node
            else:
                raise DataManagerError(f"[DeleteData] 尝试非安全地删除所有数据!")

        for i in range(len(path)):
            is_last = (i == len(path) - 1)

            # 获取当前节点的内容
            cur_path = path[i]
            if type(parent_node) is not dict:
                raise DataManagerError(f"[DeleteData] 尝试获取的路径非终端节点不是字典类型! 类型: {type(parent_node)}")

            if cur_path not in parent_node:  # 不存在则抛出异常
                if ignore_miss:
                    return None
                else:
                    raise DataManagerError(f"[DeleteData] 无法删除不存在的路径")
            cur_node = parent_node[cur_path]

            if is_last:
                del parent_node[cur_path]
            parent_node = cur_node

        return cur_node

    def get_keys(self, target: str, path: List[str]):
        """类似get_data, 但是不会返回数据的拷贝, 而是返回当前path的所有key, 当前path不存在或不是dict则抛出异常"""
        if len(path) > 1 and not path[-1]:
            raise DataManagerError(f"[GetData] 叶子结点的名称不能为空 完整路径: {path}")

        data_chunk = self.__get_data_chunk(target)
        parent_node = data_chunk.root
        cur_node = parent_node
        for i in range(len(path)):
            # 获取当前节点的内容
            if type(parent_node) is not dict:
                raise DataManagerError(f"[GetKey] 尝试获取的路径节点不是字典类型! 类型: {type(parent_node)}")

            cur_path = path[i]
            if cur_path not in parent_node:  # 不存在则抛出异常
                raise DataManagerError(f"[GetKey] 尝试访问不存在的路径! 路径: {path}")
            cur_node = parent_node[cur_path]
            if type(cur_node) is not dict:
                raise DataManagerError(f"[GetKey] 尝试获取的路径节点不是字典类型! 类型: {type(cur_node)}")
            parent_node = cur_node
        return cur_node.keys()

    def __get_data_chunk(self, target: str) -> DataChunkBase:
        if target not in self.__dataChunks:
            raise DataManagerError(f"[GetDataChunk] 找不到指定的DataChunk: {target}")
        data_chunk = self.__dataChunks[target]
        if not issubclass(type(data_chunk), DataChunkBase):
            raise DataManagerError(f"[GetDataChunk] 找到的变量({type(data_chunk)})不是继承于{DataChunkBase}!")
        return data_chunk

    def load_data(self):
        """
        从本地文件中读取数据, 会完全用本地文件覆盖内存中的信息
        """
        self.__dataChunks: Dict[str, DataChunkBase] = dict()
        for dcType in DATA_CHUNK_TYPES:
            dc_name = dcType.get_identifier()
            json_path = os.path.join(self.dataPath, f"{dc_name}.json")
            json_path_readable = json_path.replace(ROOT_DATA_PATH, "~")
            json_path_tmp = json_path + ".tmp"
            json_path_tmp_readable = json_path_tmp.replace(ROOT_DATA_PATH, "~")
            if os.path.exists(json_path_tmp):  # 先看看能不能从临时文件恢复, 临时文件应该比正式文件更新
                try:
                    json_dict = read_json(json_path_tmp)
                    self.__dataChunks[dc_name] = dcType.from_json(json_dict)
                    dice_log(f"[DataManager] [Init] 从备份{json_path_tmp_readable}中载入{dc_name}")
                    continue
                except JSONDecodeError as e:
                    dice_log(f"[DataManager] [Init] 无法从备份{json_path_tmp_readable}中载入{dc_name}: {e.args}")
            elif os.path.exists(json_path):  # 如果存在该文件, 则读取json文件并用它初始化数据
                try:
                    json_dict = read_json(json_path)
                    self.__dataChunks[dc_name] = dcType.from_json(json_dict)
                    dice_log(f"[DataManager] [Init] 从{json_path_readable}中载入{dc_name}")
                    continue
                except JSONDecodeError as e:
                    if not os.path.exists(json_path_tmp):
                        dice_log(f"[DataManager] [Init] 无法从{json_path_readable}中载入{dc_name}: {e.args}")
            # 文件不存在则用默认构造函数生成一个数据对象
            self.__dataChunks[dc_name] = dcType()
            # logger.dice_log(f"[DataManager] [Init] 找不到{json_path_readable}, 使用空白数据")

    async def save_data_async(self):
        for dataChunk in self.__dataChunks.values():
            if not dataChunk.dirty:  # 没有被修改过则不需要更新
                continue
            dataChunk.dirty = False
            dc_name = dataChunk.get_identifier()
            dataChunk.hash_code = hash(dataChunk)
            json_path = os.path.join(self.dataPath, f"{dc_name}.json")
            # 为了安全起见, 先将文件保存在临时文件中
            json_path_readable = json_path.replace(ROOT_DATA_PATH, "~")
            json_path_tmp = json_path + ".tmp"
            json_path_tmp_readable = json_path_tmp.replace(ROOT_DATA_PATH, "~")
            try:
                await update_json_async(dataChunk.to_json(), json_path_tmp)
            except JSONDecodeError as e:
                dice_log(f"[SaveData] 序列化过程中出现错误: {e.msg}")
                dataChunk.dirty = True
                continue
            # 删除正式文件
            try:
                if os.path.exists(json_path):
                    os.remove(json_path)
            except OSError as e:
                dice_log(f"[SaveData] 无法删除文件{json_path_readable}: {e.args}")
                dataChunk.dirty = True
                continue
            # 重命名临时文件
            try:
                os.rename(json_path_tmp, json_path)
            except OSError as e:
                dice_log(f"[SaveData] 无法重命名文件{json_path_tmp_readable} -> {json_path_readable} 原因: {e.args}")
                dataChunk.dirty = True
                continue

    def save_data(self):
        """
        尝试将被修改过的所有Data Chunks写入到硬盘中, 失败抛出一个DataManagerError
        """
        asyncio.run(self.save_data_async())


class DataManagerError(Exception):
    """
    DataManager产生的异常, 说明操作失败的原因, 应当在上一级捕获
    """
    def __init__(self, info: str):
        self.info = info

    def __str__(self):
        return f"[DataManager] [Error] {self.info}"
