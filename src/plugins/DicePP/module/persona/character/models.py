"""
角色卡模型

兼容 SillyTavern V2 标准的角色卡定义
"""
from typing import List, Optional
from pydantic import BaseModel, Field
import random


class ScheduledEventConfig(BaseModel):
    type: str
    time_range: str


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
