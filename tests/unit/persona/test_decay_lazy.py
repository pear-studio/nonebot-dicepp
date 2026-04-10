"""好感度衰减：惰性 effective_relationship 不修改原对象"""

import sys
from datetime import datetime, timedelta

sys.path.insert(0, "src")

from plugins.DicePP.module.persona.data.models import RelationshipState
from plugins.DicePP.module.persona.game.decay import DecayCalculator, DecayConfig


def test_effective_relationship_leaves_original_unchanged():
    calc = DecayCalculator(
        DecayConfig(
            enabled=True,
            grace_period_hours=0,
            decay_rate_per_hour=1.0,
            daily_cap=10.0,
            floor_offset=-100.0,
        )
    )
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
    eff = calc.effective_relationship(rel, 30.0)
    assert eff.composite_score < before
    assert rel.composite_score == before
