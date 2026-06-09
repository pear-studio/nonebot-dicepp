"""
Phase 7c: DecayCalculator 边界条件单元测试

覆盖：禁用衰减、免衰减期、半衰期衰减、should_apply_decay、无 last_interaction_at 等边界情况。
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
            last_interaction_at=wall_now() - timedelta(days=30),
            last_miss_sent_at=wall_now() - timedelta(days=30),
        )
        deltas, familiarity_decay, reason = calc.calculate_decay(rel)
        assert deltas.intimacy == 0.0
        assert "禁用" in reason

    def test_no_last_interaction_returns_zero(self):
        config = DecayConfig(enabled=True)
        calc = DecayCalculator(config)
        rel = RelationshipState(user_id="u1", intimacy=50.0, last_interaction_at=None)
        deltas, familiarity_decay, reason = calc.calculate_decay(rel)
        assert deltas.intimacy == 0.0
        assert "无上次互动记录" in reason

    def test_switch_off_no_decay(self):
        """开关关闭时（last_miss_sent_at=None）不衰减"""
        config = DecayConfig(enabled=True, grace_period_hours=0)
        calc = DecayCalculator(config)
        rel = RelationshipState(
            user_id="u1",
            intimacy=50.0,
            last_interaction_at=wall_now() - timedelta(days=30),
            last_miss_sent_at=None,
        )
        deltas, familiarity_decay, reason = calc.calculate_decay(rel)
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
        deltas, familiarity_decay, reason = calc.calculate_decay(rel, now=now)
        assert deltas.intimacy == 0.0
        assert "免衰减期内" in reason

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

    def test_half_life_decay_applied(self):
        """验证半衰期衰减模型：空闲足够天后 intimacy 和 familiarity 均有衰减"""
        config = DecayConfig(enabled=True, grace_period_hours=0)
        calc = DecayCalculator(config)
        rel = RelationshipState(
            user_id="u1",
            intimacy=80.0,
            familiarity=80.0,
            peak_intimacy=80.0,
            peak_familiarity=80.0,
            last_interaction_at=wall_now() - timedelta(days=60),
            last_miss_sent_at=wall_now() - timedelta(days=60),
        )
        deltas, familiarity_decay, reason = calc.calculate_decay(rel)
        assert deltas.intimacy < -10.0  # 显著衰减
        assert abs(familiarity_decay) > 5.0

    def test_floor_protection_limits_decay(self):
        """floor_ratio 保护：即使空闲很久也不会衰减到 0 以下"""
        config = DecayConfig(enabled=True, grace_period_hours=0)
        calc = DecayCalculator(config)
        rel = RelationshipState(
            user_id="u1",
            intimacy=40.0,
            familiarity=40.0,
            peak_intimacy=80.0,
            peak_familiarity=80.0,
            last_interaction_at=wall_now() - timedelta(days=365),
            last_miss_sent_at=wall_now() - timedelta(days=365),
        )
        # floor = peak * 0.5 = 40.0，当前值 40.0，gap=0，不应衰减
        deltas, familiarity_decay, reason = calc.calculate_decay(rel)
        assert deltas.intimacy == 0.0
        assert familiarity_decay == 0.0

    def test_effective_relationship_returns_copy(self):
        config = DecayConfig(enabled=True, grace_period_hours=0)
        calc = DecayCalculator(config)
        rel = RelationshipState(
            user_id="u1",
            intimacy=80.0,
            familiarity=80.0,
            peak_intimacy=80.0,
            peak_familiarity=80.0,
            last_interaction_at=wall_now() - timedelta(days=30),
            last_miss_sent_at=wall_now() - timedelta(days=30),
        )
        before = rel.composite_score
        eff = calc.effective_relationship(rel)
        assert eff.composite_score < before
        assert rel.composite_score == before

    def test_incremental_no_double_decay(self):
        """同一时刻重复计算不应产生二次衰减"""
        config = DecayConfig(enabled=True, grace_period_hours=0)
        calc = DecayCalculator(config, timezone_name="UTC")
        t0 = datetime(2026, 1, 1, 12, 0, 0)
        now = datetime(2026, 1, 2, 12, 0, 0)
        rel = RelationshipState(
            user_id="u1",
            intimacy=80.0,
            familiarity=80.0,
            peak_intimacy=80.0,
            peak_familiarity=80.0,
            last_interaction_at=t0,
            last_relationship_decay_applied_at=None,
            last_miss_sent_at=t0,
        )
        d1, f1, _ = calc.calculate_decay(rel, now=now)
        assert d1.intimacy < -0.01 or f1 > 0.01
        rel.apply_deltas(d1, updated_at=now)
        rel.apply_familiarity_delta(-f1, updated_at=now)
        rel.last_relationship_decay_applied_at = now

        d2, f2, reason = calc.calculate_decay(rel, now=now)
        assert abs(d2.intimacy) < 0.01
        assert abs(f2) < 0.01
        assert "无新增可衰减空闲时长" in reason


# ── 以下适配旧模型测试（半衰期模型替换后保持运行）──


def test_decay_switch_off_then_on():
    """开关型衰减：想念前不衰减，想念后正常衰减，用户回应后重置"""
    t0 = datetime(2026, 1, 1, 12, 0, 0)
    t_miss = datetime(2026, 1, 3, 12, 0, 0)
    t_after_miss = datetime(2026, 1, 4, 12, 0, 0)
    calc = DecayCalculator(
        DecayConfig(enabled=True, grace_period_hours=0),
        timezone_name="UTC",
    )
    rel = RelationshipState(
        user_id="u1",
        intimacy=80.0,
        familiarity=80.0,
        peak_intimacy=80.0,
        peak_familiarity=80.0,
        last_interaction_at=t0,
        last_relationship_decay_applied_at=None,
        last_miss_sent_at=None,  # 开关关闭
    )
    # 想念前：开关关闭，不衰减
    d1, f1, reason1 = calc.calculate_decay(rel, now=t_miss)
    assert abs(d1.intimacy) < 0.01
    assert abs(f1) < 0.01
    assert "开关关闭" in reason1

    # 想念发出：打开开关
    rel.last_miss_sent_at = t_miss

    # 想念后：正常衰减
    d2, f2, reason2 = calc.calculate_decay(rel, now=t_after_miss)
    assert d2.intimacy < -0.01 or f2 > 0.01

    # 用户回应：重置开关和水位
    rel.last_miss_sent_at = None
    rel.last_relationship_decay_applied_at = None
    rel.last_interaction_at = t_after_miss

    d3, f3, reason3 = calc.calculate_decay(rel, now=t_after_miss)
    assert abs(d3.intimacy) < 0.01
    assert abs(f3) < 0.01
    assert "开关关闭" in reason3


def test_effective_relationship_leaves_original_unchanged():
    calc = DecayCalculator(
        DecayConfig(enabled=True, grace_period_hours=0),
    )
    rel = RelationshipState(
        user_id="u1",
        intimacy=80.0,
        familiarity=80.0,
        peak_intimacy=80.0,
        peak_familiarity=80.0,
        last_interaction_at=wall_now() - timedelta(days=30),
        last_miss_sent_at=wall_now() - timedelta(days=30),
    )
    before = rel.composite_score
    eff = calc.effective_relationship(rel)
    assert eff.composite_score < before
    assert rel.composite_score == before
