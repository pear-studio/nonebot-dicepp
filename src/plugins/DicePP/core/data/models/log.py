from datetime import datetime

from pydantic import BaseModel


class LogGroupState(BaseModel):
    group_id: str
    current_log_id: str | None = None
    updated_at: datetime


class LogSession(BaseModel):
    id: str
    group_id: str
    name: str
    recording: bool = False
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None = None
    record_begin_at: datetime | None = None
    last_warn_at: datetime | None = None


class LogSessionSummary(BaseModel):
    session: LogSession
    record_count: int
    latest_export_at: datetime | None = None


class LogRecord(BaseModel):
    id: int | None = None
    log_id: str
    time: datetime
    user_id: str
    nickname: str = ""
    source: str
    message_type: str
    plain_content: str
    raw_content: str
    segments_json: str | None = None
    message_id: str | None = None
    recalled_at: datetime | None = None


class LogExport(BaseModel):
    id: int | None = None
    request_id: str
    log_id: str
    format: str
    view: str
    record_upper_id: int | None = None
    created_at: datetime
    local_path: str | None = None
    group_file_name: str | None = None
    generation_status: str
    delivery_status: str
    note: str | None = None


class LogPublication(BaseModel):
    id: int | None = None
    request_id: str
    log_id: str
    provider: str
    view: str
    record_upper_id: int | None = None
    created_at: datetime
    published_at: datetime | None = None
    url: str | None = None
    status: str
    note: str | None = None
