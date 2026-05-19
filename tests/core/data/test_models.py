import pytest

pytestmark = pytest.mark.unit

from core.data.models import (
    InitEntity,
    InitList,
    DNDCharacter,
    HPInfo,
    AbilityInfo,
)


class TestInitListModel:
    def test_create(self):
        init_list = InitList()
        assert len(init_list.entities) == 0
        assert init_list.round == 1

    def test_add_entity(self):
        init_list = InitList()
        init_list.add_entity("Goblin", "", 10)
        assert len(init_list.entities) == 1
        assert init_list.entities[0].name == "Goblin"

    def test_add_entity_sorted(self):
        init_list = InitList()
        init_list.add_entity("Goblin", "", 10)
        init_list.add_entity("Orc", "", 15)
        init_list.add_entity("Elf", "", 12)

        assert init_list.entities[0].name == "Orc"
        assert init_list.entities[1].name == "Elf"
        assert init_list.entities[2].name == "Goblin"

    def test_del_entity(self):
        init_list = InitList()
        init_list.add_entity("Goblin", "", 10)
        init_list.del_entity("Goblin")
        assert len(init_list.entities) == 0

    def test_del_entity_not_found(self):
        from core.data.models import InitiativeError
        init_list = InitList()
        with pytest.raises(InitiativeError):
            init_list.del_entity("NotExists")


class TestDNDCharacterModel:
    def test_create(self):
        character = DNDCharacter(group_id="group1", user_id="user1", name="TestChar")
        assert character.name == "TestChar"
        assert character.is_init is False

    def test_nested_hp_info(self):
        character = DNDCharacter(group_id="group1", user_id="user1", name="TestChar")
        character.hp_info.hp_cur = 10
        character.hp_info.hp_max = 20
        assert character.hp_info.hp_cur == 10
        assert character.hp_info.hp_max == 20

    def test_serialization_with_nested(self):
        character = DNDCharacter(group_id="group1", user_id="user1", name="TestChar")
        character.hp_info.hp_cur = 10
        character.hp_info.hp_max = 20
        character.hp_info.is_init = True

        json_str = character.model_dump_json()
        assert "TestChar" in json_str
        assert "10" in json_str

        restored = DNDCharacter.model_validate_json(json_str)
        assert restored.name == "TestChar"
        assert restored.hp_info.hp_cur == 10


class TestHPInfoModel:
    def test_create(self):
        hp = HPInfo()
        assert hp.hp_cur == 0
        assert hp.hp_max == 0

    def test_take_damage(self):
        hp = HPInfo(hp_cur=10, hp_max=10, is_init=True, is_alive=True)
        hp.take_damage(3)
        assert hp.hp_cur == 7

    def test_take_damage_kills(self):
        hp = HPInfo(hp_cur=5, hp_max=10, is_init=True, is_alive=True)
        hp.take_damage(10)
        assert hp.hp_cur == 0
        assert hp.is_alive is False

    def test_heal(self):
        hp = HPInfo(hp_cur=5, hp_max=10, is_init=True, is_alive=True)
        hp.heal(3)
        assert hp.hp_cur == 8


class TestAbilityInfoModel:
    def test_create(self):
        ability = AbilityInfo()
        assert len(ability.ability) == 6
        assert ability.level == 0

    def test_default_arrays(self):
        ability = AbilityInfo()
        assert all(v == 0 for v in ability.ability)
        assert len(ability.check_prof) > 0
        assert len(ability.check_ext) > 0

