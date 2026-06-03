"""R9: suggest_action + SleepGate + inject_spontaneous_event 专项测试"""
import asyncio
from datetime import datetime
import pytest
from unittest.mock import AsyncMock, MagicMock

from plugins.DicePP.module.persona.life.character_life import (
    CharacterLife,
    CharacterLifeConfig,
)
from plugins.DicePP.module.persona.character.models import Character, PersonaExtensions
from plugins.DicePP.module.persona.data.models import CharacterState


def _make_character(sleep_messages=None):
    ext = PersonaExtensions(sleep_messages=sleep_messages)
    return Character(name="TestChar", description="A test character", extensions=ext)


def _make_cfg(**kw):
    return CharacterLifeConfig(
        enabled=True,
        slot_match_window_minutes=15,
        timezone="Asia/Shanghai",
        min_event_interval_minutes=5,
        chain_max_depth=1,
        chain_force_extend_once_prob=0.0,
        default_energy=50, default_mood=50, default_health=50,
        recovery_energy=20, recovery_mood=10, recovery_health=5,
        **kw,
    )


def _dt(hour, minute=0):
    return datetime(2026, 5, 15, hour, minute, 0)


async def _wait_until(predicate, timeout=1.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while not predicate():
        if asyncio.get_event_loop().time() > deadline:
            break
        await asyncio.sleep(0.01)
    assert predicate()


class TestSleepGate:
    """is_awake() 各场景测试"""

    def _make_life(self, start=None, end=None, good_night_fired=False, wake_up_fired=False):
        char = _make_character()
        cfg = _make_cfg()
        event_agent = MagicMock()
        store = AsyncMock()
        life = CharacterLife(config=cfg, event_agent=event_agent, data_store=store, character=char)
        life._today_jittered_start = start
        life._today_jittered_end = end
        if good_night_fired or wake_up_fired:
            s = start if start is not None else 480
            e = end if end is not None else 1320
            life._slot_minutes_today = [(s, "wake_up"), (e, "good_night")]
            if wake_up_fired:
                life._fired_slot_indices.add(0)
            if good_night_fired:
                life._fired_slot_indices.add(1)
        return life

    @pytest.mark.asyncio
    async def test_uninitialized_returns_true(self):
        life = self._make_life(start=None, end=None)
        assert await life.is_awake() is True

    @pytest.mark.asyncio
    async def test_inside_window_not_fired(self):
        life = self._make_life(start=480, end=1320)
        life.config.now = lambda: _dt(12, 0)
        assert await life.is_awake() is True

    @pytest.mark.asyncio
    async def test_before_window_returns_false(self):
        life = self._make_life(start=480, end=1320)
        life.config.now = lambda: _dt(6, 0)
        assert await life.is_awake() is False

    @pytest.mark.asyncio
    async def test_after_window_returns_false(self):
        life = self._make_life(start=480, end=1320)
        life.config.now = lambda: _dt(23, 0)
        assert await life.is_awake() is False

    @pytest.mark.asyncio
    async def test_good_night_fired_while_in_window_returns_false(self):
        life = self._make_life(start=480, end=1320, good_night_fired=True)
        life.config.now = lambda: _dt(22, 0)
        assert await life.is_awake() is False

    @pytest.mark.asyncio
    async def test_cross_midnight_window(self):
        life = self._make_life(start=1320, end=120)
        life.config.now = lambda: _dt(23, 30)
        assert await life.is_awake() is True

    @pytest.mark.asyncio
    async def test_cross_midnight_before_dawn_in_window(self):
        life = self._make_life(start=1320, end=120)
        life.config.now = lambda: _dt(1, 0)
        assert await life.is_awake() is True

    @pytest.mark.asyncio
    async def test_cross_midnight_outside_window(self):
        life = self._make_life(start=1320, end=120)
        life.config.now = lambda: _dt(8, 0)
        assert await life.is_awake() is False

    @pytest.mark.asyncio
    async def test_cross_midnight_wake_up_resets_good_night(self):
        life = self._make_life(start=1320, end=120, good_night_fired=True, wake_up_fired=True)
        life.config.now = lambda: _dt(1, 0)
        assert await life.is_awake() is True


class TestSuggestActionRelationshipGate:
    """suggest_action executor 亲密度门控测试"""

    @pytest.mark.asyncio
    async def test_below_threshold_skips(self):
        from plugins.DicePP.module.persona.tools.suggest_action import make_suggest_action_executor

        store = AsyncMock()
        rel = MagicMock()
        rel.composite_score = 25.0
        store.get_relationship = AsyncMock(return_value=rel)
        action_evaluator = AsyncMock()
        character_life = MagicMock()

        executor = make_suggest_action_executor(
            store=store, action_evaluator=action_evaluator,
            character_life=character_life, min_relationship=40,
            life_lock=asyncio.Lock(),
        )
        ctx = MagicMock(user_id="u1")
        result = await executor({"action_idea": "出去散步"}, ctx)
        assert result == "action noted"
        action_evaluator.evaluate.assert_not_called()

    @pytest.mark.asyncio
    async def test_above_threshold_proceeds(self):
        from plugins.DicePP.module.persona.tools.suggest_action import make_suggest_action_executor

        store = AsyncMock()
        rel = MagicMock()
        rel.composite_score = 55.0
        store.get_relationship = AsyncMock(return_value=rel)
        action_evaluator = AsyncMock()
        character_life = MagicMock()
        character_life.get_ongoing_activities.return_value = []

        executor = make_suggest_action_executor(
            store=store, action_evaluator=action_evaluator,
            character_life=character_life, min_relationship=40,
            life_lock=asyncio.Lock(),
        )
        ctx = MagicMock(user_id="u1")
        result = await executor({"action_idea": "出去散步"}, ctx)
        assert result == "action noted"
        await _wait_until(lambda: len(action_evaluator.evaluate.await_args_list) == 1)
        action_evaluator.evaluate.assert_awaited_once_with(
            "出去散步",
            [],
            user_id="u1",
        )

    @pytest.mark.asyncio
    async def test_missing_relationship_skips(self):
        from plugins.DicePP.module.persona.tools.suggest_action import make_suggest_action_executor

        store = AsyncMock()
        store.get_relationship = AsyncMock(return_value=None)
        action_evaluator = AsyncMock()
        character_life = MagicMock()

        executor = make_suggest_action_executor(
            store=store, action_evaluator=action_evaluator,
            character_life=character_life, min_relationship=40,
            life_lock=asyncio.Lock(),
        )
        ctx = MagicMock(user_id="u1")
        result = await executor({"action_idea": "测试"}, ctx)
        assert result == "action noted"
        action_evaluator.evaluate.assert_not_called()

    @pytest.mark.asyncio
    async def test_lock_serializes_injection(self):
        from plugins.DicePP.module.persona.tools.suggest_action import make_suggest_action_executor

        life_lock = asyncio.Lock()
        store = AsyncMock()
        rel = MagicMock()
        rel.composite_score = 55.0
        store.get_relationship = AsyncMock(return_value=rel)

        call_order = []
        first_entered = asyncio.Event()
        release_first = asyncio.Event()

        async def slow_evaluate(*args, **kw):
            call_order.append(args[0])
            if args[0] == "task1":
                first_entered.set()
                await release_first.wait()
            return ("approved", "ok")

        action_evaluator = MagicMock()
        action_evaluator.evaluate = slow_evaluate
        character_life = MagicMock()
        character_life.get_ongoing_activities.return_value = []
        character_life._inject_spontaneous_event_impl = AsyncMock(return_value=True)

        executor = make_suggest_action_executor(
            store=store, action_evaluator=action_evaluator,
            character_life=character_life, min_relationship=40,
            life_lock=life_lock,
        )
        ctx = MagicMock(user_id="u1")

        await executor({"action_idea": "task1"}, ctx)
        await asyncio.wait_for(first_entered.wait(), timeout=1.0)
        await executor({"action_idea": "task2"}, ctx)

        await asyncio.sleep(0)
        assert call_order == ["task1"]
        release_first.set()
        await _wait_until(
            lambda: len(character_life._inject_spontaneous_event_impl.await_args_list) == 2
        )
        assert call_order == ["task1", "task2"]
        injected_actions = [
            call.args[0]
            for call in character_life._inject_spontaneous_event_impl.await_args_list
        ]
        assert injected_actions == ["task1", "task2"]


class TestInjectSpontaneousEvent:
    """inject_spontaneous_event 基础流程测试"""

    def test_state_lock_exists(self):
        char = _make_character()
        cfg = _make_cfg()
        event_agent = MagicMock()
        store = AsyncMock()
        life = CharacterLife(config=cfg, event_agent=event_agent, data_store=store, character=char)
        assert hasattr(life, '_state_lock')
        assert isinstance(life._state_lock, asyncio.Lock)

    @pytest.mark.asyncio
    async def test_inject_returns_false_on_missing_state(self):
        char = _make_character()
        cfg = _make_cfg()
        event_agent = MagicMock()
        store = AsyncMock()
        store.get_character_state = AsyncMock(return_value=None)
        life = CharacterLife(config=cfg, event_agent=event_agent, data_store=store, character=char)
        result = await life.inject_spontaneous_event("测试行动")
        assert result is False

    @pytest.mark.asyncio
    async def test_lock_serializes_concurrent_calls(self):
        char = _make_character()
        cfg = _make_cfg()
        event_agent = MagicMock()
        store = AsyncMock()
        cs = CharacterState(energy=50, mood=50, health=50)
        store.get_character_state = AsyncMock(return_value=cs)
        life = CharacterLife(config=cfg, event_agent=event_agent, data_store=store, character=char)

        call_order = []

        async def fake_impl(action_description):
            call_order.append(action_description)
            return True

        life._inject_spontaneous_event_impl = fake_impl

        results = await asyncio.gather(
            life.inject_spontaneous_event("first"),
            life.inject_spontaneous_event("second"),
            life.inject_spontaneous_event("third"),
        )
        assert results == [True, True, True]
        assert call_order == ["first", "second", "third"]


class TestSerializeRawParts:
    """_serialize_raw_parts 静态方法测试"""

    def test_both_valid_json(self):
        result = CharacterLife._serialize_raw_parts(
            '{"desc":"test event"}', '{"reaction":"test reaction"}')
        assert '"event"' in result
        assert '"reaction"' in result

    def test_both_empty_returns_empty_string(self):
        assert CharacterLife._serialize_raw_parts("", "") == ""

    def test_one_json_one_plain(self):
        result = CharacterLife._serialize_raw_parts('{"desc":"ok"}', "plain text reaction")
        assert '"event"' in result
        assert "plain text reaction" in result

    def test_invalid_json_preserved_as_is(self):
        result = CharacterLife._serialize_raw_parts("not json", "")
        assert "not json" in result
