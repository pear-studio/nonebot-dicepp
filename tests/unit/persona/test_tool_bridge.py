"""tool_bridge._dynamic_model 测试 — 动态 Pydantic model 构建"""
import pytest
from pydantic import BaseModel, ValidationError

from plugins.DicePP.module.persona.agent.tool_bridge import _dynamic_model, _json_type


class TestDynamicModel:
    """_dynamic_model: 从 JSON schema 构建 Pydantic model"""

    def test_required_fields(self):
        """必填字段应正确创建"""
        params = {
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
        Model = _dynamic_model("Search", params)
        assert issubclass(Model, BaseModel)
        # 必填字段不传应报错
        with pytest.raises(ValidationError):
            Model()
        # 正常传值
        m = Model(query="test")
        assert m.query == "test"

    def test_optional_fields_with_default(self):
        """有默认值的可选字段"""
        params = {
            "properties": {"count": {"type": "integer", "default": 10}},
            "required": [],
        }
        Model = _dynamic_model("Pager", params)
        m = Model()
        assert m.count == 10
        m2 = Model(count=5)
        assert m2.count == 5

    def test_optional_fields_without_default(self):
        """无默认值的可选字段应为 None"""
        params = {
            "properties": {"tag": {"type": "string"}},
            "required": [],
        }
        Model = _dynamic_model("Filter", params)
        m = Model()
        assert m.tag is None

    def test_list_default_factory(self):
        """list 类型可选字段默认为空列表"""
        params = {
            "properties": {"items": {"type": "array"}},
            "required": [],
        }
        Model = _dynamic_model("ListTest", params)
        m = Model()
        assert m.items == []

    def test_dict_default_factory(self):
        """dict 类型可选字段默认为空字典"""
        params = {
            "properties": {"meta": {"type": "object"}},
            "required": [],
        }
        Model = _dynamic_model("DictTest", params)
        m = Model()
        assert m.meta == {}

    def test_mixed_fields(self):
        """混合必填和可选字段"""
        params = {
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer", "default": 1},
                "tags": {"type": "array"},
            },
            "required": ["name"],
        }
        Model = _dynamic_model("Mixed", params)
        m = Model(name="test")
        assert m.name == "test"
        assert m.count == 1
        assert m.tags == []

    def test_empty_properties(self):
        """空 properties 应创建空 model"""
        params = {"properties": {}, "required": []}
        Model = _dynamic_model("Empty", params)
        m = Model()
        assert isinstance(m, BaseModel)


class TestJsonType:
    """_json_type: JSON schema type → Python type 映射"""

    def test_string(self):
        assert _json_type("string") is str

    def test_integer(self):
        assert _json_type("integer") is int

    def test_number(self):
        assert _json_type("number") is float

    def test_boolean(self):
        assert _json_type("boolean") is bool

    def test_object(self):
        assert _json_type("object") is dict

    def test_array(self):
        assert _json_type("array") is list

    def test_unknown_defaults_to_str(self):
        assert _json_type("unknown_type") is str
