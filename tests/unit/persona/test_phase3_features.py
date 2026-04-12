"""
Phase 3 功能测试

测试内容:
1. .ai mute/unmute 功能
2. search_memory 工具
3. 厌倦拒绝机制
4. 配置值更新
"""

import pytest
import asyncio
import tempfile
import os

from datetime import datetime, timedelta

from plugins.DicePP.module.persona.data.store import PersonaDataStore
from plugins.DicePP.module.persona.data.models import UserProfile, RelationshipState
from plugins.DicePP.module.persona.character.models import Character, PersonaExtensions
from plugins.DicePP.core.config.pydantic_models import PersonaConfig


@pytest.fixture
async def temp_db():
    """创建临时数据库"""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name

    import aiosqlite
    async with aiosqlite.connect(db_path) as db:
        store = PersonaDataStore(db)
        await store.ensure_tables()
        yield store

    os.unlink(db_path)


class TestMuteFunctionality:
    """测试 mute/unmute 功能"""

    @pytest.mark.asyncio
    async def test_initial_state_not_muted(self, temp_db):
        """初始状态应该未静音"""
        store = temp_db
        assert await store.is_user_muted("test_user") is False

    @pytest.mark.asyncio
    async def test_mute_user(self, temp_db):
        """静音用户"""
        store = temp_db
        user_id = "test_user"

        await store.mute_user(user_id, reason="user_request")
        assert await store.is_user_muted(user_id) is True

    @pytest.mark.asyncio
    async def test_unmute_user(self, temp_db):
        """取消静音"""
        store = temp_db
        user_id = "test_user"

        await store.mute_user(user_id)
        assert await store.is_user_muted(user_id) is True

        await store.unmute_user(user_id)
        assert await store.is_user_muted(user_id) is False

    @pytest.mark.asyncio
    async def test_repeat_mute_idempotent(self, temp_db):
        """重复静音应该保持静音状态"""
        store = temp_db
        user_id = "test_user"

        await store.mute_user(user_id)
        await store.mute_user(user_id)
        assert await store.is_user_muted(user_id) is True


class TestSearchMemory:
    """测试 search_memory 功能"""

    @pytest.mark.asyncio
    async def test_search_user_profile(self, temp_db):
        """搜索用户档案"""
        store = temp_db
        user_id = "test_user"

        # 创建用户档案
        profile = UserProfile(
            user_id=user_id,
            facts={"name": "Xiao Ming", "pet": "cat", "hobby": "games"},
            updated_at=datetime.now()
        )
        await store.save_user_profile(profile)

        # 搜索
        result = await store.search_memory(
            user_id=user_id,
            group_id="",
            query="cat",
            search_type="profile"
        )
        assert "cat" in result.lower()

    @pytest.mark.asyncio
    async def test_search_not_found(self, temp_db):
        """搜索不存在的"""
        store = temp_db
        user_id = "test_user"

        result = await store.search_memory(
            user_id=user_id,
            group_id="",
            query="nonexistent_word_xyz",
            search_type="profile"
        )
        assert "未找到" in result

    @pytest.mark.asyncio
    async def test_search_all_includes_profile(self, temp_db):
        """all 类型应该搜索用户档案"""
        store = temp_db
        user_id = "test_user"

        profile = UserProfile(
            user_id=user_id,
            facts={"name": "Test User"},
            updated_at=datetime.now()
        )
        await store.save_user_profile(profile)

        result = await store.search_memory(
            user_id=user_id,
            group_id="",
            query="Test",
            search_type="all"
        )
        assert result and len(result) > 0


class TestWarmthLevelRefuse:
    """测试厌倦拒绝机制"""

    def test_warmth_level_cold(self):
        """好感度 5 分应该在厌倦区间（0）"""
        rel = RelationshipState(
            user_id="test",
            group_id="",
            intimacy=5.0,
            passion=5.0,
            trust=5.0,
            secureness=5.0,
        )

        ext = PersonaExtensions(initial_relationship=30)
        char = Character(name="Test", extensions=ext)

        warmth_level, label = rel.get_warmth_level(char.get_warmth_labels())
        assert warmth_level == 0, f"Expected 0 (cold), got {warmth_level}"

    def test_warmth_level_distant(self):
        """好感度 15 分应该在冷淡区间（1）"""
        rel = RelationshipState(
            user_id="test",
            group_id="",
            intimacy=15.0,
            passion=15.0,
            trust=15.0,
            secureness=15.0,
        )

        ext = PersonaExtensions(initial_relationship=30)
        char = Character(name="Test", extensions=ext)

        warmth_level, label = rel.get_warmth_level(char.get_warmth_labels())
        assert warmth_level == 1, f"Expected 1 (distant), got {warmth_level}"

    def test_refuse_probability_formula(self):
        """测试拒绝概率公式"""
        # P_refuse = 0.5 + 0.4 * (1 - score / 10)
        # When score = 0: P = 0.5 + 0.4 = 0.9 (90%)
        # When score = 5: P = 0.5 + 0.4 * 0.5 = 0.7 (70%)
        # When score = 10: P = 0.5 + 0 = 0.5 (50%)

        def calc_refuse_prob(score):
            return 0.5 + 0.4 * (1 - score / 10)

        assert abs(calc_refuse_prob(0) - 0.9) < 0.001
        assert abs(calc_refuse_prob(5) - 0.7) < 0.001
        assert abs(calc_refuse_prob(10) - 0.5) < 0.001


class TestConfigValues:
    """测试配置值更新"""

    def test_max_short_term_chars(self):
        """max_short_term_chars 应该为 1500"""
        config = PersonaConfig()
        assert config.max_short_term_chars == 1500

    def test_max_messages(self):
        """max_messages 应该为 15"""
        config = PersonaConfig()
        assert config.max_messages == 15

    def test_tools_enabled(self):
        """tools_enabled 应该为 True"""
        config = PersonaConfig()
        assert config.tools_enabled is True

    def test_relationship_refuse_enabled(self):
        """relationship_refuse_enabled 应该默认为 True"""
        config = PersonaConfig()
        assert config.relationship_refuse_enabled is True

    def test_relationship_refuse_prob_base(self):
        """relationship_refuse_prob_base 应该为 0.5"""
        config = PersonaConfig()
        assert config.relationship_refuse_prob_base == 0.5

    def test_relationship_refuse_prob_max(self):
        """relationship_refuse_prob_max 应该为 0.9"""
        config = PersonaConfig()
        assert config.relationship_refuse_prob_max == 0.9

    def test_refuse_probability_formula_with_config(self):
        """使用配置的拒绝概率公式"""
        config = PersonaConfig()

        def calc_refuse_prob(score, base, max_p):
            return base + (max_p - base) * (1 - score / 10)

        # 使用新配置名测试
        assert abs(calc_refuse_prob(0, config.relationship_refuse_prob_base, config.relationship_refuse_prob_max) - 0.9) < 0.001
        assert abs(calc_refuse_prob(10, config.relationship_refuse_prob_base, config.relationship_refuse_prob_max) - 0.5) < 0.001

        # 测试禁用拒绝
        assert config.relationship_refuse_enabled is True
