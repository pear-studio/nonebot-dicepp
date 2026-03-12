from typing import Any

from pydantic import BaseModel, Field


class Variable(BaseModel):
    user_id: str
    name: str
    value: Any = None
