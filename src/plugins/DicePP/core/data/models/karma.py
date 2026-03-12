from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class UserKarma(BaseModel):
    user_id: str
    group_id: str
    value: int = 0
    last_update: datetime = Field(default_factory=datetime.now)
