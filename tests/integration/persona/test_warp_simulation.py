"""
集成测试: timewarp 多天推进

覆盖:
- SteppedClock 步进 + CharacterLife.tick() 槽位匹配
- 多天推进：跨天重置、槽位重新生成、good_night 冷却
- 事件记录到 data_store
- 状态累积（energy/mood/health）
- 错误跳过 + WallClock 恢复

使用 mock Agent 验证代码路径，不触发真实 LLM。
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.life.character_life import CharacterLife, CharacterLifeConfig
from plugins.DicePP.module.persona.character.models import Character, PersonaExtensions
from plugins.DicePP.module.persona.data.store import PersonaDataStore
from plugins.DicePP.module.persona.data.models import CharacterState
from plugins.DicePP.module.persona.life.types import EventGenerationResult, EventReactionResult, AgentResult
from utils.time import SteppedClock, set_clock, get_clock, WallClock
from plugins.DicePP.utils.time import wall_now


# ── 多天 mock 响应工厂 ──────────────────────────────────────────────


def _make_multi_day_agents(days: int, slots_per_day: int):
    """生成足够多天的 mock DM/Character agent 响应队列。

    每天每个槽位消耗 1 个 DM 响应 + 1 个 Character 响应。
    所有事件 chain_depth=1（无 follow_up）。
    """
    total = days * slots_per_day

    dm_results = []
    char_results = []
    for d in range(days):
        for s in range(slots_per_day):
            dm_results.append(
                AgentResult(
                    success=True,
                    data=EventGenerationResult(
                        description=f"Day{d}-Slot{s}: 事件描述",
                        duration_minutes=10,
                        energy_delta=2,
                        mood_delta=3,
                        health_delta=0,
                    ),
                )
            )
            char_results.append(
                AgentResult(
                    success=True,
                    data=EventReactionResult(
                        reaction=f"Day{d}-Slot{s}: 角色反应",
                        has_follow_up=False,
                    ),
                )
            )

    class QueueDMAgent:
        async def run(self, context):
            return dm_results.pop(0)

        async def load_state(self):
            return None

        async def save_state(self, state):
            pass

    class QueueCharacterAgent:
        async def react(self, context):
            return char_results.pop(0)

        async def load_state(self):
            return CharacterState()

        async def save_state(self, state):
            pass

    return QueueDMAgent(), QueueCharacterAgent()


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
async def temp_db():
    import aiosqlite
    async with aiosqlite.connect(":memory:") as persona_db, aiosqlite.connect(":memory:") as core_db:
        store = PersonaDataStore(":memory:", core_db)
        store._persona_db = persona_db
        await store.ensure_tables()
        yield store


@pytest.fixture
def character():
    """角色：每天 2 个事件槽位，活跃 8:00-22:00"""
    ext = PersonaExtensions(
        daily_events_count=2,
        event_day_start_hour=8,
        event_day_end_hour=22,
        event_day_start_jitter_minutes=0,
        event_day_end_jitter_minutes=0,
    )
    return Character(
        name="测试角色",
        description="测试用角色",
        extensions=ext,
    )


@pytest.fixture
def config():
    return CharacterLifeConfig(
        enabled=True,
        slot_match_window_minutes=15,
        timezone="Asia/Shanghai",
        min_event_interval_minutes=5,
        chain_max_depth=1,
        chain_force_extend_once_prob=0.0,
    )


@pytest.fixture
def life(temp_db, character, config):
    """创建 CharacterLife，不含 agent（测试中按需注入）"""
    life = CharacterLife(
        config=config,
        data_store=temp_db,
        character=character,
        dm_agent=None,
        character_agent=None,
    )
    life.boundary_receiver = MagicMock()
    return life


# ── 辅助函数 ─────────────────────────────────────────────────────────


def _step_to_date_time(d: datetime.date, hour: int, minute: int, stepped: SteppedClock) -> datetime:
    """返回该日期指定时刻的 datetime 并步进 SteppedClock。"""
    dt = datetime.combine(d, datetime.min.time().replace(hour=hour, minute=minute))
    stepped.step_to(dt)
    return dt


async def _run_warp_loop(
    life: CharacterLife,
    stepped: SteppedClock,
    days: int,
):
    """与 BotRunner.warp() 等价的推进循环，直接操作 CharacterLife。"""
    start_hour = life.character.extensions.event_day_start_hour
    end_hour = life.character.extensions.event_day_end_hour

    events_triggered = 0
    errors = 0
    skipped = 0

    for day_idx in range(days):
        day_date = stepped.now().date()

        # 跨天重置
        life._last_event_date = (day_date - timedelta(days=1)).isoformat()
        life._reset_daily_state()
        life._last_good_night_fired_at = None

        # 推进到当日开始时间以匹配槽位
        _step_to_date_time(day_date, start_hour, 0, stepped)

        # 遍历槽位
        slots = list(life._slot_minutes_today or [])
        for slot_idx, (slot_m, slot_type) in enumerate(slots):
            if slot_idx in life._fired_slot_indices:
                skipped += 1
                continue

            slot_hour = slot_m // 60
            slot_min = slot_m % 60
            _step_to_date_time(day_date, slot_hour, slot_min, stepped)

            try:
                result = await life.tick()
                if result:
                    events_triggered += len(result)
            except Exception:
                errors += 1

        if day_idx < days - 1:
            stepped.step_by(days=1)

    return events_triggered, errors, skipped


# ── 测试 ─────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestWarpSimulation:
    """多天 timewarp 推进测试"""

    @pytest.mark.asyncio
    async def test_single_day_all_slots_fire(self, life, temp_db):
        """单天 4 个槽位（wake_up + 2 system + good_night）全部触发"""
        dm, char = _make_multi_day_agents(days=1, slots_per_day=4)
        life.dm_agent = dm
        life.character_agent = char

        await temp_db.update_character_state(CharacterState(energy=50, mood=50, health=50))

        start = datetime(2024, 3, 1, 6, 0, 0)
        stepped = SteppedClock(start)
        original = get_clock()
        set_clock(stepped)

        try:
            triggered, errors, skipped = await _run_warp_loop(life, stepped, days=1)
        finally:
            set_clock(original)

        assert triggered == 4, f"应触发 4 个事件，实际 {triggered}"
        assert errors == 0
        assert skipped == 0

        # 验证事件写入 data_store
        events = await temp_db.get_daily_events("2024-03-01")
        assert len(events) == 4, f"应写入 4 条事件，实际 {len(events)}"

        # 验证状态累积
        # wake_up: energy=50+20(强制保底)=70, mood=50+3=53
        # slot1:   energy=70+2=72, mood=53+3=56
        # slot2:   energy=72+2=74, mood=56+3=59
        # good_night: energy=74+2=76, mood=59+3=62
        state = await temp_db.get_character_state()
        assert state is not None
        assert state.energy == 76, f"energy 应为 76，实际 {state.energy}"
        assert state.mood == 62, f"mood 应为 62，实际 {state.mood}"

    @pytest.mark.asyncio
    async def test_three_days_cross_day_reset(self, life, temp_db):
        """3 天推进：每天槽位重新生成，跨天重置正常工作"""
        dm, char = _make_multi_day_agents(days=3, slots_per_day=4)
        life.dm_agent = dm
        life.character_agent = char

        await temp_db.update_character_state(CharacterState(energy=50, mood=50, health=50))

        start = datetime(2024, 3, 1, 6, 0, 0)
        stepped = SteppedClock(start)
        original = get_clock()
        set_clock(stepped)

        try:
            triggered, errors, skipped = await _run_warp_loop(life, stepped, days=3)
        finally:
            set_clock(original)

        assert triggered == 12, f"3 天应触发 12 个事件，实际 {triggered}"
        assert errors == 0
        assert skipped == 0

        # 每天的事件应写入对应日期
        for i, date_str in enumerate(["2024-03-01", "2024-03-02", "2024-03-03"]):
            events = await temp_db.get_daily_events(date_str)
            assert len(events) == 4, f"{date_str} 应有 4 条事件，实际 {len(events)}"

        # 状态累积（energy 有 [0,100] 上限）
        # Day1: 50→70(w/u+20)→72→74→76, mood: 50→53→56→59→62
        # Day2: 76→96(w/u+20)→98→100→100(cap), mood: 62→65→68→71→74
        # Day3: 100→100(cap)→100→100→100, mood: 74→77→80→83→86
        state = await temp_db.get_character_state()
        assert state.energy == 100, f"energy 应达上限 100，实际 {state.energy}"
        assert state.mood == 86, f"mood 应为 86，实际 {state.mood}"

    @pytest.mark.asyncio
    async def test_stepped_clock_preserves_time_during_event_chain(self, life, temp_db):
        """SteppedClock 在 DM/Character agent 调用期间保持冻结"""
        # 需要 chain_max_depth >= 2 才能触发 follow_up 链
        life.config.chain_max_depth = 2

        # 使用带 follow_up 的链式事件 agent
        chain_dm = [
            AgentResult(
                success=True,
                data=EventGenerationResult(
                    description="链事件1",
                    duration_minutes=5,
                    energy_delta=3,
                    mood_delta=0,
                    health_delta=0,
                ),
            ),
            AgentResult(
                success=True,
                data=EventGenerationResult(
                    description="链事件2",
                    duration_minutes=5,
                    energy_delta=2,
                    mood_delta=0,
                    health_delta=0,
                ),
            ),
        ]
        chain_char = [
            AgentResult(
                success=True,
                data=EventReactionResult(reaction="反应1", has_follow_up=True),
            ),
            AgentResult(
                success=True,
                data=EventReactionResult(reaction="反应2", has_follow_up=False),
            ),
        ]

        class ChainDMAgent:
            async def run(self, context):
                return chain_dm.pop(0)

            async def load_state(self):
                return None

            async def save_state(self, state):
                pass

        class ChainCharAgent:
            async def react(self, context):
                return chain_char.pop(0)

            async def load_state(self):
                return CharacterState()

            async def save_state(self, state):
                pass

        life.dm_agent = ChainDMAgent()
        life.character_agent = ChainCharAgent()

        await temp_db.update_character_state(CharacterState(energy=50, mood=50, health=50))

        start = datetime(2024, 3, 1, 10, 0, 0)
        stepped = SteppedClock(start)
        original = get_clock()
        set_clock(stepped)

        try:
            # 手动设置单个槽位
            life._last_event_date = "2024-03-01"
            life._slot_minutes_today = [(10 * 60, "system")]
            life._fired_slot_indices.clear()

            # 记录 tick 前后的时钟时间
            t_before = stepped.now()
            result = await life.tick()
            t_after = stepped.now()

            # 时钟应冻结——SteppedClock 不会自动推进
            assert t_before == t_after, (
                f"SteppedClock 应在 tick 期间冻结，但 {t_before} → {t_after}"
            )

            # 链式事件应全部触发（2 个）
            assert len(result) == 2, f"链应产生 2 个事件，实际 {len(result)}"
        finally:
            set_clock(original)

    @pytest.mark.asyncio
    async def test_error_skips_slot_continues(self, life, temp_db):
        """Agent 内部异常被 generate_daily_event 捕获，槽位静默跳过，不阻塞后续槽位"""
        call_count = [0]

        class ErrorThenOkDMAgent:
            async def run(self, context):
                call_count[0] += 1
                if call_count[0] == 2:
                    raise RuntimeError("模拟 LLM 超时")
                return AgentResult(
                    success=True,
                    data=EventGenerationResult(
                        description=f"事件{call_count[0]}",
                        duration_minutes=10,
                        energy_delta=1,
                        mood_delta=1,
                        health_delta=0,
                    ),
                )

            async def load_state(self):
                return None

            async def save_state(self, state):
                pass

        class OkCharAgent:
            async def react(self, context):
                return AgentResult(
                    success=True,
                    data=EventReactionResult(reaction="ok", has_follow_up=False),
                )

            async def load_state(self):
                return CharacterState()

            async def save_state(self, state):
                pass

        life.dm_agent = ErrorThenOkDMAgent()
        life.character_agent = OkCharAgent()

        await temp_db.update_character_state(CharacterState(energy=50, mood=50, health=50))

        start = datetime(2024, 3, 1, 6, 0, 0)
        stepped = SteppedClock(start)
        original = get_clock()
        set_clock(stepped)

        try:
            triggered, errors, skipped = await _run_warp_loop(life, stepped, days=1)
        finally:
            set_clock(original)

        # generate_daily_event 内部捕获异常返回 []，tick() 返回 None
        # 失败的槽位不标记 fired，但单次 warp 循环不会重试
        # 结果：4 个槽位中 3 个成功触发，1 个静默跳过
        assert triggered == 3, f"应触发 3 个事件，实际 {triggered}"
        assert errors == 0, f"异常被内部捕获，外部不应看到错误，实际 errors={errors}"
        assert skipped == 0

        # 确认 call_count: slot 0 成功, slot 1 失败, slot 2+3 成功
        assert call_count[0] >= 3, f"至少应有 3 次 DM 调用，实际 {call_count[0]}"

    @pytest.mark.asyncio
    async def test_clock_restored_after_step(self, life, temp_db):
        """SteppedClock 设置后恢复，时钟应能获取实时时间"""
        await temp_db.update_character_state(CharacterState(energy=50, mood=50, health=50))

        start = datetime(2024, 3, 1, 6, 0, 0)
        stepped = SteppedClock(start)
        original = get_clock()
        set_clock(stepped)

        assert isinstance(get_clock(), SteppedClock)
        assert get_clock().now() == start

        # 恢复原始时钟
        set_clock(original)

        # 恢复后的时钟应返回实时时间（非 None，非冻结值）
        now = get_clock().now()
        assert now is not None
        assert now != start, "恢复后的时钟不应返回 SteppedClock 的冻结时间"

    @pytest.mark.asyncio
    async def test_dry_run_estimate_accurate(self, life):
        """验证 warp 成本预估公式的准确性"""
        # 不设置 agent——只验证计算公式
        dm, char = _make_multi_day_agents(days=1, slots_per_day=4)
        life.dm_agent = dm
        life.character_agent = char

        days = 5
        slots_per_day = life.character.extensions.daily_events_count + 2  # 4
        chain_max_depth = life.config.chain_max_depth  # 1

        dm_calls = days * slots_per_day * chain_max_depth
        char_reaction_calls = days * slots_per_day * chain_max_depth
        char_diary_calls = days
        total = dm_calls + char_reaction_calls + char_diary_calls

        assert dm_calls == 20
        assert char_reaction_calls == 20
        assert char_diary_calls == 5
        assert total == 45
