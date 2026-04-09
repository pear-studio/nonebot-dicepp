"""
Config key name constants and whitelist-preprocessing utility.

These string constants are kept for backward compatibility with any module
code that still references them.  The actual default values now live in
config/global.json and are loaded by ConfigLoader into BotConfig (pydantic_models.py).
"""
from typing import List

from core.config.declare import BOT_AGREEMENT  # noqa: F401 (re-exported for compat)

# ── Core / permissions ───────────────────────────────────────────────────────
CFG_MASTER = "master"
CFG_ADMIN = "admin"
CFG_FRIEND_TOKEN = "friend_token"
CFG_GROUP_INVITE = "group_invite"
CFG_AGREEMENT = "agreement"
CFG_COMMAND_SPLIT = "command_split"
CFG_DATA_EXPIRE = "data_expire"
CFG_USER_EXPIRE_DAY = "user_expire_day"
CFG_GROUP_EXPIRE_DAY = "group_expire_day"
CFG_GROUP_EXPIRE_WARNING = "group_expire_warning_time"
CFG_WHITE_LIST_GROUP = "white_list_group"
CFG_WHITE_LIST_USER = "white_list_user"

# ── Memory monitor ───────────────────────────────────────────────────────────
CFG_MEMORY_MONITOR_ENABLE = "memory_monitor_enable"
CFG_MEMORY_WARN_PERCENT = "memory_warn_percent"
CFG_MEMORY_RESTART_PERCENT = "memory_restart_percent"
CFG_MEMORY_RESTART_MB = "memory_restart_mb"

# ── DiceHub ──────────────────────────────────────────────────────────────────
CFG_HUB_API_URL = "dicehub_api_url"
CFG_HUB_API_KEY = "dicehub_api_key"
CFG_HUB_NAME = "dicehub_name"
CFG_HUB_ENABLE = "dicehub_enable"


def preprocess_white_list(raw_list: List[str]) -> List[str]:
    """Split semicolon-separated whitelist entries and strip whitespace."""
    result: List[str] = []
    for raw_str in raw_list:
        for item in raw_str.split(";"):
            if item.strip():
                result.append(item.strip())
    return result
