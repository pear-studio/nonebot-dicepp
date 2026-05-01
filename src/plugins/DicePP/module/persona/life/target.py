"""
主动消息目标选择器

组合 force / normal 策略生成候选目标；最终发送前的 mute 与最小间隔检查由调度器负责。
"""
from typing import List, Optional, Set
import logging

from .models import ShareTarget
from ..data.store import PersonaDataStore
from ..game.decay import DecayCalculator
from ..character.models import Character
from core.config.pydantic_models import PersonaConfig
from .utils import effective_for_proactive

logger = logging.getLogger("persona.target_selector")

FORCE_PRIORITY_BASE = 10000
NORMAL_HIGH_PRIORITY_BASE = 100
NORMAL_MEDIUM_PRIORITY_BASE = 50
FORCE_LIST_WARNING_THRESHOLD = 10


class TargetSelector:
    def __init__(
        self,
        data_store: PersonaDataStore,
        bot_config: PersonaConfig,
        decay_calculator: Optional[DecayCalculator] = None,
        character: Optional[Character] = None,
    ):
        self.data_store = data_store
        self.bot_config = bot_config
        self._decay_calculator = decay_calculator
        self._character = character
        total_force = len(bot_config.proactive_always_send_users) + len(bot_config.proactive_always_send_groups)
        if total_force > FORCE_LIST_WARNING_THRESHOLD:
            logger.warning(
                f"force 目标列表长度 ({total_force}) 超过 {FORCE_LIST_WARNING_THRESHOLD}，"
                "大量配置可能导致高频发送，请留意运维"
            )

    def is_force_user(self, user_id: str) -> bool:
        """检查用户是否在 always_send_users 中。"""
        return user_id in self.bot_config.proactive_always_send_users

    def is_force_group(self, group_id: str) -> bool:
        """检查群是否在 always_send_groups 中。"""
        return group_id in self.bot_config.proactive_always_send_groups

    async def select_share_targets(self) -> List[ShareTarget]:
        """
        选择分享目标。
        优先级：force 目标排在 normal 目标之前。
        """
        targets: List[ShareTarget] = []
        seen_ids: Set[str] = set()

        # === Force 策略 ===
        for user_id in self.bot_config.proactive_always_send_users:
            if not user_id:
                continue
            key = f"user:{user_id}"
            if key in seen_ids:
                continue
            seen_ids.add(key)
            targets.append(
                ShareTarget(
                    user_id=user_id,
                    priority=FORCE_PRIORITY_BASE,
                    score=100.0,
                    policy="force",
                )
            )

        for group_id in self.bot_config.proactive_always_send_groups:
            if not group_id:
                continue
            key = f"group:{group_id}"
            if key in seen_ids:
                continue
            seen_ids.add(key)
            targets.append(
                ShareTarget(
                    user_id="",
                    group_id=group_id,
                    is_group=True,
                    priority=FORCE_PRIORITY_BASE,
                    score=100.0,
                    policy="force",
                )
            )

        # === Normal 策略 ===
        try:
            high_score = await self.data_store.get_top_relationships(limit=20)
            for rel in high_score:
                eff = effective_for_proactive(rel, self._decay_calculator, self._character)
                key = f"user:{rel.user_id}"
                if key in seen_ids:
                    continue
                if eff.composite_score >= 60 and not rel.group_id:
                    seen_ids.add(key)
                    targets.append(
                        ShareTarget(
                            user_id=rel.user_id,
                            priority=NORMAL_HIGH_PRIORITY_BASE + int(eff.composite_score),
                            score=eff.composite_score,
                            policy="normal",
                        )
                    )
                elif 40 <= eff.composite_score < 60 and not rel.group_id:
                    seen_ids.add(key)
                    targets.append(
                        ShareTarget(
                            user_id=rel.user_id,
                            priority=NORMAL_MEDIUM_PRIORITY_BASE + int(eff.composite_score),
                            score=eff.composite_score,
                            policy="normal",
                        )
                    )
        except Exception as e:
            logger.error(f"获取高好感度用户失败: {e}")

        try:
            group_activities = await self.data_store.get_all_group_activities(
                min_score=self.bot_config.group_activity_min_threshold
            )
            for activity in group_activities:
                key = f"group:{activity.group_id}"
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                targets.append(
                    ShareTarget(
                        user_id="",
                        group_id=activity.group_id,
                        is_group=True,
                        priority=int(activity.score),
                        score=activity.score,
                        policy="normal",
                    )
                )
        except Exception as e:
            logger.debug(f"获取群活跃度失败: {e}")

        # 按优先级排序（force 已在前面，但再排一次确保稳定）
        targets.sort(key=lambda x: x.priority, reverse=True)
        return targets
