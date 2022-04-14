"""
定义所需要的数据类型, 在DataManager中使用
自定义的DataChunk应当和需要它的方法一起定义, 但在此模块内定义也是可行的(不推荐)
"""
import abc
import copy
from typing import List, Type, Dict, Any

from utils.time import get_current_date_str
from utils.logger import dice_log

from core.data.json_object import JsonObject, JSON_OBJECT_PREFIX

DC_VERSION_LATEST = "1.0"  # 格式版本


class DataChunkBase(metaclass=abc.ABCMeta):
    """
    DataChunk是一次读取/更新文件的最小单位, 每个DataChunk子类都对应一个同名的持久化json文件
    为了方便阅读和管理, 一个DataChunk应当包括某一类功能所需要的全部数据, 也不应包含太多或太少内容
    不能拥有非基础类型, 自定义类型必须继承自DataManager.JsonObject! 否则无法序列化
    例子: 保存20000条某类数据, 每条数据200字节, 大概就是4MB
    """
    identifier = "basic_data"
    include_json_object = False

    def __init__(self):
        self.version_base: str = DC_VERSION_LATEST  # 如果修改了相关的代码, 可以通过版本号来将旧版本的数据转换到新版本
        self.dirty: bool = False  # 读取后是否被修改过
        self.strict_check: bool = False  # 是否严格地检查 已有的值 与 新值/默认值 拥有相同的类型
        self.update_time: str = get_current_date_str()  # 最后一次更新的时间
        self.root = {}  # 树形数据结构的根节点, 所有想要持久化的数据应该存放在这里
        self.hash_code = hash(self)  # 哈希校验码

    @classmethod
    def get_identifier(cls):
        """
        返回定位符, 由custom_data_chunk修饰符给出
        """
        return cls.identifier

    @classmethod
    def from_json(cls, json_dict: dict):
        """
        通过json格式的字符串反序列化生成一个实例并返回
        Args:
            json_dict: 以json格式字符串生成的字典
        Returns:
            obj: 生成的实例
        """

        # noinspection PyBroadException
        def deserialize_json_object_in_node(node: Any) -> None:
            if isinstance(node, dict):
                invalid_key = []
                for key, value in node.items():
                    if isinstance(value, dict) or isinstance(value, list):
                        deserialize_json_object_in_node(value)
                    elif isinstance(value, str) and value.find(JSON_OBJECT_PREFIX) == 0:  # 反序列化JsonObject
                        try:
                            node[key] = JsonObject.construct_from_json(value)
                        except Exception as e:
                            dice_log(f"[DataManager] [Load] 从字典中加载{key}: {value}时出现错误 {e}")
                            invalid_key.append(key)
                for key in invalid_key:
                    del node[key]
            elif isinstance(node, list):
                invalid_index = []
                for index, value in enumerate(node):
                    if isinstance(value, dict) or isinstance(value, list):
                        deserialize_json_object_in_node(value)
                    elif isinstance(value, str) and value.find(JSON_OBJECT_PREFIX) == 0:  # 处理Json Object
                        try:
                            node[index] = JsonObject.construct_from_json(value)
                        except Exception as e:
                            dice_log(f"[DataManager] [Load] 从列表中加载{index}: {value}时出现错误 {e}")
                            invalid_index.append(index)
                for index in reversed(invalid_index):  # 从后往前删除
                    del node[index]

        obj = cls()
        for k, v in json_dict.items():
            obj.__setattr__(k, v)
        if cls.include_json_object:
            deserialize_json_object_in_node(obj.root)
        try:
            obj.introspect()
        except AssertionError:
            dice_log(f"[DataManager] [Introspect] Invalidate {obj.identifier}")
            obj = cls()

        return obj

    def to_json(self) -> Dict:
        """
        将自己的__dict__处理成一个字典并返回
        """

        def serialize_json_object_in_node(node: Any) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    if isinstance(value, dict) or isinstance(value, list):
                        serialize_json_object_in_node(value)
                    elif isinstance(value, JsonObject):  # 处理Json Object
                        node[key] = value.to_json()
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    if isinstance(value, dict) or isinstance(value, list):
                        serialize_json_object_in_node(value)
                    elif isinstance(value, JsonObject):  # 处理Json Object
                        node[index] = value.to_json()

        if self.include_json_object:
            json_dict = copy.deepcopy(self.__dict__)
            serialize_json_object_in_node(json_dict)
            return json_dict
        else:
            return self.__dict__

    def __hash__(self):
        target_str = self.version_base + self.update_time + str(self.root)
        return hash(target_str)

    def introspect(self) -> None:
        pass


DATA_CHUNK_TYPES: List[Type[DataChunkBase]] = []  # 记录所有DataChunk的子类, 不需要手动修改, 应当通过修饰器CustomDC增加


def custom_data_chunk(identifier: str,
                      include_json_object=False):
    """
    类修饰器, 将自定义DataChunk注册到列表中
    Args:
        identifier: 一个字符串, 作为储存该DataChunk实例的名字, 应当是一个有区分度的名字, 不能含有空格, 也不能含有文件名中的非法字符
        include_json_object: 是否会含有Json Object类型, 如果为否, 在序列化时不会进行检查
    """

    def custom_inner(cls):
        """
        包裹函数
        Args:
            cls: 修饰的类必须继承自DataChunk
        Returns:
            cls: 返回修饰后的cls
        """
        assert issubclass(cls, DataChunkBase)
        assert " " not in identifier
        for dc in DATA_CHUNK_TYPES:
            assert dc.identifier != identifier
        cls.identifier = identifier
        cls.include_json_object = include_json_object
        cls.__name__ = "DataChunkClass" + identifier
        DATA_CHUNK_TYPES.append(cls)
        return cls

    return custom_inner
