"""时间衰减：批处理与对话路径共用增量水位，避免对同一空闲窗口重复扣减。"""


from datetime import datetime, timedelta


from plugins.DicePP.module.persona.data.models import RelationshipState
from plugins.DicePP.module.persona.game.decay import DecayCalculator, DecayConfig


def test_decay_incremental_same_moment_no_double_apply():
    t0 = datetime(2026, 1, 1, 12, 0, 0)
    t_batch = datetime(2026, 1, 5, 12, 0, 0)
    calc = DecayCalculator(
        DecayConfig(
            enabled=True,
            grace_period_hours=0,
            decay_rate_per_hour=1.0,
            daily_cap=100.0,
            floor_offset=-100.0,
        ),
        timezone_name="UTC",
    )
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
    d1, _ = calc.calculate_decay(rel, 30.0, now=t_batch)
    assert d1.intimacy < -0.01
    rel.apply_deltas(d1, updated_at=t_batch)
    rel.last_relationship_decay_applied_at = t_batch

    d2, _ = calc.calculate_decay(rel, 30.0, now=t_batch)
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
            floor_offset=-100.0,
        ),
        timezone_name="UTC",
    )
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
    d_batch, _ = calc.calculate_decay(rel, 30.0, now=t_batch)
    rel.apply_deltas(d_batch, updated_at=t_batch)
    rel.last_relationship_decay_applied_at = t_batch

    d_chat, _ = calc.calculate_decay(rel, 30.0, now=t_msg)
    # 仅 1 天增量：约 24h * 1.0 = 24，受 daily_cap 50
    assert d_chat.intimacy < -20.0
