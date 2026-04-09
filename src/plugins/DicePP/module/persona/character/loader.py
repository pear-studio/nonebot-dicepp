"""
角色卡加载器

从 YAML 文件加载角色卡
"""
import logging
from pathlib import Path
from typing import Optional
import yaml

from .models import Character, CharacterBook, LoreEntry, PersonaExtensions, ScheduledEventConfig

logger = logging.getLogger("persona.character")


class CharacterLoader:
    """角色卡加载器"""

    def __init__(self, character_path: str):
        self.character_path = Path(character_path)

    def load(self, character_name: str) -> Optional[Character]:
        """
        加载指定名称的角色卡
        
        Args:
            character_name: 角色卡名称（不含扩展名）
            
        Returns:
            Character 对象，加载失败返回 None
        """
        file_path = self.character_path / f"{character_name}.yaml"
        
        if not file_path.exists():
            return None
        
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            
            if not data:
                return None
            
            return self._parse_character(data)
        except Exception as e:
            logger.exception(f"加载角色卡失败: {e}")
            return None

    def _parse_character(self, data: dict) -> Character:
        """解析角色卡数据"""
        # 解析 extensions.persona
        extensions_data = data.get("extensions", {})
        persona_data = extensions_data.get("persona", {})
        
        # 解析 scheduled_events
        scheduled_events = []
        for event in persona_data.get("scheduled_events", []):
            scheduled_events.append(ScheduledEventConfig(
                type=event.get("type", ""),
                time_range=event.get("time_range", "")
            ))
        
        extensions = PersonaExtensions(
            initial_relationship=persona_data.get("initial_relationship", 30),
            warmth_labels=persona_data.get("warmth_labels", []),
            world=persona_data.get("world", ""),
            daily_events_count=persona_data.get("daily_events_count", 5),
            event_day_start_hour=persona_data.get("event_day_start_hour", 8),
            event_day_end_hour=persona_data.get("event_day_end_hour", 22),
            event_jitter_minutes=persona_data.get("event_jitter_minutes", 60),
            scheduled_events=scheduled_events
        )
        
        # 解析 character_book
        character_book = None
        book_data = data.get("character_book")
        if book_data:
            entries = []
            for entry in book_data.get("entries", []):
                entries.append(LoreEntry(
                    keys=entry.get("keys", []),
                    content=entry.get("content", ""),
                    enabled=entry.get("enabled", True),
                    selective=entry.get("selective", False),
                    secondary_keys=entry.get("secondary_keys", [])
                ))
            character_book = CharacterBook(entries=entries)
        
        return Character(
            name=data.get("name", "未命名"),
            description=data.get("description", ""),
            personality=data.get("personality", ""),
            scenario=data.get("scenario", ""),
            first_mes=data.get("first_mes", ""),
            mes_example=data.get("mes_example", ""),
            system_prompt=data.get("system_prompt", ""),
            character_book=character_book,
            extensions=extensions
        )

    def list_characters(self) -> list[str]:
        """列出所有可用的角色卡名称"""
        if not self.character_path.exists():
            return []
        
        characters = []
        for file_path in self.character_path.glob("*.yaml"):
            characters.append(file_path.stem)
        return characters
