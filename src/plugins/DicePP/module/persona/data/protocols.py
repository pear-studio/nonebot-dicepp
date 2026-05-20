"""Persona 数据存储窄接口

每个 Protocol 对应一张或一组紧密关联的数据表。
PersonaDataStore 已实现全部方法，隐式满足所有 Protocol。
"""

from typing import List, Optional, Protocol
from datetime import datetime

from .models import (
    DailyEvent,
    MessageType,
    RelationshipState,
    ScoreEvent,
    ScoringFailure,
    UnifiedMessage,
    UserProfile,
)


class MessageStore(Protocol):
    """message_stream 表 CRUD + 搜索"""

    async def add_message_stream(
        self,
        user_id: str,
        group_id: str,
        role: str,
        type: MessageType,
        content: str,
        display_name: str = "",
    ) -> int: ...

    async def get_recent_messages(
        self,
        user_id: str,
        group_id: str = "",
        limit: int = 20,
    ) -> List[UnifiedMessage]: ...

    async def get_group_messages(
        self,
        group_id: str,
        limit: Optional[int] = 50,
    ) -> List[UnifiedMessage]: ...

    async def get_earliest_message_time(
        self, user_id: str, group_id: str = ""
    ) -> Optional[datetime]: ...

    async def count_messages(
        self, user_id: str, group_id: str = ""
    ) -> int: ...

    async def search_messages(
        self,
        group_id: str,
        *,
        keyword: Optional[str] = None,
        type: Optional[MessageType] = None,
        user_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        hours_back: Optional[int] = None,
        limit: int = 5,
    ) -> List[UnifiedMessage]: ...

    async def clear_messages(self, user_id: str, group_id: str) -> None: ...


class RelationshipStore(Protocol):
    """persona_user_relationships + persona_score_history + persona_scoring_failures"""

    async def get_relationship(
        self, user_id: str
    ) -> Optional[RelationshipState]: ...

    async def init_relationship(
        self, user_id: str, initial_score: float = 40.0
    ) -> RelationshipState: ...

    async def update_relationship(self, rel: RelationshipState) -> None: ...

    async def list_all_relationships_raw(self) -> List[RelationshipState]: ...

    async def list_active_relationships(
        self, min_score: float = 0, active_within_days: int = 30
    ) -> List[RelationshipState]: ...

    async def get_top_relationships(
        self, limit: int = 10
    ) -> List[RelationshipState]: ...

    async def add_score_event(self, event: ScoreEvent) -> None: ...

    async def get_recent_score_events(
        self, user_id: str, limit: int = 2
    ) -> List[ScoreEvent]: ...

    async def record_scoring_failure(self, failure: ScoringFailure) -> None: ...

    async def get_recent_scoring_failures(
        self, user_id: str, group_id: Optional[str] = None, limit: int = 10
    ) -> List[ScoringFailure]: ...


class ProfileStore(Protocol):
    """persona_user_profiles 表"""

    async def get_user_profile(
        self, user_id: str
    ) -> Optional[UserProfile]: ...

    async def save_user_profile(self, profile: UserProfile) -> None: ...


class EventStore(Protocol):
    """persona_daily_events + persona_diary"""

    async def get_daily_events(self, date: str) -> List[DailyEvent]: ...

    async def add_daily_event(
        self,
        date: str,
        event_type: str,
        description: str,
        reaction: str = "",
        share_desire: float = 0.0,
        duration_minutes: int = 0,
        system_prompt_digest: str = "",
        raw_response: str = "",
        energy_delta: Optional[int] = None,
        mood_delta: Optional[int] = None,
        health_delta: Optional[int] = None,
        context_summary: str = "",
    ) -> None: ...

    async def get_diary(self, date: str) -> Optional[str]: ...

    async def save_diary(self, date: str, content: str) -> None: ...
