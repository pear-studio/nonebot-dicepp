"""
Phase 7c: DecayCalculator 边界条件单元测试

覆盖：禁用衰减、免衰减期、下限保护、should_apply_decay、无 last_interaction_at 等边界情况。
"""

import pytest
from datetime import datetime, timedelta

from plugins.DicePP.module.persona.data.models import RelationshipState
from plugins.DicePP.module.persona.game.decay import DecayCalculator, DecayConfig


class TestDecayCalculatorEdgeCases:
    """测试 DecayCalculator 边界条件"""

    def test_disabled_decay_returns_zero(self):
        config = DecayConfig(enabled=False)
        calc = DecayCalculator(config)
        rel = RelationshipState(
            user_id="u1",
            group_id="",
            intimacy=50.0,
            last_interaction_at=datetime.now() - timedelta(days=1),
        )
        deltas, reason = calc.calculate_decay(rel, initial_score=30.0)
        assert deltas.intimacy == 0.0
        assert "禁用" in reason

    def test_no_last_interaction_returns_zero(self):
        config = DecayConfig(enabled=True)
        calc = DecayCalculator(config)
        rel = RelationshipState(user_id="u1", group_id="", intimacy=50.0, last_interaction_at=None)
        deltas, reason = calc.calculate_decay(rel, initial_score=30.0)
        assert deltas.intimacy == 0.0
        assert "无上次互动记录" in reason

    def test_within_grace_period_no_decay(self):
        config = DecayConfig(enabled=True, grace_period_hours=8)
        calc = DecayCalculator(config)
        rel = RelationshipState(
            user_id="u1",
            group_id="",
            intimacy=50.0,
            last_interaction_at=datetime.now() - timedelta(hours=4),
        )
        deltas, reason = calc.calculate_decay(rel, initial_score=30.0)
        assert deltas.intimacy == 0.0
        assert "免衰减期内" in reason

    def test_floor_protection_limits_decay(self):
        config = DecayConfig(
            enabled=True,
            grace_period_hours=0,
            decay_rate_per_hour=10.0,
            daily_cap=100.0,
            floor_offset=0.0,
        )
        calc = DecayCalculator(config)
        rel = RelationshipState(
            user_id="u1",
            group_id="",
            intimacy=35.0,
            passion=35.0,
            trust=35.0,
            secureness=35.0,
            last_interaction_at=datetime.now() - timedelta(hours=1),
        )
        # floor = 30 + 0 = 30, current = 35, max_decay = 5
        deltas, reason = calc.calculate_decay(rel, initial_score=30.0)
        assert deltas.intimacy == -5.0
        assert "下限保护后 5.00" in reason

    def test_already_at_floor_no_decay(self):
        config = DecayConfig(
            enabled=True,
            grace_period_hours=0,
            decay_rate_per_hour=1.0,
            daily_cap=100.0,
            floor_offset=0.0,
        )
        calc = DecayCalculator(config)
        rel = RelationshipState(
            user_id="u1",
            group_id="",
            intimacy=30.0,
            passion=30.0,
            trust=30.0,
            secureness=30.0,
            last_interaction_at=datetime.now() - timedelta(hours=10),
        )
        deltas, reason = calc.calculate_decay(rel, initial_score=30.0)
        assert deltas.intimacy == 0.0
        assert "已到达衰减下限" in reason

    def test_daily_cap_limits_decay(self):
        config = DecayConfig(
            enabled=True,
            grace_period_hours=0,
            decay_rate_per_hour=10.0,
            daily_cap=3.0,
            floor_offset=-100.0,
        )
        calc = DecayCalculator(config)
        rel = RelationshipState(
            user_id="u1",
            group_id="",
            intimacy=80.0,
            passion=80.0,
            trust=80.0,
            secureness=80.0,
            last_interaction_at=datetime.now() - timedelta(hours=1),
        )
        deltas, _ = calc.calculate_decay(rel, initial_score=30.0)
        assert deltas.intimacy == -3.0

    def test_should_apply_decay_true_after_grace(self):
        config = DecayConfig(enabled=True, grace_period_hours=1)
        calc = DecayCalculator(config)
        rel = RelationshipState(
            user_id="u1",
            group_id="",
            intimacy=50.0,
            last_interaction_at=datetime.now() - timedelta(hours=2),
        )
        assert calc.should_apply_decay(rel) is True

    def test_should_apply_decay_false_within_grace(self):
        config = DecayConfig(enabled=True, grace_period_hours=8)
        calc = DecayCalculator(config)
        rel = RelationshipState(
            user_id="u1",
            group_id="",
            intimacy=50.0,
            last_interaction_at=datetime.now() - timedelta(minutes=30),
        )
        assert calc.should_apply_decay(rel) is False

    def test_should_apply_decay_false_when_disabled(self):
        config = DecayConfig(enabled=False)
        calc = DecayCalculator(config)
        rel = RelationshipState(
            user_id="u1",
            group_id="",
            intimacy=50.0,
            last_interaction_at=datetime.now() - timedelta(days=1),
        )
        assert calc.should_apply_decay(rel) is False

    def test_should_apply_decay_false_no_interaction(self):
        config = DecayConfig(enabled=True)
        calc = DecayCalculator(config)
        rel = RelationshipState(user_id="u1", group_id="", intimacy=50.0, last_interaction_at=None)
        assert calc.should_apply_decay(rel) is False

    def test_effective_relationship_returns_copy(self):
        config = DecayConfig(
            enabled=True,
            grace_period_hours=0,
            decay_rate_per_hour=1.0,
            daily_cap=10.0,
            floor_offset=-100.0,
        )
        calc = DecayCalculator(config)
        rel = RelationshipState(
            user_id="u1",
            group_id="",
            intimacy=50.0,
            passion=50.0,
            trust=50.0,
            secureness=50.0,
            last_interaction_at=datetime.now() - timedelta(hours=5),
        )
        before = rel.composite_score
        eff = calc.effective_relationship(rel, initial_score=30.0)
        assert eff.composite_score < before
        assert rel.composite_score == before

    def test_incremental_no_double_decay(self):
        """同一时刻重复计算不应产生二次衰减"""
        config = DecayConfig(
            enabled=True,
            grace_period_hours=0,
            decay_rate_per_hour=1.0,
            daily_cap=100.0,
            floor_offset=-100.0,
        )
        calc = DecayCalculator(config, timezone_name="UTC")
        t0 = datetime(2026, 1, 1, 12, 0, 0)
        now = datetime(2026, 1, 2, 12, 0, 0)
        rel = RelationshipState(
            user_id="u1",
            group_id="",
            intimacy=80.0,
            passion=80.0,
            trust=80.0,
            secureness=80.0,
            last_interaction_at=t0,
            last_relationship_decay_applied_at=None,
        )
        d1, _ = calc.calculate_decay(rel, initial_score=30.0, now=now)
        assert d1.intimacy < -20.0
        rel.apply_deltas(d1)
        rel.last_relationship_decay_applied_at = now

        d2, reason = calc.calculate_decay(rel, initial_score=30.0, now=now)
        assert abs(d2.intimacy) < 0.01
        assert "无新增可衰减空闲时长" in reason
