import unittest
import os

from core.data.manager import DataManager, DataManagerError
from core.data.data_chunk import DataChunkBase, custom_data_chunk
from core.data.json_object import JsonObject, custom_json_object

test_path = os.path.join(os.path.dirname(__file__), 'test_data')


class MyTestCase(unittest.TestCase):
    test_index = -1

    @classmethod
    def setUpClass(cls) -> None:
        cls.test_index = 0

        @custom_data_chunk(identifier="Test_A")
        class _(DataChunkBase):
            def __init__(self):
                super().__init__()

        @custom_data_chunk(identifier="Test_B")
        class _(DataChunkBase):
            def __init__(self):
                super().__init__()
                self.strict_check = True

    @classmethod
    def tearDownClass(cls) -> None:
        DataManager._instances = {}
        if os.path.exists(test_path):
            for root, dirs, files in os.walk(test_path, topdown=False):
                for name in files:
                    os.remove(os.path.join(root, name))
                    print(f"[Test TearDown] 清除文件{name}")
                for name in dirs:
                    os.rmdir(os.path.join(root, name))
                    print(f"[Test TearDown] 清除文件夹{name}")
            os.rmdir(test_path)
            print(f"[Test TearDown] 清除文件夹{test_path}")
        else:
            print(f"测试路径不存在! path:{test_path}")

    def setUp(self) -> None:
        self.test_index += 1

    def test0_save(self):
        print("开始测试保存功能")

        @custom_data_chunk(identifier=f"Test_Add_1")
        class _(DataChunkBase):
            def __init__(self):
                super().__init__()
        self.data_manager = DataManager(test_path)
        self.data_manager.set_data("Test_A", ["Level-1-A", "Attr-2-A"], 0)
        self.data_manager.set_data("Test_B", ["Level-1-A"], "This is test B!")
        self.data_manager.set_data("Test_B", ["Level-1-B", "Attr-2-A"], ["ABC", 123, {}])
        self.data_manager.set_data("Test_Add_1", ["Attr-1-A"], {"ABC": 1.5, "666": "666", "1": []})
        self.data_manager.save_data()
        print("保存成功!")

    def test1_load(self):
        print("开始测试读取功能")
        self.data_manager = DataManager(test_path)
        self.assertTrue(type(self.data_manager.get_data("Test_A", [])) is dict)
        self.assertEqual(self.data_manager.get_data("Test_A", ["Level-1-A", "Attr-2-A"]), 0)
        self.assertEqual(self.data_manager.get_data("Test_B", ["Level-1-A"]), "This is test B!")
        self.assertEqual(self.data_manager.get_data("Test_B", ["Level-1-B", "Attr-2-A"]), ["ABC", 123, {}])
        self.assertEqual(self.data_manager.get_data("Test_Add_1", ["Attr-1-A"]), {"ABC": 1.5, "666": "666", "1": []})
        print("读取数据正确!")

    def test1_get(self):
        self.data_manager = DataManager(test_path)
        copied_data = self.data_manager.get_data("Test_B", ["Level-1-B"])
        self.assertEqual(copied_data, {"Attr-2-A": ["ABC", 123, {}]})
        copied_data["Attr-2-A"][0] = "DEF"
        self.assertEqual(self.data_manager.get_data("Test_B", ["Level-1-B", "Attr-2-A"])[0], "ABC")
        self.assertNotEqual(id(copied_data), id(self.data_manager.get_data("Test_B", ["Level-1-B", "Attr-2-A"])))
        print("取得的数据是深拷贝!")
        not_exist_path = ["Level-1-A", "Attr-Not_Exist"]
        default_val = [0, 1, "A"]
        self.assertEqual(self.data_manager.get_data("Test_A", not_exist_path, default_val), default_val)
        self.assertEqual(self.data_manager.get_data("Test_A", not_exist_path), default_val)
        print("带默认值的get可以创建不存在的数据")

    def test2_obj(self):
        @custom_data_chunk(identifier=f"Test_Object", include_json_object=True)
        class _(DataChunkBase):
            def __init__(self):
                super().__init__()

        @custom_json_object
        class DumbJsonObject(JsonObject):
            def __init__(self):
                super().__init__()
                self.intField = 0
                self.floatField = 2.5
                self.strField = "ABC"
                self.ListField = [0, 1, "A", {"N1": -1}]
                self.DictField = {"A": 1, "B": 2, "C": [0, 1, "3"]}

            def serialize(self) -> str:
                json_dict = {
                    "i": self.intField,
                    "f": self.floatField,
                    "s": self.strField,
                    "l": self.ListField,
                    "d": self.DictField,
                }
                import json
                res = json.dumps(json_dict)
                return res

            def deserialize(self, json_str: str) -> None:
                import json
                json_dict = json.loads(json_str)
                self.intField = json_dict["i"]
                self.floatField = json_dict["f"]
                self.strField = json_dict["s"]
                self.ListField = json_dict["l"]
                self.DictField = json_dict["d"]
        self.data_manager = DataManager(test_path)
        dumb_obj_1: DumbJsonObject = DumbJsonObject()
        dumb_obj_1.intField = -1
        dumb_obj_1.floatField = 6.66
        dumb_obj_1.strField = "CBA"
        dumb_obj_2: DumbJsonObject = DumbJsonObject()
        dumb_obj_2.strField = ""

        self.data_manager.set_data("Test_Object", ["Level-1", "Dumb1"], dumb_obj_1)
        self.data_manager.set_data("Test_Object", ["Dumb2"], [dumb_obj_2])
        self.data_manager.save_data()
        data_manager_new = DataManager(test_path)
        dumb_obj_1 = data_manager_new.get_data("Test_Object", ["Level-1", "Dumb1"])
        dumb_obj_2 = data_manager_new.get_data("Test_Object", ["Dumb2"])[0]
        self.assertEqual(dumb_obj_1.intField, -1)
        self.assertEqual(dumb_obj_1.floatField, 6.66)
        self.assertEqual(dumb_obj_1.strField, "CBA")
        self.assertEqual(dumb_obj_2.strField, "")

    def test9_exception(self):
        print("开始测试异常")
        self.data_manager = DataManager(test_path)
        self.assertRaisesRegex(DataManagerError, "尝试在不给出默认值的情况下访问不存在的路径",
                               self.data_manager.get_data, "Test_A", ["Invalid-path"])
        print(self.data_manager.get_data("Test_A", ["Level-1-A", "Attr-2-A"]))
        self.assertRaisesRegex(DataManagerError, "尝试获取的路径非终端节点不是字典类型",
                               self.data_manager.get_data, "Test_A", ["Level-1-A", "Attr-2-A", "Invalid"], 0)

        self.data_manager.set_data("Test_A", ["Attr-1-A"], 0)
        self.data_manager.set_data("Test_A", ["Attr-1-A"], "1")
        self.assertEqual(self.data_manager.get_data("Test_A", ["Attr-1-A"], 0), "1")
        self.assertEqual(self.data_manager.get_data("Test_A", ["Attr-1-A"], []), "1")
        print("非严格类型检查效果正确")
        self.data_manager.set_data("Test_B", ["Attr-1-A"], 0)
        self.assertRaises(DataManagerError, self.data_manager.set_data, "Test_B", ["Attr-1-A"], "1")
        self.assertRaises(DataManagerError, self.data_manager.get_data, "Test_B", ["Attr-1-A"], "1")
        self.assertRaises(DataManagerError, self.data_manager.get_data, "Test_B", ["Attr-1-A"], [])
        self.assertEqual(self.data_manager.get_data("Test_B", ["Attr-1-A"]), 0)
        self.assertEqual(self.data_manager.get_data("Test_B", ["Attr-1-A"], 5), 0)
        print("严格类型检查效果正确")
        self.assertRaises(DataManagerError, self.data_manager.set_data, "Test_B", ["Attr-1-A", ""], 0)
        self.assertRaises(DataManagerError, self.data_manager.get_data, "Test_B", ["Attr-1-A", ""], 0)
        print("非空叶子节点检查通过")


if __name__ == '__main__':
    unittest.main()
