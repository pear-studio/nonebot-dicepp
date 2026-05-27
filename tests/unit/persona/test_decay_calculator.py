"""
Phase 7c: DecayCalculator 边界条件单元测试

覆盖：禁用衰减、免衰减期、阶段下限保护、开关型衰减、should_apply_decay、无 last_interaction_at 等边界情况。
"""

import pytest
from datetime import datetime, timedelta
from plugins.DicePP.utils.time import wall_now

from plugins.DicePP.module.persona.data.models import RelationshipState
from plugins.DicePP.module.persona.game.decay import DecayCalculator, DecayConfig


class TestDecayCalculatorEdgeCases:
    """测试 DecayCalculator 边界条件"""

    def test_disabled_decay_returns_zero(self):
        config = DecayConfig(enabled=False)
        calc = DecayCalculator(config)
        rel = RelationshipState(
            user_id="u1",
            intimacy=50.0,
            last_interaction_at=wall_now() - timedelta(days=1),
            last_miss_sent_at=wall_now() - timedelta(days=1),
        )
        deltas, reason = calc.calculate_decay(rel)
        assert deltas.intimacy == 0.0
        assert "禁用" in reason

    def test_no_last_interaction_returns_zero(self):
        config = DecayConfig(enabled=True)
        calc = DecayCalculator(config)
        rel = RelationshipState(user_id="u1", intimacy=50.0, last_interaction_at=None)
        deltas, reason = calc.calculate_decay(rel)
        assert deltas.intimacy == 0.0
        assert "无上次互动记录" in reason

    def test_switch_off_no_decay(self):
        """开关关闭时（last_miss_sent_at=None）不衰减"""
        config = DecayConfig(enabled=True, grace_period_hours=0)
        calc = DecayCalculator(config)
        rel = RelationshipState(
            user_id="u1",
            intimacy=50.0,
            last_interaction_at=wall_now() - timedelta(days=1),
            last_miss_sent_at=None,
        )
        deltas, reason = calc.calculate_decay(rel)
        assert deltas.intimacy == 0.0
        assert "开关关闭" in reason

    def test_within_grace_period_no_decay(self):
        config = DecayConfig(enabled=True, grace_period_hours=8)
        calc = DecayCalculator(config)
        now = wall_now()
        rel = RelationshipState(
            user_id="u1",
            intimacy=50.0,
            last_interaction_at=now - timedelta(hours=4),
            last_miss_sent_at=now - timedelta(hours=4),
        )
        deltas, reason = calc.calculate_decay(rel, now=now)
        assert deltas.intimacy == 0.0
        assert "免衰减期内" in reason

    def test_stage_floor_protection_limits_decay(self):
        """阶段下限保护：peak_stage=1(疏远, floor=20)，当前 25，最多衰减 5"""
        config = DecayConfig(
            enabled=True,
            grace_period_hours=0,
            decay_rate_per_hour=10.0,
            daily_cap=100.0,
        )
        calc = DecayCalculator(config)
        rel = RelationshipState(
            user_id="u1",
            intimacy=25.0,
            passion=25.0,
            trust=25.0,
            secureness=25.0,
            peak_stage=1,
            last_interaction_at=wall_now() - timedelta(hours=1),
            last_miss_sent_at=wall_now() - timedelta(hours=1),
        )
        # floor = 20, current = 25, allowed_decay = 5
        deltas, reason = calc.calculate_decay(rel)
        assert deltas.intimacy == -5.0
        assert "阶段下限保护后 5.00" in reason

    def test_already_at_stage_floor_no_decay(self):
        """恰在阶段下限时零衰减"""
        config = DecayConfig(
            enabled=True,
            grace_period_hours=0,
            decay_rate_per_hour=1.0,
            daily_cap=100.0,
        )
        calc = DecayCalculator(config)
        rel = RelationshipState(
            user_id="u1",
            intimacy=20.0,
            passion=20.0,
            trust=20.0,
            secureness=20.0,
            peak_stage=1,
            last_interaction_at=wall_now() - timedelta(hours=10),
            last_miss_sent_at=wall_now() - timedelta(hours=10),
        )
        deltas, reason = calc.calculate_decay(rel)
        assert deltas.intimacy == 0.0
        assert "已到达阶段下限" in reason

    def test_peak_stage_intimate_locks_80(self):
        """亲密用户锁底 80：当前 82，peak_stage=4，衰减上限 2"""
        config = DecayConfig(
            enabled=True,
            grace_period_hours=0,
            decay_rate_per_hour=10.0,
            daily_cap=100.0,
        )
        calc = DecayCalculator(config)
        rel = RelationshipState(
            user_id="u1",
            intimacy=82.0,
            passion=82.0,
            trust=82.0,
            secureness=82.0,
            peak_stage=4,
            last_interaction_at=wall_now() - timedelta(hours=1),
            last_miss_sent_at=wall_now() - timedelta(hours=1),
        )
        deltas, reason = calc.calculate_decay(rel)
        assert deltas.intimacy == -2.0
        assert "阶段下限保护后 2.00" in reason

    def test_peak_stage_intimate_at_exactly_80_no_decay(self):
        """composite 恰为 80 时（阶段下界），零衰减"""
        config = DecayConfig(
            enabled=True,
            grace_period_hours=0,
            decay_rate_per_hour=10.0,
            daily_cap=100.0,
        )
        calc = DecayCalculator(config)
        rel = RelationshipState(
            user_id="u1",
            intimacy=80.0,
            passion=80.0,
            trust=80.0,
            secureness=80.0,
            peak_stage=4,
            last_interaction_at=wall_now() - timedelta(hours=1),
            last_miss_sent_at=wall_now() - timedelta(hours=1),
        )
        deltas, reason = calc.calculate_decay(rel)
        assert deltas.intimacy == 0.0
        assert "已到达阶段下限" in reason

    def test_peak_stage_does_not_affect_other_stages(self):
        """peak_stage 不影响其他阶段：peak_stage=2(友好, floor=40)，当前 58"""
        config = DecayConfig(
            enabled=True,
            grace_period_hours=0,
            decay_rate_per_hour=10.0,
            daily_cap=100.0,
        )
        calc = DecayCalculator(config)
        now = wall_now()
        rel = RelationshipState(
            user_id="u1",
            intimacy=58.0,
            passion=58.0,
            trust=58.0,
            secureness=58.0,
            peak_stage=2,
            last_interaction_at=now - timedelta(hours=1),
            last_miss_sent_at=now - timedelta(hours=1),
        )
        # floor = 40, current = 58, allowed_decay = 18
        deltas, reason = calc.calculate_decay(rel, now=now)
        assert abs(deltas.intimacy + 10.0) < 0.1  # raw = 10, capped by daily_cap=100, limited by allowed=18
        assert "阶段下限保护后 10.00" in reason

    def test_daily_cap_limits_decay(self):
        config = DecayConfig(
            enabled=True,
            grace_period_hours=0,
            decay_rate_per_hour=10.0,
            daily_cap=3.0,
        )
        calc = DecayCalculator(config)
        rel = RelationshipState(
            user_id="u1",
            intimacy=80.0,
            passion=80.0,
            trust=80.0,
            secureness=80.0,
            peak_stage=0,
            last_interaction_at=wall_now() - timedelta(hours=1),
            last_miss_sent_at=wall_now() - timedelta(hours=1),
        )
        deltas, _ = calc.calculate_decay(rel)
        assert deltas.intimacy == -3.0

    def test_should_apply_decay_true_after_grace(self):
        config = DecayConfig(enabled=True, grace_period_hours=1)
        calc = DecayCalculator(config)
        rel = RelationshipState(
            user_id="u1",
            intimacy=50.0,
            last_interaction_at=wall_now() - timedelta(hours=2),
            last_miss_sent_at=wall_now() - timedelta(hours=2),
        )
        assert calc.should_apply_decay(rel) is True

    def test_should_apply_decay_false_when_switch_off(self):
        """开关关闭时不应评估衰减"""
        config = DecayConfig(enabled=True, grace_period_hours=0)
        calc = DecayCalculator(config)
        rel = RelationshipState(
            user_id="u1",
            intimacy=50.0,
            last_interaction_at=wall_now() - timedelta(days=1),
            last_miss_sent_at=None,
        )
        assert calc.should_apply_decay(rel) is False

    def test_should_apply_decay_false_within_grace(self):
        config = DecayConfig(enabled=True, grace_period_hours=8)
        calc = DecayCalculator(config)
        now = wall_now()
        rel = RelationshipState(
            user_id="u1",
            intimacy=50.0,
            last_interaction_at=now - timedelta(minutes=30),
            last_miss_sent_at=now - timedelta(minutes=30),
        )
        assert calc.should_apply_decay(rel, now=now) is False

    def test_should_apply_decay_false_when_disabled(self):
        config = DecayConfig(enabled=False)
        calc = DecayCalculator(config)
        rel = RelationshipState(
            user_id="u1",
            intimacy=50.0,
            last_interaction_at=wall_now() - timedelta(days=1),
            last_miss_sent_at=wall_now() - timedelta(days=1),
        )
        assert calc.should_apply_decay(rel) is False

    def test_should_apply_decay_false_no_interaction(self):
        config = DecayConfig(enabled=True)
        calc = DecayCalculator(config)
        rel = RelationshipState(user_id="u1", intimacy=50.0, last_interaction_at=None)
        assert calc.should_apply_decay(rel) is False

    def test_effective_relationship_returns_copy(self):
        config = DecayConfig(
            enabled=True,
            grace_period_hours=0,
            decay_rate_per_hour=1.0,
            daily_cap=10.0,
        )
        calc = DecayCalculator(config)
        rel = RelationshipState(
            user_id="u1",
            intimacy=50.0,
            passion=50.0,
            trust=50.0,
            secureness=50.0,
            peak_stage=0,
            last_interaction_at=wall_now() - timedelta(hours=5),
            last_miss_sent_at=wall_now() - timedelta(hours=5),
        )
        before = rel.composite_score
        eff = calc.effective_relationship(rel)
        assert eff.composite_score < before
        assert rel.composite_score == before

    def test_incremental_no_double_decay(self):
        """同一时刻重复计算不应产生二次衰减"""
        config = DecayConfig(
            enabled=True,
            grace_period_hours=0,
            decay_rate_per_hour=1.0,
            daily_cap=100.0,
        )
        calc = DecayCalculator(config, timezone_name="UTC")
        t0 = datetime(2026, 1, 1, 12, 0, 0)
        now = datetime(2026, 1, 2, 12, 0, 0)
        rel = RelationshipState(
            user_id="u1",
            intimacy=80.0,
            passion=80.0,
            trust=80.0,
            secureness=80.0,
            peak_stage=0,
            last_interaction_at=t0,
            last_relationship_decay_applied_at=None,
            last_miss_sent_at=t0,
        )
        d1, _ = calc.calculate_decay(rel, now=now)
        assert d1.intimacy < -20.0
        rel.apply_deltas(d1, updated_at=now)
        rel.last_relationship_decay_applied_at = now

        d2, reason = calc.calculate_decay(rel, now=now)
        assert abs(d2.intimacy) < 0.01
        assert "无新增可衰减空闲时长" in reason


# ── 以下测试从 test_decay_incremental.py 合并 ──


def test_decay_incremental_same_moment_no_double_apply():
    t0 = datetime(2026, 1, 1, 12, 0, 0)
    t_batch = datetime(2026, 1, 5, 12, 0, 0)
    calc = DecayCalculator(
        DecayConfig(
            enabled=True,
            grace_period_hours=0,
            decay_rate_per_hour=1.0,
            daily_cap=100.0,
        ),
        timezone_name="UTC",
    )
    rel = RelationshipState(
        user_id="u1",
        intimacy=80.0,
        passion=80.0,
        trust=80.0,
        secureness=80.0,
        last_interaction_at=t0,
        last_relationship_decay_applied_at=None,
        last_miss_sent_at=t0,
        peak_stage=0,
    )
    d1, _ = calc.calculate_decay(rel, now=t_batch)
    assert d1.intimacy < -0.01
    rel.apply_deltas(d1, updated_at=t_batch)
    rel.last_relationship_decay_applied_at = t_batch

    d2, _ = calc.calculate_decay(rel, now=t_batch)
    assert abs(d2.intimacy) < 0.01


def test_decay_incremental_after_batch_user_message_only_new_idle():
    t0 = datetime(2026, 1, 1, 12, 0, 0)
    t_batch = datetime(2026, 1, 5, 12, 0, 0)
    t_msg = datetime(2026, 1, 6, 12, 0, 0)
    calc = DecayCalculator(
        DecayConfig(
            enabled=True,
            grace_period_hours=0,
            decay_rate_per_hour=1.0,
            daily_cap=50.0,
        ),
        timezone_name="UTC",
    )
    rel = RelationshipState(
        user_id="u1",
        intimacy=80.0,
        passion=80.0,
        trust=80.0,
        secureness=80.0,
        last_interaction_at=t0,
        last_relationship_decay_applied_at=None,
        last_miss_sent_at=t0,
        peak_stage=0,
    )
    d_batch, _ = calc.calculate_decay(rel, now=t_batch)
    rel.apply_deltas(d_batch, updated_at=t_batch)
    rel.last_relationship_decay_applied_at = t_batch

    d_chat, _ = calc.calculate_decay(rel, now=t_msg)
    # 仅 1 天增量：约 24h * 1.0 = 24，受 daily_cap 50
    # 但 batch 后 peak_stage 自动升为 1（floor=20），当前 composite=30，allowed_decay=10
    assert d_chat.intimacy == -10.0


def test_decay_after_miss_accounts_full_idle():
    """想念后首次衰减按 last_interaction_at 起算，补扣全部 idle（受 daily_cap 限制）"""
    t0 = datetime(2026, 1, 1, 12, 0, 0)
    t_miss = datetime(2026, 1, 3, 12, 0, 0)
    calc = DecayCalculator(
        DecayConfig(
            enabled=True,
            grace_period_hours=0,
            decay_rate_per_hour=1.0,
            daily_cap=100.0,
        ),
        timezone_name="UTC",
    )
    rel = RelationshipState(
        user_id="u1",
        intimacy=80.0,
        passion=80.0,
        trust=80.0,
        secureness=80.0,
        last_interaction_at=t0,
        last_relationship_decay_applied_at=None,
        last_miss_sent_at=None,
        peak_stage=0,
    )
    # 想念前：开关关闭，不衰减
    d1, reason1 = calc.calculate_decay(rel, now=t_miss)
    assert abs(d1.intimacy) < 0.01
    assert "开关关闭" in reason1

    # 想念发出：打开开关
    rel.last_miss_sent_at = t_miss

    # 想念后首次衰减：按 last_interaction_at 起算，补扣 48h idle
    d2, reason2 = calc.calculate_decay(rel, now=t_miss)
    assert d2.intimacy == -48.0
    assert "增量可衰减 48.00h" in reason2


def test_decay_switch_off_then_on():
    """开关型衰减：想念前不衰减，想念后正常衰减，用户回应后重置"""
    t0 = datetime(2026, 1, 1, 12, 0, 0)
    t_miss = datetime(2026, 1, 3, 12, 0, 0)
    t_after_miss = datetime(2026, 1, 4, 12, 0, 0)
    calc = DecayCalculator(
        DecayConfig(
            enabled=True,
            grace_period_hours=0,
            decay_rate_per_hour=1.0,
            daily_cap=100.0,
        ),
        timezone_name="UTC",
    )
    rel = RelationshipState(
        user_id="u1",
        intimacy=80.0,
        passion=80.0,
        trust=80.0,
        secureness=80.0,
        last_interaction_at=t0,
        last_relationship_decay_applied_at=None,
        last_miss_sent_at=None,  # 开关关闭
        peak_stage=0,
    )
    # 想念前：开关关闭，不衰减
    d1, reason1 = calc.calculate_decay(rel, now=t_miss)
    assert abs(d1.intimacy) < 0.01
    assert "开关关闭" in reason1

    # 想念发出：打开开关
    rel.last_miss_sent_at = t_miss

    # 想念后：正常衰减
    d2, reason2 = calc.calculate_decay(rel, now=t_after_miss)
    assert d2.intimacy < -20.0

    # 用户回应：重置开关和水位
    rel.last_miss_sent_at = None
    rel.last_relationship_decay_applied_at = None
    rel.last_interaction_at = t_after_miss

    d3, reason3 = calc.calculate_decay(rel, now=t_after_miss)
    assert abs(d3.intimacy) < 0.01
    assert "开关关闭" in reason3


# ── 以下测试从 test_decay_lazy.py 合并 ──


def test_effective_relationship_leaves_original_unchanged():
    calc = DecayCalculator(
        DecayConfig(
            enabled=True,
            grace_period_hours=0,
            decay_rate_per_hour=1.0,
            daily_cap=10.0,
        )
    )
    rel = RelationshipState(
        user_id="u1",
        intimacy=50.0,
        passion=50.0,
        trust=50.0,
        secureness=50.0,
        last_interaction_at=wall_now() - timedelta(hours=5),
        last_miss_sent_at=wall_now() - timedelta(hours=5),
        peak_stage=0,
    )
    before = rel.composite_score
    eff = calc.effective_relationship(rel)
    assert eff.composite_score < before
    assert rel.composite_score == before
