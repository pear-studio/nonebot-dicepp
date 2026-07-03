"""
Latest schema SQL fragments for Persona-owned SQLite targets.

These constants are not a legacy runtime migration chain. New Persona DBs are
created directly from the latest schema through SchemaTarget lifecycle.
Forward migrations for future versions belong on SchemaTarget.migrations or
SchemaTarget.async_migrations.
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
    reputation_delta REAL DEFAULT 0,
    familiarity_delta REAL DEFAULT 0,
    composite_before REAL,
    composite_after REAL,
    reason TEXT DEFAULT '',
    conversation_digest TEXT DEFAULT '',
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
    duration_minutes INTEGER DEFAULT 0,
    system_prompt_digest TEXT DEFAULT '',
    raw_response TEXT DEFAULT '',
    energy_delta INTEGER,
    mood_delta INTEGER,
    health_delta INTEGER,
    context_summary TEXT DEFAULT '',
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
    user_id TEXT PRIMARY KEY,
    familiarity REAL DEFAULT 0.0,
    peak_familiarity REAL DEFAULT 0.0,
    intimacy REAL DEFAULT 0.0,
    peak_intimacy REAL DEFAULT 0.0,
    reputation REAL DEFAULT 100.0,
    last_interaction_at TIMESTAMP,
    last_reputation_recovery_date TIMESTAMP,
    last_relationship_decay_applied_at TIMESTAMP,
    last_miss_sent_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# 评分失败记录表
CREATE_SCORING_FAILURES_TABLE = """
CREATE TABLE IF NOT EXISTS persona_scoring_failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    group_id TEXT DEFAULT '',
    messages_count INTEGER DEFAULT 0,
    error TEXT DEFAULT '',
    raw_response TEXT DEFAULT '',
    conversation_digest TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_SCORING_FAILURES_INDEX = """
CREATE INDEX IF NOT EXISTS idx_persona_scoring_failures_user ON persona_scoring_failures(user_id, group_id, created_at DESC);
"""

CREATE_SCORING_FAILURES_INDEX_CREATED_AT = """
CREATE INDEX IF NOT EXISTS idx_persona_scoring_failures_created_at ON persona_scoring_failures(created_at);
"""

# 群活跃度表
CREATE_GROUP_ACTIVITY_TABLE = """
CREATE TABLE IF NOT EXISTS persona_group_activity (
    group_id TEXT PRIMARY KEY,
    score REAL DEFAULT 50.0,
    last_interaction_at TIMESTAMP,    -- 最后互动时间（@bot/AI回复）
    daily_add_date TEXT,              -- 当日累计加分日期 (YYYY-MM-DD)
    daily_add_total REAL DEFAULT 0    -- 当日累计加分值
);
"""

# familiarity 每日累计表（持久化日上限，防重启丢失）
CREATE_FAMILIARITY_DAILY_TABLE = """
CREATE TABLE IF NOT EXISTS persona_familiarity_daily (
    user_id TEXT NOT NULL,
    date TEXT NOT NULL,
    total REAL DEFAULT 0.0,
    PRIMARY KEY (user_id, date)
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

# 统一消息流表 (替代 persona_messages + persona_group_conversations)
CREATE_MESSAGE_STREAM_TABLE = """
CREATE TABLE IF NOT EXISTS message_stream (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT NOT NULL,
    group_id      TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL,
    type          TEXT NOT NULL DEFAULT 'chat',
    content       TEXT NOT NULL,
    display_name  TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    agent_run_id  TEXT DEFAULT '',
    turn_id       TEXT DEFAULT '',
    segment_index INTEGER DEFAULT -1,
    segment_phase TEXT DEFAULT '',
    image_meta    TEXT DEFAULT ''
);
"""

CREATE_MESSAGE_STREAM_USER_INDEX = """
CREATE INDEX IF NOT EXISTS idx_msgstream_user_time
ON message_stream(user_id, created_at DESC);
"""

CREATE_MESSAGE_STREAM_GROUP_INDEX = """
CREATE INDEX IF NOT EXISTS idx_msgstream_group_time
ON message_stream(group_id, created_at DESC);
"""

# 用户 LLM 配置表 (Phase 4) — core_db 侧
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

# 全局设置表 — core_db 侧（存 bot 级设置，如口令 "code"）
CREATE_GLOBAL_SETTINGS_TABLE = """
CREATE TABLE IF NOT EXISTS persona_global_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

# LLM Trace 表 (Phase 7a)
CREATE_LLM_TRACES_TABLE = """
CREATE TABLE IF NOT EXISTS persona_llm_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    user_id TEXT DEFAULT '',
    group_id TEXT DEFAULT '',
    run_id TEXT DEFAULT '',
    model TEXT NOT NULL,
    tier TEXT NOT NULL,
    messages TEXT NOT NULL,
    response TEXT NOT NULL,
    tool_calls TEXT DEFAULT '',
    round_messages TEXT DEFAULT '',
    selected_provider TEXT DEFAULT '',
    selected_model TEXT DEFAULT '',
    selection_policy TEXT DEFAULT '',
    candidate_count INTEGER DEFAULT 0,
    latency_ms INTEGER,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    temperature REAL,
    status TEXT NOT NULL,
    error TEXT DEFAULT '',
    reasoning_content TEXT DEFAULT '',
    cache_read INTEGER DEFAULT 0,
    cache_creation INTEGER DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# 为未来按 session 调试预留的索引
CREATE_LLM_TRACES_INDEX_SESSION = """
CREATE INDEX IF NOT EXISTS idx_persona_llm_traces_session ON persona_llm_traces(session_id, created_at DESC);
"""

CREATE_LLM_TRACES_INDEX_USER = """
CREATE INDEX IF NOT EXISTS idx_persona_llm_traces_user ON persona_llm_traces(user_id, created_at DESC);
"""

CREATE_LLM_TRACES_INDEX_CREATED_AT = """
CREATE INDEX IF NOT EXISTS idx_persona_llm_traces_created_at ON persona_llm_traces(created_at);
"""

CREATE_SCORE_HISTORY_INDEX = """
CREATE INDEX IF NOT EXISTS idx_score_history_user_time
ON persona_score_history(user_id, created_at DESC);
"""

CREATE_DAILY_EVENTS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_daily_events_date
ON persona_daily_events(date);
"""

# Agent Runtime 表 (Phase M1)
CREATE_AGENT_RUNS_TABLE = """
CREATE TABLE IF NOT EXISTS persona_agent_runs (
    run_id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    group_id TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    final_reason TEXT DEFAULT '',
    provider TEXT DEFAULT '',
    model TEXT DEFAULT '',
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    tool_rounds INTEGER DEFAULT 0,
    warning_count INTEGER DEFAULT 0,
    sink_failure_count INTEGER DEFAULT 0,
    error TEXT DEFAULT ''
);
"""

CREATE_AGENT_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS persona_agent_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_id, seq)
);
"""

CREATE_AGENT_EVENTS_RUN_INDEX = """
CREATE INDEX IF NOT EXISTS idx_agent_events_run_seq
ON persona_agent_events(run_id, seq);
"""

CREATE_AGENT_EVENTS_TYPE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_agent_events_type
ON persona_agent_events(event_type);
"""

# SA 状态表 (Phase 1 Agent 框架)
CREATE_SA_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS persona_sa_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    text TEXT DEFAULT '',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# Story Deck 表 (SA + DM 共享叙事条目图)
CREATE_STORY_DECK_TABLE = """
CREATE TABLE IF NOT EXISTS persona_story_deck (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    type TEXT NOT NULL,
    content TEXT NOT NULL
);
"""

# Session 表
CREATE_PERSONA_SESSION_TABLE = """
CREATE TABLE IF NOT EXISTS persona_session (
    session_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    static_prompt TEXT DEFAULT '',
    static_hash TEXT DEFAULT '',
    token_budget INTEGER DEFAULT 64000,
    token_estimate INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    cursors_json TEXT DEFAULT '{}',
    last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_PERSONA_SESSION_INDEX = """
CREATE INDEX IF NOT EXISTS idx_persona_session_user_status
ON persona_session(user_id, status);
"""

ALTER_PERSONA_SESSION_CURSORS_JSON = """
ALTER TABLE persona_session ADD COLUMN cursors_json TEXT DEFAULT '{}';
"""

CREATE_PERSONA_SESSION_MESSAGE_TABLE = """
CREATE TABLE IF NOT EXISTS persona_session_message (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_calls TEXT DEFAULT '',
    tool_call_id TEXT DEFAULT '',
    name TEXT,
    sequence INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES persona_session(session_id) ON DELETE CASCADE
);
"""

CREATE_PERSONA_SESSION_MESSAGE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_persona_session_message_session
ON persona_session_message(session_id, sequence);
"""


# 跟角色走 -> persona_db
PERSONA_SCHEMA_SQL = [
    CREATE_PERSONA_SESSION_TABLE,
    CREATE_PERSONA_SESSION_INDEX,
    CREATE_PERSONA_SESSION_MESSAGE_TABLE,
    CREATE_PERSONA_SESSION_MESSAGE_INDEX,
    CREATE_MESSAGE_STREAM_TABLE,
    CREATE_MESSAGE_STREAM_USER_INDEX,
    CREATE_MESSAGE_STREAM_GROUP_INDEX,
    CREATE_SETTINGS_TABLE,
    CREATE_SCORE_HISTORY_TABLE,
    CREATE_USAGE_TABLE,
    CREATE_DIARY_TABLE,
    CREATE_DAILY_EVENTS_TABLE,
    CREATE_CHARACTER_STATE_TABLE,
    CREATE_USER_PROFILES_TABLE,
    CREATE_USER_RELATIONSHIPS_TABLE,
    CREATE_FAMILIARITY_DAILY_TABLE,
    CREATE_GROUP_ACTIVITY_TABLE,
    CREATE_LLM_TRACES_TABLE,
    CREATE_LLM_TRACES_INDEX_SESSION,
    CREATE_LLM_TRACES_INDEX_USER,
    CREATE_LLM_TRACES_INDEX_CREATED_AT,
    CREATE_SCORING_FAILURES_TABLE,
    CREATE_SCORING_FAILURES_INDEX,
    CREATE_SCORING_FAILURES_INDEX_CREATED_AT,
    CREATE_SCORE_HISTORY_INDEX,
    CREATE_DAILY_EVENTS_INDEX,
    # Phase M1: Agent Runtime
    CREATE_AGENT_RUNS_TABLE,
    CREATE_AGENT_EVENTS_TABLE,
    CREATE_AGENT_EVENTS_RUN_INDEX,
    CREATE_AGENT_EVENTS_TYPE_INDEX,
    # Phase 1: Agent 框架 — SA 状态表
    CREATE_SA_STATE_TABLE,
    # Story Deck 叙事条目图
    CREATE_STORY_DECK_TABLE,
]

# 留主库 -> core_db
BOT_CORE_SCHEMA_SQL = [
    CREATE_WHITELIST_TABLE,
    CREATE_USER_MUTE_TABLE,
    CREATE_USER_LLM_CONFIG_TABLE,
    CREATE_GLOBAL_SETTINGS_TABLE,
]
