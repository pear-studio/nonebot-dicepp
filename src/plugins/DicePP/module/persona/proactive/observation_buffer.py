"""
群聊观察缓冲系统

实现消息缓冲、动态阈值、观察提取。

动态阈值（`_adjust_threshold`）：
- 每次触发提取后会记录时间；若因「缓冲条数 ≥ 阈值」快速触发，且距上次提取不足 30 分钟，
  则将阈值 +10（上限为 `max_threshold`），避免群聊刷屏时过于频繁调用 LLM。
- 若因「缓冲超过 2 小时」慢速触发，且距上次提取超过 3 小时，则将阈值 −5（下限为 `min_threshold`），
  避免冷清群迟迟达不到阈值。
- 阈值因此会在 `min_threshold`～`max_threshold` 之间浮动；爆发期可能接近上限，需调参时改构造参数。
"""
import re
from dataclasses import dataclass
from typing import List, Dict, Optional, Set, Any
from datetime import datetime, timedelta
import logging

from ..agents.event_agent import EventGenerationAgent
from ..data.store import PersonaDataStore

logger = logging.getLogger("persona.observation")


@dataclass
class DynamicThresholdConfig:
    """动态阈值配置"""

    fast_trigger_window_minutes: int = 30
    slow_trigger_window_hours: int = 3
    threshold_increase: int = 10
    threshold_decrease: int = 5
    min_messages_for_timeout: int = 5
    timeout_check_hours: int = 2


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
        *,
        timezone: str = "",
        dynamic_threshold_config: Optional[DynamicThresholdConfig] = None,
    ):
        self.group_id = group_id
        self._timezone = (timezone or "").strip()
        self.threshold = initial_threshold
        self.max_threshold = max_threshold
        self.min_threshold = min_threshold
        self.max_buffer_size = max_buffer_size
        self.max_records_per_group = max_records_per_group
        self._dynamic_threshold_config = dynamic_threshold_config or DynamicThresholdConfig()

        self._buffer: List[BufferedMessage] = []
        self._last_trigger_time: Optional[datetime] = None

    def _wall_now(self) -> datetime:
        from ..wall_clock import persona_wall_now

        return persona_wall_now(self._timezone)

    def _is_pure_emoji(self, content: str) -> bool:
        """
        检查内容是否为纯 emoji

        Returns:
            True = 纯 emoji
        """
        if not content:
            return False

        # 使用 emoji 库如果可用
        try:
            import emoji

            return all(emoji.is_emoji(c) or c.isspace() for c in content)
        except ImportError:
            # 回退到正则：匹配 emoji 范围
            # 只匹配明确的 emoji 字符，不包括中文或其他文字
            emoji_pattern = re.compile(
                "["
                "\U0001F600-\U0001F64F"  # 表情符号
                "\U0001F300-\U0001F5FF"  # 符号和象形文字
                "\U0001F680-\U0001F6FF"  # 交通和地图符号
                "\U0001F1E0-\U0001F1FF"  # 国旗
                "\U00002702-\U000027B0"  # 其他符号
                "\U0001F900-\U0001F9FF"  # 补充符号
                "\U0001FA00-\U0001FA6F"  # 象棋符号等
                "\U00002600-\U000026FF"  # 杂项符号
                "]+",
                re.UNICODE,
            )
            # 移除所有 emoji 后检查是否为空（保留空格）
            remaining = emoji_pattern.sub("", content).strip()
            return len(remaining) == 0

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

        # 纯 emoji（使用 emoji 库或更精确的正则）
        # 注意：不使用 \U000024C2-\U0001F251 因为它包含中文字符
        if self._is_pure_emoji(content):
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
            timestamp=self._wall_now(),
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

        # 检查时间（超过配置的超时时间）
        if self._buffer:
            first_msg_time = self._buffer[0].timestamp
            timeout_hours = self._dynamic_threshold_config.timeout_check_hours
            if self._wall_now() - first_msg_time > timedelta(hours=timeout_hours):
                min_msgs = self._dynamic_threshold_config.min_messages_for_timeout
                if len(self._buffer) >= min_msgs:
                    self._adjust_threshold(fast=False)
                    return True

        return False

    def _adjust_threshold(self, fast: bool) -> None:
        """
        按上次触发间隔调整阈值（见模块文档）。

        Args:
            fast: True 表示本次因条数达标触发；False 表示因超时触发。
        """
        now = self._wall_now()
        cfg = self._dynamic_threshold_config

        if self._last_trigger_time:
            time_since_last = now - self._last_trigger_time

            fast_window = timedelta(minutes=cfg.fast_trigger_window_minutes)
            slow_window = timedelta(hours=cfg.slow_trigger_window_hours)

            if fast and time_since_last < fast_window:
                # 快速触发，增加阈值
                self.threshold = min(self.max_threshold, self.threshold + cfg.threshold_increase)
            elif not fast and time_since_last > slow_window:
                # 慢速触发，减少阈值
                self.threshold = max(self.min_threshold, self.threshold - cfg.threshold_decrease)

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

    def to_persist_dict(self) -> Dict[str, Any]:
        """序列化到 persona_settings（跨重启恢复缓冲与动态阈值）。"""
        return {
            "threshold": self.threshold,
            "last_trigger": self._last_trigger_time.isoformat() if self._last_trigger_time else None,
            "messages": [
                {
                    "user_id": m.user_id,
                    "nickname": m.nickname,
                    "content": m.content,
                    "ts": m.timestamp.isoformat(),
                }
                for m in self._buffer
            ],
        }

    @classmethod
    def from_persist_dict(
        cls,
        group_id: str,
        data: Dict[str, Any],
        *,
        initial_threshold: int,
        max_threshold: int,
        min_threshold: int,
        max_buffer_size: int,
        max_records_per_group: int,
        timezone: str = "",
        dynamic_threshold_config: Optional[DynamicThresholdConfig] = None,
    ) -> "ObservationBuffer":
        buf = cls(
            group_id=group_id,
            initial_threshold=initial_threshold,
            max_threshold=max_threshold,
            min_threshold=min_threshold,
            max_buffer_size=max_buffer_size,
            max_records_per_group=max_records_per_group,
            timezone=timezone,
            dynamic_threshold_config=dynamic_threshold_config,
        )
        buf.threshold = int(data.get("threshold", initial_threshold))
        lt = data.get("last_trigger")
        try:
            buf._last_trigger_time = datetime.fromisoformat(lt) if lt else None
        except (TypeError, ValueError):
            buf._last_trigger_time = None
        for m in data.get("messages") or []:
            try:
                buf._buffer.append(
                    BufferedMessage(
                        user_id=m["user_id"],
                        nickname=m.get("nickname", ""),
                        content=m.get("content", ""),
                        timestamp=datetime.fromisoformat(m["ts"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        if len(buf._buffer) > buf.max_buffer_size:
            buf._buffer = buf._buffer[-buf.max_buffer_size :]
        return buf


class ObservationExtractor:
    """观察提取器"""

    def __init__(
        self,
        event_agent: EventGenerationAgent,
        data_store: PersonaDataStore,
        config,
        *,
        prune_observations_keep: int = 30,
    ):
        self.event_agent = event_agent
        self.data_store = data_store
        self.config = config
        self._prune_observations_keep = prune_observations_keep

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

        logger.debug(
            f"观察提取触发: group={group_id}, messages={len(messages)}"
        )

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

                raw_digest = messages_text[:200]
                if self.config.observation_store_raw_digest:
                    digest_value = raw_digest
                else:
                    import hashlib
                    digest_value = hashlib.sha256(raw_digest.encode("utf-8")).hexdigest()[:32]

                await self.data_store.add_observation(
                    group_id=group_id,
                    participants=participants,
                    who_names=who_names,
                    what=obs.get("what", ""),
                    why_remember=obs.get("why", ""),
                    source_messages_count=len(messages),
                    extract_prompt_digest=digest_value,
                )
                results.append(obs)

            # 清理旧观察记录
            await self.data_store.prune_observations(
                group_id=group_id, keep=self._prune_observations_keep
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

            # 解析 JSON 响应（3 级容错，与 scoring_agent 对齐）
            import json
            import re

            # 尝试 1：直接解析
            try:
                observations = json.loads(response.strip())
                if isinstance(observations, list):
                    return observations
                elif isinstance(observations, dict):
                    return [observations]
                else:
                    logger.warning(f"LLM 返回了非列表/字典格式: {response[:100]}")
                    return []
            except json.JSONDecodeError:
                pass

            # 尝试 2：去除 markdown 围栏后解析
            try:
                cleaned = re.sub(r'```json\s*|\s*```', '', response, flags=re.DOTALL)
                cleaned = re.sub(r'^[\s\n]*json\s*', '', cleaned, flags=re.DOTALL)
                cleaned = cleaned.strip()
                observations = json.loads(cleaned)
                if isinstance(observations, list):
                    return observations
                elif isinstance(observations, dict):
                    return [observations]
            except json.JSONDecodeError:
                pass

            # 尝试 3：括号计数提取第一个完整 JSON 对象/数组
            try:
                for start_char, end_char in [('[', ']'), ('{', '}')]:
                    start = response.find(start_char)
                    if start >= 0:
                        depth = 0
                        in_string = False
                        escape = False
                        for i in range(start, len(response)):
                            ch = response[i]
                            if escape:
                                escape = False
                                continue
                            if ch == '\\':
                                escape = True
                                continue
                            if ch == '"':
                                in_string = not in_string
                                continue
                            if not in_string:
                                if ch == start_char:
                                    depth += 1
                                elif ch == end_char:
                                    depth -= 1
                                    if depth == 0:
                                        observations = json.loads(response[start:i+1])
                                        if isinstance(observations, list):
                                            return observations
                                        elif isinstance(observations, dict):
                                            return [observations]
                                        break
            except json.JSONDecodeError:
                pass

            logger.warning(f"无法解析 LLM 响应为 JSON, 响应: {response[:200]}")
            return []

        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            return []
