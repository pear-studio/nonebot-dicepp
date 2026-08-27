from datetime import datetime

from pydantic import BaseModel, Field


class UserNickname(BaseModel):
    user_id: str
    group_id: str
    nickname: str = ""


class UserPoint(BaseModel):
    user_id: str
    cur_point: int = 0
    today_point: int = 0
    last_update: datetime = Field(default_factory=datetime.now)


class GroupConfig(BaseModel):
    group_id: str
    data: dict = Field(default_factory=dict)


class GroupActivate(BaseModel):
    group_id: str
    active: bool = True
    last_update: datetime = Field(default_factory=datetime.now)


class GroupWelcome(BaseModel):
    group_id: str
    welcome_msg: str = ""
    welcome_enabled: bool = False
    last_update: datetime = Field(default_factory=datetime.now)


class BotControl(BaseModel):
    key: str
    value: str = ""


class UserStat(BaseModel):
    user_id: str
    data: str = ""


class GroupStat(BaseModel):
    group_id: str
    data: str = ""


class MetaStat(BaseModel):
    key: str = "meta"
    data: str = ""


class NPCHealth(BaseModel):
    group_id: str
    name: str
    hp_data: str = ""

