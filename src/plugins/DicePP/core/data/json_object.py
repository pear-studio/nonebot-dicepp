"""
继承JsonObject的类可以通过DataManager序列化或反序列
"""

import abc
from typing import Type, Dict

JSON_OBJECT_PREFIX = "JSON_OBJ_"


class JsonObject(metaclass=abc.ABCMeta):
    """
    可以存在Json中的Object, 并不代表一定要通过Json反序列化和序列化
    构造函数不能拥有参数! 原因见construct_from_json
    """
    def to_json(self) -> str:
        return JSON_OBJECT_PREFIX + type(self).__name__ + "$" + self.serialize().strip()

    @classmethod
    def construct_from_json(cls, json_str: str) -> 'JsonObject':
        """
        从一个json字符串中构造一个json object并返回
        Args:
            json_str: 格式见JsonObject.to_json方法
        Returns:

        """
        json_str = json_str[len(JSON_OBJECT_PREFIX):]
        cls_name, json_str = json_str.split("$", 1)
        json_cls: Type[JsonObject] = ALL_JSON_OBJ_DICT[cls_name]
        json_obj: JsonObject = json_cls()
        json_obj.deserialize(json_str)
        return json_obj

    @abc.abstractmethod
    def serialize(self) -> str:
        """
        将自身序列化为任意字符串
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def deserialize(self, json_str: str) -> None:
        """
        通过字符串将自身反序列化
        """
        raise NotImplementedError()


ALL_JSON_OBJ_DICT: Dict[str, Type[JsonObject]] = {}


def custom_json_object(cls):
    """
    类修饰器, 将自定义JsonObject注册到列表中
    """
    assert issubclass(cls, JsonObject)
    assert cls.__name__ not in ALL_JSON_OBJ_DICT
    ALL_JSON_OBJ_DICT[cls.__name__] = cls
    return cls


def construct_from_dict(obj: dict) -> 'JsonObject':
    """
    尝试从一个普通 dict 推断并构造合适的 JsonObject 实例。
    使用启发式匹配：对所有已注册 JsonObject 类实例化并比较属性名的匹配数量，
    选择匹配度最高（且至少匹配 1 个属性）的类来构造实例。
    如果无法匹配则返回 None。
    """
    if not isinstance(obj, dict) or not obj:
        return None
    best_cls = None
    best_score = 0
    keys = set(obj.keys())
    for cls_name, cls in ALL_JSON_OBJ_DICT.items():
        try:
            sample = cls()
        except Exception:
            continue
        attrs = set(sample.__dict__.keys())
        # score: number of overlapping attribute names
        score = len(attrs & keys)
        if score > best_score:
            best_score = score
            best_cls = cls
    if best_score == 0 or best_cls is None:
        return None
    try:
        inst = best_cls()
        # shallow set attributes present in dict
        for k, v in obj.items():
            try:
                setattr(inst, k, v)
            except Exception:
                pass
        return inst
    except Exception:
        return None
