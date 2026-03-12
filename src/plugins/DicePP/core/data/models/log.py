from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class LogSession(BaseModel):
    id: str
    group_id: str
    name: str
    recording: bool = False
    created_at: datetime
    updated_at: datetime
    record_begin_at: str = ""
    last_warn: str = ""
    filter_outside: bool = False
    filter_command: bool = False
    filter_bot: bool = False
    filter_media: bool = False
    filter_forum_code: bool = False
    upload_time: Optional[str] = None
    upload_file: Optional[str] = None
    upload_note: Optional[str] = None
    url: Optional[str] = None


class LogRecord(BaseModel):
    id: Optional[int] = None
    log_id: str
    time: datetime
    user_id: str
    nickname: str = ""
    content: str
    source: str
    message_id: Optional[str] = None
