from pydantic import BaseModel


class HubConfig(BaseModel):
    key: str
    value: str = ""

