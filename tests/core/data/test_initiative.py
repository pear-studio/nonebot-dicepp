import pytest
from core.data.models.initiative import (
    InitEntity, InitList, InitiativeError, INIT_LIST_SIZE
)


@pytest.mark.unit
class TestInitEntity:
    def test_init(self):
        entity = InitEntity()
        assert entity.name == ""
        assert entity.owner == ""
        assert entity.init == 0

    def test_init_with_values(self):
        entity = InitEntity(name="勇者", owner="user123", init=15)
        assert entity.name == "勇者"
        assert entity.owner == "user123"
        assert entity.init == 15

    def test_get_info(self):
        entity = InitEntity(name="怪物", init=20)
        info = entity.get_info()
        assert info == "怪物 先攻:20"


@pytest.mark.unit
class TestInitList:
    def setup_method(self):
        self.init_list = InitList(group_id="group123")

    def test_init(self):
        assert self.init_list.group_id == "group123"
        assert self.init_list.entities == []
        assert self.init_list.round == 1
        assert self.init_list.turn == 1
        assert self.init_list.turns_in_round == 1
        assert self.init_list.first_turn

    def test_add_entity(self):
        self.init_list.add_entity("勇者", "user1", 15)
        assert len(self.init_list.entities) == 1
        assert self.init_list.entities[0].name == "勇者"
        assert self.init_list.entities[0].init == 15

    def test_add_entity_sorted(self):
        self.init_list.add_entity("怪物A", "user1", 10)
        self.init_list.add_entity("勇者", "user2", 20)
        self.init_list.add_entity("怪物B", "user3", 15)
        assert self.init_list.entities[0].name == "勇者"
        assert self.init_list.entities[1].name == "怪物B"
        assert self.init_list.entities[2].name == "怪物A"

    def test_add_entity_replace_same_name(self):
        self.init_list.add_entity("勇者", "user1", 15)
        self.init_list.add_entity("勇者", "user2", 20)
        assert len(self.init_list.entities) == 1
        assert self.init_list.entities[0].init == 20

    def test_add_entity_max_limit(self):
        for i in range(INIT_LIST_SIZE):
            self.init_list.add_entity(f"entity{i}", f"user{i}", i)
        with pytest.raises(InitiativeError):
            self.init_list.add_entity("overflow", "user_overflow", 100)

    def test_del_entity(self):
        self.init_list.add_entity("勇者", "user1", 15)
        self.init_list.del_entity("勇者")
        assert len(self.init_list.entities) == 0

    def test_del_entity_not_found(self):
        with pytest.raises(InitiativeError):
            self.init_list.del_entity("不存在")

    def test_serialization(self):
        self.init_list.add_entity("勇者", "user1", 20)
        self.init_list.add_entity("怪物", "user2", 10)
        serialized = self.init_list.model_dump_json()

        init_list2 = InitList.model_validate_json(serialized)
        assert len(init_list2.entities) == 2
        assert init_list2.entities[0].name == "勇者"

    def test_update_entity_init(self):
        self.init_list.add_entity("勇者", "user1", 15)
        self.init_list.add_entity("怪物", "user2", 10)
        self.init_list.entities[0].init = 25
        self.init_list.entities = sorted(self.init_list.entities, key=lambda x: -x.init)
        assert self.init_list.entities[0].name == "勇者"
        assert self.init_list.entities[0].init == 25


@pytest.mark.unit
class TestInitiativeError:
    def test_error_message(self):
        error = InitiativeError("测试错误")
        assert "测试错误" in str(error)
        assert "Initiative" in str(error)

