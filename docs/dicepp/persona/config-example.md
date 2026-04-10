# Persona AI 配置示例

> Phase 2 完整配置参考

## 完整配置示例

```json
{
  "persona_ai": {
    "enabled": true,
    "character_name": "default",
    "character_path": "./content/characters",

    "whitelist_enabled": true,

    "primary_api_key": "sk-xxxxxxxx",
    "primary_base_url": "https://api.openai.com/v1",
    "primary_model": "gpt-4o",

    "auxiliary_api_key": "",
    "auxiliary_base_url": "",
    "auxiliary_model": "gpt-4o-mini",

    "max_concurrent_requests": 2,
    "timeout": 30,
    "timezone": "Asia/Shanghai",

    "max_short_term_chars": 3000,
    "max_messages": 200,

    "game_enabled": true,
    "scoring_interval": 5,
    "group_chat_enabled": true,
    "group_simple_scoring": true,

    "daily_limit": 20,
    "allow_user_key": true,

    "=== Phase 2 新增配置 ===": "",

    "decay_enabled": true,
    "decay_grace_period_hours": 8,
    "decay_rate_per_hour": 0.5,
    "decay_daily_cap": 5.0,
    "decay_floor_offset": 20.0,

    "character_life_enabled": true,
    "character_life_event_hours": [8, 11, 14, 17, 20],
    "character_life_jitter_minutes": 15,
    "character_life_diary_time": "23:30",

    "proactive_enabled": true,
    "proactive_quiet_start": 23,
    "proactive_quiet_end": 7,
    "proactive_min_interval_hours": 4,
    "proactive_max_shares": 10,
    "proactive_miss_enabled": true,
    "proactive_miss_min_hours": 72,
    "proactive_miss_min_score": 40.0,
    "proactive_share_time_window_minutes": 15,
    "proactive_greeting_schedule": [
      {"event_type": "wake_up", "time_range": "07:00-08:00"},
      {"event_type": "lunch", "time_range": "11:30-13:00"},
      {"event_type": "afternoon", "time_range": "14:00-15:00"},
      {"event_type": "dinner", "time_range": "17:30-19:00"},
      {"event_type": "good_night", "time_range": "22:00-23:00"}
    ],

    "group_activity_enabled": true,
    "group_activity_decay_per_day": 10.0,
    "group_activity_add_per_interaction": 2.0,
    "group_activity_max_daily_add": 20.0,
    "group_activity_min_threshold": 60.0,
    "group_activity_floor_whitelist": 50.0,

    "observe_group_enabled": true,
    "observe_max_buffer_size": 60,
    "observe_min_length": 5,
    "observe_max_length": 500,
    "observe_initial_threshold": 20,
    "observe_max_threshold": 60,
    "observe_min_threshold": 5,
    "observe_max_records": 30
  }
}
```

## 配置说明

### 时间衰减 (time-decay)

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `decay_enabled` | bool | true | 是否启用时间衰减 |
| `decay_grace_period_hours` | int | 8 | 免衰减期（小时） |
| `decay_rate_per_hour` | float | 0.5 | 每小时衰减量 |
| `decay_daily_cap` | float | 5.0 | **单次**衰减计算中可应用的衰减量上限（`min(按空闲时长算出的衰减, cap)`），非「每个自然日累计总帽」 |
| `decay_floor_offset` | float | 20.0 | 下限 = 初始值 + offset |

展示与对话上下文会按当前规则**惰性计算**衰减；长时间未互动的关系还会在**每日任务**（`tick_daily`）里批量写回数据库，避免仅靠聊天才落库。

### 角色生活模拟 (character-life)

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `character_life_enabled` | bool | true | 是否启用生活模拟 |
| `character_life_event_hours` | list | [8,11,14,17,20] | 事件生成时间点 |
| `character_life_jitter_minutes` | int | 15 | 时间抖动范围（±分钟） |
| `character_life_diary_time` | string | "23:30" | 日记生成时间 |

`timezone`（根级 `persona_ai`）用于生活模拟与主动调度器的本地时钟（`ZoneInfo`）。

### 主动消息 (proactive-scheduler)

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `proactive_enabled` | bool | true | 是否启用主动消息 |
| `proactive_quiet_start` | int | 23 | 安静时段开始（小时） |
| `proactive_quiet_end` | int | 7 | 安静时段结束（小时） |
| `proactive_min_interval_hours` | int | 4 | 最小发送间隔（小时） |
| `proactive_max_shares` | int | 10 | 每事件最多分享数 |
| `proactive_miss_enabled` | bool | true | 是否启用想念触发 |
| `proactive_miss_min_hours` | int | 72 | 想念触发最小空闲时间 |
| `proactive_miss_min_score` | float | 40.0 | 想念触发最小好感度 |
| `proactive_share_time_window_minutes` | int | 15 | 生活事件入队后仅在此窗口内继续分享 |
| `proactive_greeting_schedule` | list | 见默认 | 定时问候：`event_type` 为日内去重键；`time_range` 为**同一自然日内** `HH:MM-HH:MM`（闭区间），**不支持跨午夜**，跨日请拆成多条 |

### 群活跃度 (group-activity)

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `group_activity_enabled` | bool | true | 是否启用活跃度追踪 |
| `group_activity_decay_per_day` | float | 10.0 | 每日衰减量 |
| `group_activity_add_per_interaction` | float | 2.0 | 每次互动增加量 |
| `group_activity_max_daily_add` | float | 20.0 | 每日最大增量 |
| `group_activity_min_threshold` | float | 60.0 | 主动消息最低活跃度要求 |
| `group_activity_floor_whitelist` | float | 50.0 | 白名单群下限 |

### 群聊观察 (group-observation)

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `observe_group_enabled` | bool | true | 是否启用群聊观察（JSON 中兼容键名 **`observe_group`**，与 `observe_group_enabled` 等价） |
| `observe_min_length` | int | 5 | 最小消息长度 |
| `observe_max_length` | int | 500 | 最大消息长度 |
| `observe_initial_threshold` | int | 20 | 初始触发阈值（条） |
| `observe_max_threshold` | int | 60 | 最大触发阈值 |
| `observe_min_threshold` | int | 5 | 最小触发阈值 |
| `observe_max_records` | int | 30 | 每群最多保留观察数 |
