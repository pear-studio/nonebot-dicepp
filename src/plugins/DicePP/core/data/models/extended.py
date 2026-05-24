from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


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


class ChatRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    group_id: str
    user_id: str
    nickname: str = ""
    content: str
    time: datetime = Field(default_factory=datetime.now)
    source: str = "unknown"
    message_id: Optional[str] = None


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


class UserVariable(BaseModel):
    user_id: str
    group_id: str
    name: str
    val: int = 0


class UserFavor(BaseModel):
    user_id: str
    group_id: str
    favor_value: int = 0
    last_update: datetime = Field(default_factory=datetime.now)


class UserMacro(BaseModel):
    """用户自定义宏（指令文本替换）

    一个 user_id + key 对应一条宏。key 是宏的关键字，target 是替换目标。
    args 描述宏的参数列表（用括号 "(a,b)" 在 raw 里声明），命中时按位置替换。
    """
    user_id: str
    key: str
    raw: str = ""             # 用户原始定义字符串
    args: list[str] = Field(default_factory=list)
    target: str = ""          # 替换后的目标字符串（含 {arg} 占位）
    command_split: str = ""   # bot 当前的指令分隔符（用于把 %% 替换回去）


class GroupTeam(BaseModel):
    """群级玩家队伍

    跑团群里区分玩家（PC）和观众（OB）。team 内的用户保留原名；
    team 外的用户被自动改群名片为 "ob"（需要骰娘有群管理员权限）。
    触发时机：.team set 时全量刷新；新成员进群时单独改一次。
    """
    group_id: str
    members: list[str] = Field(default_factory=list)
    auto_rename_ob: bool = True
    last_update: datetime = Field(default_factory=datetime.now)


class GroupMacro(BaseModel):
    """群级宏

    跟 UserMacro 类似但作用域为整个群。主持人 `.hb 宏 X = Y` 后，
    群里所有成员的消息都会被 X→Y 替换。在 process_message 中群宏
    先于用户宏执行（让群宏铺路，用户宏在此基础上进一步定制）。
    """
    group_id: str
    key: str
    raw: str = ""
    args: list[str] = Field(default_factory=list)
    target: str = ""
    command_split: str = ""
    creator_id: str = ""   # 审计：哪位主持人创建的
    last_update: datetime = Field(default_factory=datetime.now)
