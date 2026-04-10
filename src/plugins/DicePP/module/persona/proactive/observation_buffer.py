"""
群聊观察缓冲系统

实现消息缓冲、动态阈值、观察提取
"""
import re
from typing import List, Dict, Optional, Set
from datetime import datetime, timedelta
import logging

from ..agents.event_agent import EventGenerationAgent
from ..data.store import PersonaDataStore

logger = logging.getLogger("persona.observation")


class BufferedMessage:
    """缓冲的消息"""

    def __init__(
        self,
        user_id: str,
        nickname: str,
        content: str,
        timestamp: datetime,
    ):
        self.user_id = user_id
        self.nickname = nickname
        self.content = content
        self.timestamp = timestamp


class ObservationBuffer:
    """群聊观察缓冲区"""

    def __init__(
        self,
        group_id: str,
        initial_threshold: int = 20,
        max_threshold: int = 60,
        min_threshold: int = 5,
        max_buffer_size: int = 60,
        max_records_per_group: int = 30,
    ):
        self.group_id = group_id
        self.threshold = initial_threshold
        self.max_threshold = max_threshold
        self.min_threshold = min_threshold
        self.max_buffer_size = max_buffer_size
        self.max_records_per_group = max_records_per_group

        self._buffer: List[BufferedMessage] = []
        self._last_trigger_time: Optional[datetime] = None

    def should_filter(self, content: str) -> bool:
        """
        检查消息是否应该被过滤

        Returns:
            True = 应该过滤掉
        """
        # 空消息
        if not content or not content.strip():
            return True

        content = content.strip()

        # 指令消息（以 . 或 。开头）
        if content.startswith((".", "。", "/", "!", "！")):
            return True

        # 纯 emoji
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"  # emoticons
            "\U0001F300-\U0001F5FF"  # symbols & pictographs
            "\U0001F680-\U0001F6FF"  # transport & map symbols
            "\U0001F1E0-\U0001F1FF"  # flags
            "\U00002702-\U000027B0"
            "\U000024C2-\U0001F251"
            "]+",
            flags=re.UNICODE,
        )
        if emoji_pattern.fullmatch(content):
            return True

        # 长度检查
        if len(content) < 5:
            return True
        if len(content) > 500:
            return True

        # 检查常见图片/文件标记
        lower = content.lower()
        file_markers = ["[图片]", "[表情]", "[语音]", "[视频]", "[文件]", "[动画表情]"]
        if any(marker in lower for marker in file_markers):
            return True

        return False

    def add_message(
        self,
        user_id: str,
        nickname: str,
        content: str,
    ) -> bool:
        """
        添加消息到缓冲

        Returns:
            是否触发了提取
        """
        # 过滤检查
        if self.should_filter(content):
            return False

        msg = BufferedMessage(
            user_id=user_id,
            nickname=nickname,
            content=content,
            timestamp=datetime.now(),
        )
        self._buffer.append(msg)

        # 限制缓冲区大小
        if len(self._buffer) > self.max_buffer_size:
            self._buffer = self._buffer[-self.max_buffer_size:]

        # 检查是否触发提取
        return self._should_trigger()

    def _should_trigger(self) -> bool:
        """检查是否应该触发提取"""
        # 检查消息数量
        if len(self._buffer) >= self.threshold:
            self._adjust_threshold(fast=True)
            return True

        # 检查时间（超过2小时）
        if self._buffer:
            first_msg_time = self._buffer[0].timestamp
            if datetime.now() - first_msg_time > timedelta(hours=2):
                if len(self._buffer) >= 5:  # 至少有5条消息
                    self._adjust_threshold(fast=False)
                    return True

        return False

    def _adjust_threshold(self, fast: bool) -> None:
        """
        调整阈值

        Args:
            fast: 是否快速触发（触发间隔短）
        """
        now = datetime.now()

        if self._last_trigger_time:
            time_since_last = now - self._last_trigger_time

            if fast and time_since_last < timedelta(minutes=30):
                # 快速触发，增加阈值
                self.threshold = min(self.max_threshold, self.threshold + 10)
            elif not fast and time_since_last > timedelta(hours=3):
                # 慢速触发，减少阈值
                self.threshold = max(self.min_threshold, self.threshold - 5)

        self._last_trigger_time = now

    def get_messages_for_extraction(self) -> List[BufferedMessage]:
        """获取用于提取观察的消息并清空缓冲"""
        messages = self._buffer.copy()
        self._buffer.clear()
        return messages

    def get_status(self) -> Dict:
        """获取缓冲区状态（用于调试）"""
        return {
            "buffer_size": len(self._buffer),
            "threshold": self.threshold,
            "last_trigger": self._last_trigger_time.isoformat() if self._last_trigger_time else None,
        }


class ObservationExtractor:
    """观察提取器"""

    def __init__(
        self,
        event_agent: EventGenerationAgent,
        data_store: PersonaDataStore,
    ):
        self.event_agent = event_agent
        self.data_store = data_store

    async def extract_observations(
        self,
        group_id: str,
        messages: List[BufferedMessage],
    ) -> List[Dict]:
        """
        从消息中提取观察

        Returns:
            观察记录列表
        """
        if not messages:
            return []

        try:
            # 构建消息文本
            messages_text = "\n".join(
                f"{msg.nickname}: {msg.content}"
                for msg in messages[-20:]  # 最多取最近20条
            )

            # 使用辅助模型提取观察
            observations = await self._call_llm_for_observations(messages_text)

            # 保存到数据库
            results = []
            for obs in observations[:3]:  # 最多保存3条
                participants = list(set(msg.user_id for msg in messages))
                who_names = {msg.user_id: msg.nickname for msg in messages}

                await self.data_store.add_observation(
                    group_id=group_id,
                    participants=participants,
                    who_names=who_names,
                    what=obs.get("what", ""),
                    why_remember=obs.get("why", ""),
                )
                results.append(obs)

            # 清理旧观察记录
            await self.data_store.prune_observations(
                group_id=group_id, keep=30
            )

            logger.info(f"从群 {group_id} 提取了 {len(results)} 条观察")
            return results

        except Exception as e:
            logger.exception(f"观察提取失败: {e}")
            return []

    async def _call_llm_for_observations(self, messages_text: str) -> List[Dict]:
        """
        调用 LLM 提取观察

        Returns:
            [{"what": ..., "why": ...}, ...]
        """
        from ..data.models import ModelTier

        # 构建提示
        system_prompt = """你是一个观察者。从以下群聊消息中提取1-3条有价值的观察。

要求:
1. 关注有趣、有意义或值得记住的内容
2. 记录谁参与了、发生了什么、为什么值得记住
3. 简洁具体，每条20-50字

输出格式（JSON）:
[
  {"what": "发生了什么", "why": "为什么值得记住"},
  ...
]

只输出JSON，不要其他内容。"""

        user_prompt = f"群聊消息:\n{messages_text}\n\n请提取观察:"

        try:
            # 使用 EventGenerationAgent 的 llm_router 进行提取
            if not self.event_agent or not self.event_agent.llm_router:
                logger.warning("LLM 路由器未初始化，跳过观察提取")
                return []

            response = await self.event_agent.llm_router.generate(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model_tier=ModelTier.AUXILIARY,
                temperature=0.7,
            )

            # 解析 JSON 响应
            import json
            try:
                observations = json.loads(response.strip())
                if isinstance(observations, list):
                    return observations
                elif isinstance(observations, dict):
                    return [observations]
                else:
                    logger.warning(f"LLM 返回了非列表/字典格式: {response[:100]}")
                    return []
            except json.JSONDecodeError as e:
                logger.warning(f"无法解析 LLM 响应为 JSON: {e}, 响应: {response[:200]}")
                # 尝试提取 JSON 部分
                import re
                json_match = re.search(r'\[.*?\]', response, re.DOTALL)
                if json_match:
                    try:
                        observations = json.loads(json_match.group(0))
                        if isinstance(observations, list):
                            return observations
                    except json.JSONDecodeError:
                        pass
                return []

        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            return []
