"""
数据库迁移脚本

创建 Persona 模块所需的表。

所有列定义直接包含在 CREATE TABLE 语句中，不再需要运行时 ALTER 补丁。
新增索引通过 ``_ensure_indexes`` 幂等补全。
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
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL,
    group_id     TEXT NOT NULL DEFAULT '',
    role         TEXT NOT NULL,
    type         TEXT NOT NULL DEFAULT 'chat',
    content      TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

# 旧表迁移: persona_unified_messages → message_stream
RENAME_LEGACY_TABLE = """
ALTER TABLE persona_unified_messages RENAME TO message_stream;
"""

DROP_LEGACY_USER_INDEX = """
DROP INDEX IF EXISTS idx_umsgs_user_time;
"""

DROP_LEGACY_GROUP_INDEX = """
DROP INDEX IF EXISTS idx_umsgs_group_time;
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

# DM 状态表 (Phase 1 Agent 框架)
CREATE_DM_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS persona_dm_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    text TEXT DEFAULT '',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# SA 状态表 (Phase 1 Agent 框架)
CREATE_SA_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS persona_sa_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    text TEXT DEFAULT '',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# message_stream 扩展列（Phase M1），用 ALTER TABLE 以避免影响已有 schema
ALTER_MESSAGE_STREAM_ADD_AGENT_RUN_ID = """
ALTER TABLE message_stream ADD COLUMN agent_run_id TEXT DEFAULT '';
"""

ALTER_MESSAGE_STREAM_ADD_TURN_ID = """
ALTER TABLE message_stream ADD COLUMN turn_id TEXT DEFAULT '';
"""

ALTER_MESSAGE_STREAM_ADD_SEGMENT_INDEX = """
ALTER TABLE message_stream ADD COLUMN segment_index INTEGER DEFAULT -1;
"""

ALTER_MESSAGE_STREAM_ADD_SEGMENT_PHASE = """
ALTER TABLE message_stream ADD COLUMN segment_phase TEXT DEFAULT '';
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
    last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_PERSONA_SESSION_INDEX = """
CREATE INDEX IF NOT EXISTS idx_persona_session_user_status
ON persona_session(user_id, status);
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


# ── 跟角色走 → persona_db（14 张表 + 索引） ──────────────────────
PERSONA_DB_MIGRATIONS = [
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
    # Phase 1: Agent 框架 — DM / SA 状态表
    CREATE_DM_STATE_TABLE,
    CREATE_SA_STATE_TABLE,
]

# ── 留主库 → core_db（4 张表） ──────────────────────────────────
CORE_DB_MIGRATIONS = [
    CREATE_WHITELIST_TABLE,
    CREATE_USER_MUTE_TABLE,
    CREATE_USER_LLM_CONFIG_TABLE,
    CREATE_GLOBAL_SETTINGS_TABLE,
]

# Phase 3: message_stream 图片元信息列
ALTER_MESSAGE_STREAM_ADD_IMAGE_META = """
ALTER TABLE message_stream ADD COLUMN image_meta TEXT DEFAULT '';
"""

# message_stream 扩展列 ALTER TABLE（独立列表，因 SQLite 的 ADD COLUMN 非幂等）
ALTER_MESSAGE_STREAM_COLUMNS = [
    ALTER_MESSAGE_STREAM_ADD_AGENT_RUN_ID,
    ALTER_MESSAGE_STREAM_ADD_TURN_ID,
    ALTER_MESSAGE_STREAM_ADD_SEGMENT_INDEX,
    ALTER_MESSAGE_STREAM_ADD_SEGMENT_PHASE,
    ALTER_MESSAGE_STREAM_ADD_IMAGE_META,
]

# persona_llm_traces 扩展列（Phase 1: reasoning_content 通用化）
ALTER_LLM_TRACES_ADD_RUN_ID = """
ALTER TABLE persona_llm_traces ADD COLUMN run_id TEXT DEFAULT '';
"""

ALTER_LLM_TRACES_ADD_REASONING_CONTENT = """
ALTER TABLE persona_llm_traces ADD COLUMN reasoning_content TEXT DEFAULT '';
"""

ALTER_LLM_TRACES_ADD_CACHE_READ = """
ALTER TABLE persona_llm_traces ADD COLUMN cache_read INTEGER DEFAULT 0;
"""

ALTER_LLM_TRACES_ADD_CACHE_CREATION = """
ALTER TABLE persona_llm_traces ADD COLUMN cache_creation INTEGER DEFAULT 0;
"""

ALTER_LLM_TRACES_ADD_REASONING_TOKENS = """
ALTER TABLE persona_llm_traces ADD COLUMN reasoning_tokens INTEGER DEFAULT 0;
"""

ALTER_LLM_TRACES_COLUMNS = [
    ALTER_LLM_TRACES_ADD_RUN_ID,
    ALTER_LLM_TRACES_ADD_REASONING_CONTENT,
    ALTER_LLM_TRACES_ADD_CACHE_READ,
    ALTER_LLM_TRACES_ADD_CACHE_CREATION,
    ALTER_LLM_TRACES_ADD_REASONING_TOKENS,
]

# persona_user_relationships 扩展列（四维→三维模型迁移，幂等 ALTER TABLE）
ALTER_USER_RELATIONSHIPS_ADD_FAMILIARITY = """
ALTER TABLE persona_user_relationships ADD COLUMN familiarity REAL DEFAULT 0.0;
"""

ALTER_USER_RELATIONSHIPS_ADD_PEAK_FAMILIARITY = """
ALTER TABLE persona_user_relationships ADD COLUMN peak_familiarity REAL DEFAULT 0.0;
"""

ALTER_USER_RELATIONSHIPS_ADD_PEAK_INTIMACY = """
ALTER TABLE persona_user_relationships ADD COLUMN peak_intimacy REAL DEFAULT 0.0;
"""

ALTER_USER_RELATIONSHIPS_ADD_REPUTATION = """
ALTER TABLE persona_user_relationships ADD COLUMN reputation REAL DEFAULT 100.0;
"""

ALTER_USER_RELATIONSHIPS_ADD_RECOVERY_DATE = """
ALTER TABLE persona_user_relationships ADD COLUMN last_reputation_recovery_date TIMESTAMP;
"""

ALTER_USER_RELATIONSHIPS_COLUMNS = [
    ALTER_USER_RELATIONSHIPS_ADD_FAMILIARITY,
    ALTER_USER_RELATIONSHIPS_ADD_PEAK_FAMILIARITY,
    ALTER_USER_RELATIONSHIPS_ADD_PEAK_INTIMACY,
    ALTER_USER_RELATIONSHIPS_ADD_REPUTATION,
    ALTER_USER_RELATIONSHIPS_ADD_RECOVERY_DATE,
]

# persona_score_history 扩展列（四维→三维模型迁移，幂等 ALTER TABLE）
ALTER_SCORE_HISTORY_ADD_REPUTATION_DELTA = """
ALTER TABLE persona_score_history ADD COLUMN reputation_delta REAL DEFAULT 0;
"""

ALTER_SCORE_HISTORY_ADD_FAMILIARITY_DELTA = """
ALTER TABLE persona_score_history ADD COLUMN familiarity_delta REAL DEFAULT 0;
"""

ALTER_SCORE_HISTORY_COLUMNS = [
    ALTER_SCORE_HISTORY_ADD_REPUTATION_DELTA,
    ALTER_SCORE_HISTORY_ADD_FAMILIARITY_DELTA,
]

# share_desire 列清理（Phase 3 业务逻辑已全部移除）
ALTER_DAILY_EVENTS_DROP_SHARE_DESIRE = """
ALTER TABLE persona_daily_events DROP COLUMN share_desire;
"""
