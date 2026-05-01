from typing import Optional

from ..data.models import RelationshipState
from ..game.decay import DecayCalculator
from ..character.models import Character


def effective_for_proactive(
    rel: RelationshipState,
    decay_calculator: Optional[DecayCalculator],
    character: Optional[Character],
) -> RelationshipState:
    """与对话侧一致：阈值/概率按惰性时间衰减后的综合分（不写库）。"""
    if not decay_calculator or not character:
        return rel
    initial = float(character.extensions.initial_relationship)
    return decay_calculator.effective_relationship(rel, initial)
