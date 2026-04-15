"""
角色卡模型

兼容 SillyTavern V2 标准的角色卡定义
"""
import logging
import random
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger("persona.character")


class SharePolicy(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    NEVER = "never"


class ScheduledEventConfig(BaseModel):
    type: str
    time_range: str
    share: SharePolicy = SharePolicy.OPTIONAL


class PersonaExtensions(BaseModel):
    initial_relationship: int = 30
    warmth_labels: List[str] = Field(default_factory=list)
    world: str = ""
    daily_events_count: int = 5
    event_day_start_hour: int = 8
    event_day_end_hour: int = 22
    event_jitter_minutes: int = 60
    scheduled_events: List[ScheduledEventConfig] = Field(default_factory=list)
    # Phase 3: 好感度低时的拒绝回复语（可选，不配置则使用系统默认）
    # 语义说明：
    # - None（或未配置）：使用系统默认拒绝语
    # - []（空列表）：明确不拒绝（即使好感度低也正常回复）
    # - ["...", "..."]（非空列表）：使用自定义拒绝语
    # 注意：是否启用拒绝机制由全局配置 `relationship_refuse_enabled` 控制
    refuse_messages: Optional[List[str]] = Field(default=None)

    def generate_event_times(self, count: Optional[int] = None) -> List[int]:
        n = count if count is not None else self.daily_events_count
        if n <= 0:
            return []
        start = self.event_day_start_hour * 60
        end = self.event_day_end_hour * 60
        interval = (end - start) / n
        result = []
        for i in range(n):
            base = start + int(i * interval + interval / 2)
            jitter = random.randint(-self.event_jitter_minutes, self.event_jitter_minutes)
            result.append(max(start, min(end - 1, base + jitter)))
        return sorted(result)


class LoreEntry(BaseModel):
    keys: List[str]
    content: str
    enabled: bool = True
    selective: bool = False
    secondary_keys: List[str] = Field(default_factory=list)
    order: int = 100  # 优先级，数值越高越优先注入（与 SillyTavern 对齐）
    exact_match: bool = False  # 是否要求整词匹配（减少短 key 子串误触）
    min_match_length: Optional[int] = None  # 子串匹配时的最小 key 长度限制


class CharacterBook(BaseModel):
    entries: List[LoreEntry] = Field(default_factory=list)


class Character(BaseModel):
    name: str
    description: str = ""
    personality: str = ""
    scenario: str = ""
    first_mes: str = ""
    mes_example: str = ""
    system_prompt: str = ""
    character_book: Optional[CharacterBook] = None
    extensions: PersonaExtensions = Field(default_factory=PersonaExtensions)

    @staticmethod
    def _key_matches(key: str, scanned: str, exact_match: bool, min_match_length: Optional[int]) -> bool:
        """判断单个 key 是否命中扫描文本"""
        if min_match_length is not None and len(key) < min_match_length:
            return False
        if exact_match:
            import re
            escaped = re.escape(key)
            # 只要求不被英文单词包裹（即前后不能是 [A-Za-z0-9_]），
            # 避免如 "cat" 在 "category" 中因子串而误触
            pattern = rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])"
            return bool(re.search(pattern, scanned))
        return key in scanned

    def search_lore_entries(self, texts: List[str]) -> List[LoreEntry]:
        """扫描文本并返回命中的 LoreEntry 列表

        命中规则：
        - 至少有一个 key 出现在拼接后的文本中（子串匹配，受 exact_match/min_match_length 控制）
        - selective=True 时，还需至少一个 secondary_keys 命中
        """
        if not self.character_book:
            return []

        scanned = "\n".join(texts)
        matched: List[LoreEntry] = []

        for entry in self.character_book.entries:
            if not entry.enabled:
                continue
            key_hit = any(
                self._key_matches(k, scanned, entry.exact_match, entry.min_match_length)
                for k in entry.keys
            )
            if not key_hit:
                continue
            if entry.selective:
                if not entry.secondary_keys:
                    logger.debug(
                        "LoreEntry keys=%s has selective=True but empty secondary_keys, skipping",
                        entry.keys,
                    )
                    continue
                if not any(sk in scanned for sk in entry.secondary_keys):
                    continue
            matched.append(entry)

        return matched

    def get_warmth_labels(self) -> List[str]:
        labels = self.extensions.warmth_labels
        defaults = ["厌倦", "冷淡", "疏远", "友好", "亲近", "亲密"]
        result = []
        for i in range(6):
            if i < len(labels) and labels[i]:
                result.append(labels[i])
            else:
                result.append(defaults[i])
        return result

    def format_mes_example(self, user_name: str = "用户") -> str:
        return self.mes_example.replace("{{user}}", user_name).replace("{{char}}", self.name)
