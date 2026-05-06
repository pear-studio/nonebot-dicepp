"""消息处理管道 — 链式转换 SendAction 元数据，不做 I/O"""
from typing import List, Dict, Any
from dataclasses import dataclass
from abc import ABC, abstractmethod


@dataclass
class SendAction:
    """发送动作元数据"""

    user_id: str
    group_id: str
    content: str
    delay_seconds: float = 0.0  # Pipeline 可设置, Port._execute_background 读取并执行
    skip_history_record: bool = False  # 是否跳过 adapter 层历史记录


class MessageStage(ABC):
    """转换 SendAction 元数据, 不执行 I/O

    实现样例：

        class TokenBudgetStage(MessageStage):
            def __init__(self, max_tokens: int):
                self.max_tokens = max_tokens

            async def process(self, actions):
                for a in actions:
                    while estimate_tokens(a.content) > self.max_tokens:
                        a.content = a.content[:-1]
                return actions

    阶段约束：
      - 不做 I/O（不发消息、不读数据库），只修改 ``SendAction`` 字段
      - 必须就地或返回新列表，不抛异常打断流水线
      - 阶段之间无显式协议依赖，但写入 ``content`` / ``delay_seconds``
        / ``skip_history_record`` 时应能容忍前序阶段已修改
    """

    @abstractmethod
    async def process(self, actions: List[SendAction]) -> List[SendAction]:
        ...


class MessagePipeline:
    """消息处理管道 — 链式转换 SendAction 元数据"""

    def __init__(self):
        self._stages: List[MessageStage] = []

    def add(self, stage: MessageStage) -> "MessagePipeline":
        self._stages.append(stage)
        return self

    async def process(self, actions: List[SendAction]) -> List[SendAction]:
        result = actions
        for stage in self._stages:
            result = await stage.process(result)
        return result


class TruncateStage(MessageStage):
    """超长消息截断"""

    def __init__(self, max_chars: int = 2000):
        self.max_chars = max_chars

    async def process(self, actions: List[SendAction]) -> List[SendAction]:
        for action in actions:
            if len(action.content) > self.max_chars:
                action.content = action.content[: self.max_chars - 3] + "..."
        return actions


def make_segment(content: str, group_id: str = "") -> Dict[str, Any]:
    """构造 send_segmented 兼容的消息段"""
    return {"content": content, "skip_history_record": bool(group_id)}
