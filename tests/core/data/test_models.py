import pytest
from datetime import datetime

from plugins.DicePP.core.data.models import (
    UserKarma,
    InitEntity,
    InitList,
    LogSession,
    LogRecord,
    DNDCharacter,
    COCCharacter,
    HPInfo,
    AbilityInfo,
)
from plugins.DicePP.core.data.models.extended import (
    UserNickname,
    GroupConfig,
    GroupActivate,
    GroupWelcome,
    ChatRecord,
    BotControl,
    UserStat,
    GroupStat,
    MetaStat,
    NPCHealth,
)


class TestUserKarmaModel:
    def test_create(self):
        karma = UserKarma(user_id="user1", group_id="group1", value=50)
        assert karma.user_id == "user1"
        assert karma.group_id == "group1"
        assert karma.value == 50

    def test_serialization(self):
        karma = UserKarma(user_id="user1", group_id="group1", value=50)
        json_str = karma.model_dump_json()
        assert "user1" in json_str
        assert "group1" in json_str
        assert "50" in json_str

    def test_deserialization(self):
        json_str = '{"user_id": "user1", "group_id": "group1", "value": 50, "last_update": "2024-01-01T00:00:00"}'
        karma = UserKarma.model_validate_json(json_str)
        assert karma.user_id == "user1"
        assert karma.value == 50


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
        from plugins.DicePP.core.data.models import InitiativeError
        init_list = InitList()
        with pytest.raises(InitiativeError):
            init_list.del_entity("NotExists")


class TestLogModel:
    def test_log_session_create(self):
        session = LogSession(
            id="session1",
            group_id="group1",
            name="Test Session",
            recording=True,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        assert session.id == "session1"
        assert session.recording is True

    def test_log_record_create(self):
        record = LogRecord(
            log_id="session1",
            time=datetime.now(),
            user_id="user1",
            nickname="Test User",
            content="Hello",
            source="user",
        )
        assert record.log_id == "session1"
        assert record.content == "Hello"


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


class TestCOCCharacterModel:
    def test_create(self):
        character = COCCharacter(group_id="group1", user_id="user1", name="COCChar")
        assert character.name == "COCChar"


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


class TestExtendedModels:
    def test_user_nickname(self):
        nick = UserNickname(user_id="user1", group_id="group1", nickname="TestNick")
        assert nick.user_id == "user1"
        assert nick.nickname == "TestNick"

    def test_user_nickname_serialization(self):
        nick = UserNickname(user_id="user1", group_id="group1", nickname="TestNick")
        json_str = nick.model_dump_json()
        assert "TestNick" in json_str

    def test_group_config(self):
        cfg = GroupConfig(group_id="group1", data={"key": "value"})
        assert cfg.data["key"] == "value"

    def test_group_config_default(self):
        cfg = GroupConfig(group_id="group1")
        assert cfg.data == {}

    def test_group_activate(self):
        activate = GroupActivate(group_id="group1", active=True)
        assert activate.active is True

    def test_group_welcome(self):
        welcome = GroupWelcome(group_id="group1", welcome_msg="Hello!", welcome_enabled=True)
        assert welcome.welcome_enabled is True
        assert welcome.welcome_msg == "Hello!"

    def test_chat_record(self):
        record = ChatRecord(
            group_id="group1",
            user_id="user1",
            nickname="TestUser",
            content="Hello world",
            source="user"
        )
        assert record.content == "Hello world"

    def test_bot_control(self):
        ctrl = BotControl(key="rebooter", value="admin")
        assert ctrl.key == "rebooter"
        assert ctrl.value == "admin"

    def test_user_stat(self):
        stat = UserStat(user_id="user1", data='{"msg_count": 10}')
        assert stat.data == '{"msg_count": 10}'

    def test_group_stat(self):
        stat = GroupStat(group_id="group1", data='{"msg_count": 100}')
        assert stat.data == '{"msg_count": 100}'

    def test_meta_stat(self):
        stat = MetaStat(key="meta", data='{"total": 1000}')
        assert stat.data == '{"total": 1000}'

    def test_npc_health(self):
        npc = NPCHealth(group_id="group1", name="GoblinKing", hp_data='{"current": 50, "max": 100}')
        assert npc.name == "GoblinKing"
        assert npc.hp_data == '{"current": 50, "max": 100}'
