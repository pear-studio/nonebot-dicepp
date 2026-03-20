import unittest
import pytest
from core.data.models.initiative import (
    InitEntity, InitList, InitiativeError, INIT_LIST_SIZE
)


@pytest.mark.unit
class TestInitEntity(unittest.TestCase):
    def test_init(self):
        entity = InitEntity()
        self.assertEqual(entity.name, "")
        self.assertEqual(entity.owner, "")
        self.assertEqual(entity.init, 0)

    def test_init_with_values(self):
        entity = InitEntity(name="勇者", owner="user123", init=15)
        self.assertEqual(entity.name, "勇者")
        self.assertEqual(entity.owner, "user123")
        self.assertEqual(entity.init, 15)

    def test_get_info(self):
        entity = InitEntity(name="怪物", init=20)
        info = entity.get_info()
        self.assertEqual(info, "怪物 先攻:20")


@pytest.mark.unit
class TestInitList(unittest.TestCase):
    def setUp(self):
        self.init_list = InitList(group_id="group123")

    def test_init(self):
        self.assertEqual(self.init_list.group_id, "group123")
        self.assertEqual(self.init_list.entities, [])
        self.assertEqual(self.init_list.round, 1)
        self.assertEqual(self.init_list.turn, 1)
        self.assertEqual(self.init_list.turns_in_round, 1)
        self.assertTrue(self.init_list.first_turn)

    def test_add_entity(self):
        self.init_list.add_entity("勇者", "user1", 15)
        self.assertEqual(len(self.init_list.entities), 1)
        self.assertEqual(self.init_list.entities[0].name, "勇者")
        self.assertEqual(self.init_list.entities[0].init, 15)

    def test_add_entity_sorted(self):
        self.init_list.add_entity("怪物A", "user1", 10)
        self.init_list.add_entity("勇者", "user2", 20)
        self.init_list.add_entity("怪物B", "user3", 15)
        self.assertEqual(self.init_list.entities[0].name, "勇者")
        self.assertEqual(self.init_list.entities[1].name, "怪物B")
        self.assertEqual(self.init_list.entities[2].name, "怪物A")

    def test_add_entity_replace_same_name(self):
        self.init_list.add_entity("勇者", "user1", 15)
        self.init_list.add_entity("勇者", "user2", 20)
        self.assertEqual(len(self.init_list.entities), 1)
        self.assertEqual(self.init_list.entities[0].init, 20)

    def test_add_entity_max_limit(self):
        for i in range(INIT_LIST_SIZE):
            self.init_list.add_entity(f"entity{i}", f"user{i}", i)
        with self.assertRaises(InitiativeError):
            self.init_list.add_entity("overflow", "user_overflow", 100)

    def test_del_entity(self):
        self.init_list.add_entity("勇者", "user1", 15)
        self.init_list.del_entity("勇者")
        self.assertEqual(len(self.init_list.entities), 0)

    def test_del_entity_not_found(self):
        with self.assertRaises(InitiativeError):
            self.init_list.del_entity("不存在")

    def test_serialization(self):
        self.init_list.add_entity("勇者", "user1", 20)
        self.init_list.add_entity("怪物", "user2", 10)
        serialized = self.init_list.model_dump_json()

        init_list2 = InitList.model_validate_json(serialized)
        self.assertEqual(len(init_list2.entities), 2)
        self.assertEqual(init_list2.entities[0].name, "勇者")

    def test_update_entity_init(self):
        self.init_list.add_entity("勇者", "user1", 15)
        self.init_list.add_entity("怪物", "user2", 10)
        self.init_list.entities[0].init = 25
        self.init_list.entities = sorted(self.init_list.entities, key=lambda x: -x.init)
        self.assertEqual(self.init_list.entities[0].name, "勇者")
        self.assertEqual(self.init_list.entities[0].init, 25)


@pytest.mark.unit
class TestInitiativeError(unittest.TestCase):
    def test_error_message(self):
        error = InitiativeError("测试错误")
        self.assertIn("测试错误", str(error))
        self.assertIn("Initiative", str(error))


if __name__ == '__main__':
    unittest.main()
