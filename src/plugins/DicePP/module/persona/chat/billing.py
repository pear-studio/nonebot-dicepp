"""计费策略

封装对话计费逻辑，替代 ChatSession 中"双处维护"的注释不变量。
"""
from typing import Optional

from ..llm.router import LLMRouter


class BillingPolicy:
    """对话计费策略

    统一处理单轮对话的额度扣减，避免 coordinator 中间轮与最终轮
    各自维护计费逻辑导致的不一致。
    """

    def __init__(self, router: Optional[LLMRouter]) -> None:
        self._router = router

    async def charge(self, user_id: str) -> None:
        """为指定用户扣减一次对话额度"""
        if self._router and user_id:
            await self._router.increment_usage(user_id)
