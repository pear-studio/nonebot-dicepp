"""
单元测试: CharacterStateChangeSource — 状态变更通知
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from plugins.DicePP.module.persona.data.models import CharacterState
from plugins.DicePP.module.persona.life.change_sources import CharacterStateChangeSource


class TestCharacterStateChangeSource:
    """测试 CharacterStateChangeSource.update() 核心分支"""

    def _make_store(self, energy=None, mood=None, health=None):
        store = MagicMock()
        store.get_character_state = AsyncMock(
            return_value=CharacterState(energy=energy, mood=mood, health=health)
        )
        return store

    # ── 初始化 (cursor=None) ──────────────────────────────────

    @pytest.mark.asyncio
    async def test_init_notification_all_dimensions(self):
        """cursor=None + 三维有值 → 返回初始化通知"""
        store = self._make_store(energy=80, mood=60, health=90)
        source = CharacterStateChangeSource(store)
        notifications, cursor = await source.update(None)
        assert len(notifications) == 3
        assert cursor == {"energy": 80, "mood": 60, "health": 90}
        contents = [n.content for n in notifications]
        assert any("体力" in c and "80" in c for c in contents)
        assert any("心情" in c and "60" in c for c in contents)
        assert any("健康" in c and "90" in c for c in contents)

    @pytest.mark.asyncio
    async def test_init_notification_skips_none_values(self):
        """cursor=None + 部分维 None → 跳过 None 维度"""
        store = self._make_store(energy=80, mood=None, health=90)
        source = CharacterStateChangeSource(store)
        notifications, cursor = await source.update(None)
        assert len(notifications) == 2
        contents = [n.content for n in notifications]
        assert not any("心情" in c for c in contents)

    @pytest.mark.asyncio
    async def test_init_all_none(self):
        """cursor=None + 全维 None → 空通知"""
        store = self._make_store(energy=None, mood=None, health=None)
        source = CharacterStateChangeSource(store)
        notifications, cursor = await source.update(None)
        assert notifications == []
        assert cursor == {"energy": None, "mood": None, "health": None}

    # ── 无变化 ───────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_no_change_returns_empty(self):
        """cursor == current → 无通知"""
        store = self._make_store(energy=80, mood=60, health=90)
        source = CharacterStateChangeSource(store)
        cursor = {"energy": 80, "mood": 60, "health": 90}
        notifications, new_cursor = await source.update(cursor)
        assert notifications == []
        assert new_cursor == {"energy": 80, "mood": 60, "health": 90}

    # ── 正值变化 (delta > 0) ─────────────────────────────────

    @pytest.mark.asyncio
    async def test_positive_delta_format(self):
        """current > cursor → content 含 +N"""
        store = self._make_store(energy=85, mood=60, health=90)
        source = CharacterStateChangeSource(store)
        cursor = {"energy": 80, "mood": 60, "health": 90}
        notifications, new_cursor = await source.update(cursor)
        assert len(notifications) == 1
        assert "体力" in notifications[0].content
        assert "+5" in notifications[0].content
        assert "85" in notifications[0].content

    # ── 负值变化 (delta < 0) ─────────────────────────────────

    @pytest.mark.asyncio
    async def test_negative_delta_format(self):
        """current < cursor → content 含 -N"""
        store = self._make_store(energy=65, mood=60, health=90)
        source = CharacterStateChangeSource(store)
        cursor = {"energy": 80, "mood": 60, "health": 90}
        notifications, new_cursor = await source.update(cursor)
        assert len(notifications) == 1
        assert "体力" in notifications[0].content
        assert "-15" in notifications[0].content
        assert "65" in notifications[0].content

    # ── 多维度同时变化 ───────────────────────────────────────

    @pytest.mark.asyncio
    async def test_multi_dimension_change(self):
        """多个维度同时变化 → 每条维度一条通知"""
        store = self._make_store(energy=70, mood=50, health=90)
        source = CharacterStateChangeSource(store)
        cursor = {"energy": 80, "mood": 60, "health": 90}
        notifications, new_cursor = await source.update(cursor)
        assert len(notifications) == 2  # energy -10, mood -10, health unchanged
        dims = {n.content for n in notifications}
        assert any("体力" in c for c in dims)
        assert any("心情" in c for c in dims)
        assert not any("健康" in c for c in dims)

    # ── 属性 ─────────────────────────────────────────────────

    def test_source_attributes(self):
        """验证 ChangeSource 协议属性"""
        source = CharacterStateChangeSource(MagicMock())
        assert source.source_id == "state.character"
        assert source.priority == 10
        assert source.name == "状态变化"
