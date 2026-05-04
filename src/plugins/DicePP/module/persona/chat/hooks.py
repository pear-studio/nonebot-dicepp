"""对话生命周期钩子 — 所有钩子默认 no-op，按需覆盖

设计说明
========
为什么 chat 域有 hooks 而 life 域没有？

  - **chat 是同步用户面**：每条用户消息都是可观测、可干预的；外部插件
    （白名单、敏感词过滤、A/B 测试上下文）需要在请求/响应链上插桩。
    所以 chat 暴露 4 个钩子点，覆盖"前置短路 / 上下文构建后 / 发送前 /
    出错"四个语义点。
  - **life 是后台模拟**：``CharacterLife`` / ``LifeSimulator`` 的 tick 由
    时钟驱动，没有外部调用方需要观测。Phase 2 的 R5 已删除遗留的
    ``LifeHooks`` 占位类，正式确立"life 域不开放钩子"。
  - 这种**不对称是有意的**：策略统一不等于结构统一。如果未来 life 域
    也出现外部观测需求（如计费、Trace 注入），再单独建立其钩子契约。

钩子触发顺序（``ChatSession.chat`` 路径）：

  1. ``on_before_chat``：消息进入但未做任何处理；返回非 None 直接短路
  2. ``on_after_context_built``：上下文已构建好但尚未送 LLM
  3. ``on_before_send``：LLM 已返回最终内容，发送前可篡改或自行发送（返回 None）
  4. ``on_error``：任何 except 块兜底；返回非 None 替换默认错误消息
"""
from typing import List, Dict, Optional


class ChatHooks:
    """对话生命周期钩子"""

    async def on_before_chat(
        self, user_id: str, group_id: str, message: str
    ) -> Optional[str]:
        """返回非 None 则短路，直接作为回复返回；None 表示继续正常流程"""
        return None

    async def on_after_context_built(
        self, user_id: str, group_id: str, messages: List[Dict]
    ) -> List[Dict]:
        """修改或增强注入 LLM 的消息列表"""
        return messages

    async def on_before_send(
        self, user_id: str, group_id: str, content: str
    ) -> Optional[str]:
        """修改最终发送内容。返回 None 表示已处理（已发送），不再走默认发送流程。
        实现者必须自行完成发送，否则会出现消息丢失（已加日志告警）。
        """
        return content

    async def on_error(
        self, user_id: str, group_id: str, error: Exception
    ) -> Optional[str]:
        """自定义错误回复，返回 None 使用默认错误消息"""
        return None
