"""
数据库迁移脚本

创建 Persona 模块所需的表。

.. note::
    新列/新表：除本文件 ``ALL_MIGRATIONS`` 中的 ``CREATE`` 外，若需兼容**已存在**的 SQLite 文件，
    往往还要在 ``PersonaDataStore._apply_runtime_schema_patches`` 里做条件 ``ALTER``。
    两处须同步维护（store 模块顶部另有交叉引用注释）。
"""

# 对话历史表
CREATE_MESSAGES_TABLE = """
CREATE TABLE IF NOT EXISTS persona_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    group_id TEXT DEFAULT '',
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# 索引
CREATE_MESSAGES_INDEX = """
CREATE INDEX IF NOT EXISTS idx_persona_messages_user_group 
ON persona_messages(user_id, group_id, created_at DESC);
"""

# 白名单表
CREATE_WHITELIST_TABLE = """
CREATE TABLE IF NOT EXISTS persona_whitelist (
    id TEXT NOT NULL,
    type TEXT NOT NULL,
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id, type)
);
"""

# 设置表（存储口令等运行时配置）
CREATE_SETTINGS_TABLE = """
CREATE TABLE IF NOT EXISTS persona_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

# 评分历史表
CREATE_SCORE_HISTORY_TABLE = """
CREATE TABLE IF NOT EXISTS persona_score_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    group_id TEXT DEFAULT '',
    intimacy_delta REAL DEFAULT 0,
    passion_delta REAL DEFAULT 0,
    trust_delta REAL DEFAULT 0,
    secureness_delta REAL DEFAULT 0,
    composite_before REAL,
    composite_after REAL,
    reason TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# 每日用量表
CREATE_USAGE_TABLE = """
CREATE TABLE IF NOT EXISTS persona_usage (
    user_id TEXT NOT NULL,
    date TEXT NOT NULL,
    count INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, date)
);
"""

# 群聊观察表
CREATE_OBSERVATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS persona_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id TEXT NOT NULL,
    participants TEXT NOT NULL,  -- JSON list of user_ids
    who_names TEXT NOT NULL,     -- JSON dict of user_id -> nickname
    what TEXT NOT NULL,
    why_remember TEXT NOT NULL,
    observed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# 角色日记表
CREATE_DIARY_TABLE = """
CREATE TABLE IF NOT EXISTS persona_diary (
    date TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# 每日事件表
CREATE_DAILY_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS persona_daily_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    event_type TEXT NOT NULL,
    description TEXT NOT NULL,
    reaction TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# 角色永久状态表
CREATE_CHARACTER_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS persona_character_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    text TEXT DEFAULT '',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_USER_PROFILES_TABLE = """
CREATE TABLE IF NOT EXISTS persona_user_profiles (
    user_id TEXT PRIMARY KEY,
    facts TEXT DEFAULT '{}',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_USER_RELATIONSHIPS_TABLE = """
CREATE TABLE IF NOT EXISTS persona_user_relationships (
    user_id TEXT NOT NULL,
    group_id TEXT DEFAULT '',
    intimacy REAL DEFAULT 30.0,
    passion REAL DEFAULT 30.0,
    trust REAL DEFAULT 30.0,
    secureness REAL DEFAULT 30.0,
    last_interaction_at TIMESTAMP,
    last_relationship_decay_applied_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, group_id)
);
"""

# 群活跃度表
CREATE_GROUP_ACTIVITY_TABLE = """
CREATE TABLE IF NOT EXISTS persona_group_activity (
    group_id TEXT PRIMARY KEY,
    score REAL DEFAULT 50.0,
    last_interaction_at TIMESTAMP,    -- 最后互动时间（@bot/AI回复）
    last_content_at TIMESTAMP,        -- 最后内容时间（群聊观察触发）
    content_count_today INTEGER DEFAULT 0,  -- 今日内容计数
    daily_add_date TEXT,              -- 当日累计加分日期 (YYYY-MM-DD)
    daily_add_total REAL DEFAULT 0    -- 当日累计加分值
);
"""

# 用户主动消息静音表 (Phase 3)
CREATE_USER_MUTE_TABLE = """
CREATE TABLE IF NOT EXISTS persona_user_mute (
    user_id TEXT PRIMARY KEY,
    muted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason TEXT DEFAULT ''
);
"""

# 用户 LLM 配置表 (Phase 4)
CREATE_USER_LLM_CONFIG_TABLE = """
CREATE TABLE IF NOT EXISTS persona_user_llm_config (
    user_id TEXT PRIMARY KEY,
    primary_api_key_encrypted TEXT DEFAULT '',
    primary_base_url TEXT DEFAULT '',
    primary_model TEXT DEFAULT '',
    auxiliary_api_key_encrypted TEXT DEFAULT '',
    auxiliary_base_url TEXT DEFAULT '',
    auxiliary_model TEXT DEFAULT '',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

ALL_MIGRATIONS = [
    CREATE_MESSAGES_TABLE,
    CREATE_MESSAGES_INDEX,
    CREATE_WHITELIST_TABLE,
    CREATE_SETTINGS_TABLE,
    CREATE_SCORE_HISTORY_TABLE,
    CREATE_USAGE_TABLE,
    CREATE_OBSERVATIONS_TABLE,
    CREATE_DIARY_TABLE,
    CREATE_DAILY_EVENTS_TABLE,
    CREATE_CHARACTER_STATE_TABLE,
    CREATE_USER_PROFILES_TABLE,
    CREATE_USER_RELATIONSHIPS_TABLE,
    CREATE_GROUP_ACTIVITY_TABLE,
    CREATE_USER_MUTE_TABLE,
    CREATE_USER_LLM_CONFIG_TABLE,
]
