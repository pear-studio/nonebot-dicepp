from pydantic import BaseModel


class Macro(BaseModel):
    user_id: str
    name: str
    content: str
