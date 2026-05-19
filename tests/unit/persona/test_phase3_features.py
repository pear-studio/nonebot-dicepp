"""
Phase 3 功能测试

测试内容:
1. .ai mute/unmute 功能
2. search_memory 工具
3. 冷淡拒绝机制
4. 配置值更新
"""

import pytest
import tempfile
import os

from datetime import datetime

from plugins.DicePP.module.persona.data.store import PersonaDataStore
from plugins.DicePP.module.persona.data.models import UserProfile, RelationshipState
from plugins.DicePP.module.persona.character.models import Character, PersonaExtensions


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
    """测试冷淡拒绝机制"""

    def test_warmth_level_cold(self):
        """好感度 5 分应该在冷淡区间（0）"""
        rel = RelationshipState(
            user_id="test",
            intimacy=5.0,
            passion=5.0,
            trust=5.0,
            secureness=5.0,
        )

        ext = PersonaExtensions(initial_relationship=40)
        char = Character(name="Test", extensions=ext)

        warmth_level, label = rel.get_warmth_level(char.get_warmth_labels())
        assert warmth_level == 0, f"Expected 0 (cold), got {warmth_level}"

    def test_warmth_level_distant(self):
        """好感度 30 分应该在疏远区间（1）"""
        rel = RelationshipState(
            user_id="test",
            intimacy=30.0,
            passion=30.0,
            trust=30.0,
            secureness=30.0,
        )

        ext = PersonaExtensions(initial_relationship=40)
        char = Character(name="Test", extensions=ext)

        warmth_level, label = rel.get_warmth_level(char.get_warmth_labels())
        assert warmth_level == 1, f"Expected 1 (distant), got {warmth_level}"

