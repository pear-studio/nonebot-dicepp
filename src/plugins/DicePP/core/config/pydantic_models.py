"""
Pydantic models for DicePP configuration.

All bot configuration is represented as typed fields here.
Config is loaded hierarchically by ConfigLoader:
  global defaults < global secrets < persona < account overrides < env vars
"""
from typing import List
from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    enabled: bool = False
    api_key: str = ""
    base_url: str = "https://api.moonshot.cn/v1"
    model: str = "kimi-k2.5"
    personality: str = "你是一个 helpful 的助手，回答简洁。"
    max_context: int = 20
    timeout: int = 10


class MemoryMonitorConfig(BaseModel):
    enable: bool = False
    warn_percent: int = 80
    restart_percent: int = 90
    restart_mb: int = 2048


class DiceHubConfig(BaseModel):
    api_url: str = ""
    api_key: str = ""
    webchat_url: str = ""
    name: str = "未命名"


class RollConfig(BaseModel):
    enable: bool = True
    hide_enable: bool = True
    dnd_enable: bool = True
    coc_enable: bool = True


class DeckConfig(BaseModel):
    enable: bool = True
    data_path: str = "./decks"


class RandomGenConfig(BaseModel):
    enable: bool = True
    data_path: str = "./random"


class QueryConfig(BaseModel):
    enable: bool = True
    data_path: str = "./queries"
    private_database: str = "DND5E2014"


class LogConfig(BaseModel):
    upload_enable: bool = True
    upload_endpoint: str = "https://dice.weizaima.com/dice/api/log"
    upload_token: str = ""
    max_records: int = 5000


class ModeConfig(BaseModel):
    enable: bool = True
    default: str = "DND5E2024"


class BotConfig(BaseModel):
    """Top-level configuration model for a single Bot instance."""

    # Account/permissions
    master: List[str] = Field(default_factory=list)
    admin: List[str] = Field(default_factory=list)
    friend_token: List[str] = Field(default_factory=list)
    group_invite: bool = True
    nickname: str = ""
    persona: str = "default"

    # Agreement text (long, kept as str for direct use)
    agreement: str = ""

    # Command parsing
    command_split: str = "\\\\"

    # Data expiry
    data_expire: bool = False
    user_expire_day: int = 60
    group_expire_day: int = 14
    group_expire_warning_time: int = 1
    white_list_group: List[str] = Field(default_factory=list)
    white_list_user: List[str] = Field(default_factory=list)

    # Chat command
    chat_interval: int = 20

    # Bot activation
    bot_default_enable: bool = True

    # Subsystem configs
    llm: LLMConfig = Field(default_factory=LLMConfig)
    memory_monitor: MemoryMonitorConfig = Field(default_factory=MemoryMonitorConfig)
    dicehub: DiceHubConfig = Field(default_factory=DiceHubConfig)
    roll: RollConfig = Field(default_factory=RollConfig)
    deck: DeckConfig = Field(default_factory=DeckConfig)
    random_gen: RandomGenConfig = Field(default_factory=RandomGenConfig)
    query: QueryConfig = Field(default_factory=QueryConfig)
    log: LogConfig = Field(default_factory=LogConfig)
    mode: ModeConfig = Field(default_factory=ModeConfig)
