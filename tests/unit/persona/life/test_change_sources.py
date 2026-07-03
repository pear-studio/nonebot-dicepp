"""
单元测试: ChangeSource 实现（chat 路径）
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import timedelta

from utils.time import wall_now

from plugins.DicePP.module.persona.life.change_sources import (
    DateChangeSource,
    RelationChangeSource,
    ProfileFactsChangeSource,
    DailyEventChangeSource,
)
from plugins.DicePP.module.persona.data.models import UserProfile


class TestDateChangeSource:

    @pytest.mark.asyncio
    async def test_first_call_returns_notification(self):
        source = DateChangeSource(timezone="Asia/Shanghai")
        notifs, cursor = await source.update(None)
        assert len(notifs) == 1
        assert "现在是" in notifs[0].content
        assert isinstance(cursor, str)

    @pytest.mark.asyncio
    async def test_same_date_no_notification(self):
        source = DateChangeSource(timezone="Asia/Shanghai")
        _, cursor1 = await source.update(None)
        notifs, cursor2 = await source.update(cursor1)
        assert len(notifs) == 0
        assert cursor2 == cursor1


class TestRelationChangeSource:

    @pytest.mark.asyncio
    async def test_first_call_no_notification_sets_baseline(self):
        store = MagicMock()
        store.get_relationship = AsyncMock(return_value=None)
        with patch(
            'plugins.DicePP.module.persona.data.models.RelationshipState.get_relation_level',
            return_value=(2, "朋友"),
        ):
            source = RelationChangeSource(
                store=store, user_id="u1",
                relation_labels=["陌生人", "熟人", "朋友", "挚友"],
            )
            notifs, cursor = await source.update(None)
        assert len(notifs) == 0
        assert cursor == "朋友"

    @pytest.mark.asyncio
    async def test_label_changed_returns_notification(self):
        store = MagicMock()
        store.get_relationship = AsyncMock(return_value=None)
        with patch(
            'plugins.DicePP.module.persona.data.models.RelationshipState.get_relation_level',
            return_value=(3, "挚友"),
        ):
            source = RelationChangeSource(
                store=store, user_id="u1",
                relation_labels=["陌生人", "熟人", "朋友", "挚友"],
            )
            notifs, _ = await source.update("朋友")
        assert len(notifs) == 1
        assert "现在是挚友" in notifs[0].content

    @pytest.mark.asyncio
    async def test_same_label_no_notification(self):
        store = MagicMock()
        store.get_relationship = AsyncMock(return_value=None)
        with patch(
            'plugins.DicePP.module.persona.data.models.RelationshipState.get_relation_level',
            return_value=(2, "朋友"),
        ):
            source = RelationChangeSource(
                store=store, user_id="u1",
                relation_labels=["陌生人", "熟人", "朋友", "挚友"],
            )
            notifs, cursor = await source.update("朋友")
        assert len(notifs) == 0
        assert cursor == "朋友"


class TestProfileFactsChangeSource:

    @pytest.mark.asyncio
    async def test_first_call_sets_baseline(self):
        profile = UserProfile(user_id="u1", facts={"爱好": "种花"})
        store = MagicMock()
        store.get_user_profile = AsyncMock(return_value=profile)
        source = ProfileFactsChangeSource(store=store, user_id="u1")
        notifs, cursor = await source.update(None)
        assert len(notifs) == 0
        assert len(cursor) == 32

    @pytest.mark.asyncio
    async def test_facts_changed_returns_notification(self):
        store = MagicMock()
        profile1 = UserProfile(user_id="u1", facts={"爱好": "种花"})
        store.get_user_profile = AsyncMock(return_value=profile1)
        source = ProfileFactsChangeSource(store=store, user_id="u1")
        _, cursor1 = await source.update(None)
        profile2 = UserProfile(user_id="u1", facts={"爱好": "养猫", "年龄": "25"})
        store.get_user_profile = AsyncMock(return_value=profile2)
        notifs, _ = await source.update(cursor1)
        assert len(notifs) == 1
        assert "新的了解" in notifs[0].content

    @pytest.mark.asyncio
    async def test_no_profile_no_break(self):
        store = MagicMock()
        store.get_user_profile = AsyncMock(return_value=None)
        source = ProfileFactsChangeSource(store=store, user_id="u1")
        notifs, cursor = await source.update(None)
        assert len(notifs) == 0


class TestDailyEventChangeSource:

    @pytest.mark.asyncio
    async def test_first_call_marks_seen_no_injection(self):
        from plugins.DicePP.module.persona.data.models import DailyEvent
        store = MagicMock()
        naive_now = wall_now()
        events = [
            DailyEvent(id=1, date="2026-07-02", event_type="system",
                       description="事件1", created_at=naive_now),
        ]
        store.get_daily_events = AsyncMock(return_value=events)
        source = DailyEventChangeSource(store=store)
        notifs, cursor = await source.update(None)
        assert len(notifs) == 0
        assert 1 in cursor["event_ids"]

    @pytest.mark.asyncio
    async def test_new_event_injected(self):
        from plugins.DicePP.module.persona.data.models import DailyEvent
        store = MagicMock()
        old = wall_now() - timedelta(minutes=10)
        new = wall_now()
        cursor = {
            "date": new.strftime("%Y-%m-%d"),
            "event_ids": [1],
            "context_since": old.isoformat(),
        }
        events = [
            DailyEvent(id=1, date=new.strftime("%Y-%m-%d"),
                       event_type="system", description="事件1",
                       created_at=old),
            DailyEvent(id=2, date=new.strftime("%Y-%m-%d"),
                       event_type="system", description="新事件",
                       created_at=new, context_summary="新事件摘要"),
        ]
        store.get_daily_events = AsyncMock(return_value=events)
        source = DailyEventChangeSource(store=store)
        notifs, _ = await source.update(cursor)
        assert len(notifs) == 1
        assert "新事件" in notifs[0].content


# ── CharacterStateChangeSource 回归测试 ──────────────────


class TestCharacterStateChangeSource:

    @pytest.mark.asyncio
    async def test_init_notification_all_dimensions(self):
        """cursor=None 三维有值 → 3 条通知"""
        from plugins.DicePP.module.persona.life.change_sources import CharacterStateChangeSource
        from plugins.DicePP.module.persona.data.models import CharacterState

        state = CharacterState(energy=80, mood=60, health=90)
        store = MagicMock()
        store.get_character_state = AsyncMock(return_value=state)
        source = CharacterStateChangeSource(store=store)

        notifs, cursor = await source.update(None)
        assert len(notifs) == 3
        assert cursor == {"energy": 80, "mood": 60, "health": 90}

    @pytest.mark.asyncio
    async def test_no_change_returns_empty(self):
        """cursor==current → 空通知"""
        from plugins.DicePP.module.persona.life.change_sources import CharacterStateChangeSource
        from plugins.DicePP.module.persona.data.models import CharacterState

        state = CharacterState(energy=80, mood=60, health=90)
        store = MagicMock()
        store.get_character_state = AsyncMock(return_value=state)
        source = CharacterStateChangeSource(store=store)

        notifs, _ = await source.update({"energy": 80, "mood": 60, "health": 90})
        assert len(notifs) == 0

    @pytest.mark.asyncio
    async def test_positive_delta_format(self):
        """delta>0 → "+N" 格式"""
        from plugins.DicePP.module.persona.life.change_sources import CharacterStateChangeSource
        from plugins.DicePP.module.persona.data.models import CharacterState

        state = CharacterState(energy=85, mood=60, health=90)
        store = MagicMock()
        store.get_character_state = AsyncMock(return_value=state)
        source = CharacterStateChangeSource(store=store)

        notifs, _ = await source.update({"energy": 80, "mood": 60, "health": 90})
        assert len(notifs) == 1
        assert "体力 +5" in notifs[0].content

    @pytest.mark.asyncio
    async def test_negative_delta_format(self):
        """delta<0 → "-N" 格式"""
        from plugins.DicePP.module.persona.life.change_sources import CharacterStateChangeSource
        from plugins.DicePP.module.persona.data.models import CharacterState

        state = CharacterState(energy=75, mood=60, health=90)
        store = MagicMock()
        store.get_character_state = AsyncMock(return_value=state)
        source = CharacterStateChangeSource(store=store)

        notifs, _ = await source.update({"energy": 80, "mood": 60, "health": 90})
        assert len(notifs) == 1
        assert "体力 -5" in notifs[0].content

    @pytest.mark.asyncio
    async def test_none_value_skipped(self):
        """None 维度不发通知"""
        from plugins.DicePP.module.persona.life.change_sources import CharacterStateChangeSource
        from plugins.DicePP.module.persona.data.models import CharacterState

        state = CharacterState(energy=None, mood=60, health=None)
        store = MagicMock()
        store.get_character_state = AsyncMock(return_value=state)
        source = CharacterStateChangeSource(store=store)

        notifs, cursor = await source.update(None)
        assert len(notifs) == 1  # only mood
        assert "心情" in notifs[0].content
        assert cursor == {"energy": None, "mood": 60, "health": None}
