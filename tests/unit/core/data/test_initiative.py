import pytest
from plugins.DicePP.core.data.models.initiative import (
    InitEntity, InitList, InitiativeError, INIT_LIST_SIZE
)


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

    def test_del_entity_duplicates_removed_all(self):
        # 历史 bug 可能产生完全相同的重复条目, del 应一并清除而不是报错
        self.init_list.add_entity("勇者", "user1", 15)
        self.init_list.add_entity("怪物", "user2", 10)
        self.init_list.entities.append(InitEntity(name="勇者", owner="user1", init=15))
        self.init_list.del_entity("勇者")
        assert [e.name for e in self.init_list.entities] == ["怪物"]
        assert self.init_list.turns_in_round == 1

    def test_del_entity_duplicates_adjust_turn(self):
        for name, init in [("A", 60), ("B", 50), ("C", 40), ("D", 10)]:
            self.init_list.add_entity(name, "u", init)
        # 注入重复同名条目: [A, B, X, C, X, D]
        self.init_list.entities.insert(2, InitEntity(name="X", owner="u", init=45))
        self.init_list.entities.insert(4, InitEntity(name="X", owner="u", init=45))
        self.init_list.first_turn = False
        self.init_list.turn = 4  # 指向 C
        self.init_list.turns_in_round = 6
        self.init_list.del_entity("X")
        assert [e.name for e in self.init_list.entities] == ["A", "B", "C", "D"]
        assert self.init_list.turns_in_round == 4
        assert self.init_list.turn == 3  # 仍指向 C
        assert self.init_list.round == 1

    def test_del_entity_duplicates_wrap_round(self):
        # turn 指向被删的重复条目本身: 平移后仍越界, 回绕一轮
        self.init_list.add_entity("A", "u", 40)
        self.init_list.add_entity("B", "u", 30)
        self.init_list.entities.append(InitEntity(name="X", owner="u", init=20))
        self.init_list.entities.append(InitEntity(name="X", owner="u", init=20))
        # entities: [A, B, X, X]
        self.init_list.first_turn = False
        self.init_list.turn = 3  # 指向第一个 X
        self.init_list.turns_in_round = 4
        self.init_list.del_entity("X")
        assert [e.name for e in self.init_list.entities] == ["A", "B"]
        assert self.init_list.turns_in_round == 2
        assert self.init_list.turn == 1
        assert self.init_list.round == 2

    def test_del_entity_duplicates_after_turn_keeps_turn(self):
        # 重复条目全部位于 turn 之后: turn 与 round 均不变
        for name, init in [("A", 50), ("B", 40), ("C", 30)]:
            self.init_list.add_entity(name, "u", init)
        self.init_list.entities.append(InitEntity(name="X", owner="u", init=20))
        self.init_list.entities.append(InitEntity(name="X", owner="u", init=20))
        # entities: [A, B, C, X, X]
        self.init_list.first_turn = False
        self.init_list.turn = 2  # 指向 B
        self.init_list.turns_in_round = 5
        self.init_list.del_entity("X")
        assert [e.name for e in self.init_list.entities] == ["A", "B", "C"]
        assert self.init_list.turn == 2
        assert self.init_list.round == 1

    def test_add_entity_replaces_duplicate_same_name(self):
        # 已存在重复同名条目时, add 应清掉全部重复再加新条目
        self.init_list.add_entity("勇者", "user1", 15)
        self.init_list.add_entity("怪物", "user2", 10)
        self.init_list.entities.append(InitEntity(name="勇者", owner="user1", init=15))
        self.init_list.add_entity("勇者", "user1", 20)
        hero = [e for e in self.init_list.entities if e.name == "勇者"]
        assert len(self.init_list.entities) == 2
        assert len(hero) == 1
        assert hero[0].init == 20

    def test_serialization(self):
        self.init_list.add_entity("勇者", "user1", 20)
        self.init_list.add_entity("怪物", "user2", 10)
        serialized = self.init_list.model_dump_json()

        init_list2 = InitList.model_validate_json(serialized)
        assert len(init_list2.entities) == 2
        assert init_list2.entities[0].name == "勇者"

    def test_manual_sort_after_direct_field_mutation(self):
        self.init_list.add_entity("勇者", "user1", 15)
        self.init_list.add_entity("怪物", "user2", 10)
        self.init_list.entities[0].init = 25
        self.init_list.entities = sorted(self.init_list.entities, key=lambda x: -x.init)
        assert self.init_list.entities[0].name == "勇者"
        assert self.init_list.entities[0].init == 25


class TestInitiativeError:
    def test_error_message(self):
        error = InitiativeError("测试错误")
        assert "测试错误" in str(error)
        assert "Initiative" in str(error)

