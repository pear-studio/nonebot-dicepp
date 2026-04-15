# Persona AI 配置说明

**可复制范本**：仓库根目录 `config/global.json` 中的 `persona_ai` 对象已包含常用键、Phase 2 默认值及多条 `_comment_*` 说明；部署时在其上改 `character_name`、模型 URL 等即可。敏感项（如 `primary_api_key`）放在 `config/secrets.json`，按账号覆盖可写 `config/bots/{账号}.json`（见 `config/bots/_template.json`）。

**本文档用途**：用表格列出各字段含义与注意点；与 `global.json` 不重复贴整段 JSON。更完整的设计说明见 [architecture.md](./architecture.md)。

---

## 基础与时间

| 配置项 | 类型 | 示例 / 默认 | 说明 |
|--------|------|-------------|------|
| `enabled` | bool | `true` | 是否启用 Persona 模块 |
| `character_name` | string | `default` | 角色卡文件名（不含路径），对应 `character_path` 下 yaml |
| `character_path` | string | `./content/characters` | 角色卡目录 |
| `timezone` | string | `Asia/Shanghai` | **IANA 时区名**（`ZoneInfo`）；勿写 `UTC+8` 等。见 `global.json` 内 `_comment_timezone` |
| `whitelist_enabled` | bool | `true` | 是否启用白名单门禁（口令未设置时仍不拦访问，见 deploy.md） |
| `primary_api_key` 等 | string | （secrets） | 密钥建议只放 `secrets.json`；`auxiliary_*` 留空则复用 primary |

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `max_concurrent_requests` | int | 2 | LLM 并发上限 |
| `timeout` | int | 30 | 单次请求超时（秒） |
| `max_short_term_chars` | int | 3000 | 短期记忆字数上限 |
| `max_messages` | int | 200 | 每会话保留消息条数上限 |
| `daily_limit` | int | 20 | 主模型每日对话次数上限（白名单等规则见 deploy.md） |
| `allow_user_key` | bool | true | 是否允许用户自带 API Key |

---

## 时间衰减 (time-decay)

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `decay_enabled` | bool | true | 是否启用时间衰减 |
| `decay_grace_period_hours` | int | 8 | 免衰减期（小时） |
| `decay_rate_per_hour` | float | 0.5 | 每小时衰减量 |
| `decay_daily_cap` | float | 5.0 | **单次**衰减计算中可应用的衰减量上限（`min(按空闲时长算出的衰减, cap)`），非「每个自然日累计总帽」 |
| `decay_floor_offset` | float | 20.0 | 下限 = 初始值 + offset |

展示与对话上下文会按当前规则**惰性计算**衰减；长时间未互动的关系还会在**每日任务**（`tick_daily`）里批量写回数据库，避免仅靠聊天才落库。

---

## 角色生活模拟 (character-life)

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `character_life_enabled` | bool | true | 是否启用生活模拟 |
| `character_life_jitter_minutes` | int | 15 | 触发容差：当前本地「时:分」与角色卡 `generate_event_times()` 给出的计划槽（日内分钟）之差 ≤ 该值则生成事件（`tick` 约 60s） |
| `character_life_diary_time` | string | `"23:30"` | 日记生成时间（`HH:MM`） |

一天内**几条事件、活跃时段、槽位抖动**在角色卡 `extensions.persona`（`daily_events_count`、`event_day_start_hour`、`event_day_end_hour`、`event_jitter_minutes`）配置，不在 `persona_ai`。

---

## 主动消息 (proactive-scheduler)

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `proactive_enabled` | bool | true | 是否启用主动消息 |
| `proactive_min_interval_hours` | int | 4 | 最小发送间隔（小时） |
| `proactive_max_shares` | int | 10 | 每事件最多分享数 |
| `proactive_miss_enabled` | bool | true | 是否启用想念触发 |
| `proactive_miss_min_hours` | int | 72 | 想念触发最小空闲时间 |
| `proactive_miss_min_score` | float | 40.0 | 想念触发最小好感度 |
| `proactive_share_time_window_minutes` | int | 15 | 生活事件入队后仅在此窗口内继续分享 |
| `proactive_event_share_delay_min` | int | 1 | 事件分享延迟的最小值（分钟） |
| `proactive_event_share_delay_max` | int | 5 | 事件分享延迟的最大值（分钟） |
| `proactive_event_share_threshold` | float | 0.5 | 事件分享欲望阈值，大于等于该值的事件才会被分享（`share_desire`） |
| `proactive_greeting_schedule` | list | 见 `global.json` | **已弃用（DEPRECATED）**。定时事件配置已迁移到角色卡 `extensions.persona.scheduled_events`，该字段被调度器 redesign 忽略 |

---

## 群活跃度 (group-activity)

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `group_activity_enabled` | bool | true | 是否启用活跃度追踪 |
| `group_activity_decay_per_day` | float | 10.0 | 每日衰减量 |
| `group_activity_add_per_interaction` | float | 2.0 | 每次互动增加量 |
| `group_activity_max_daily_add` | float | 20.0 | 每日最大增量 |
| `group_activity_min_threshold` | float | 60.0 | 主动消息最低活跃度要求 |
| `group_activity_floor_whitelist` | float | 50.0 | 白名单群下限 |

---

## 群聊观察 (group-observation)

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `observe_group_enabled` | bool | true | 是否启用群聊观察（JSON 中兼容键名 **`observe_group`**，与 `observe_group_enabled` 等价） |
| `observe_min_length` | int | 5 | 最小消息长度 |
| `observe_max_length` | int | 500 | 最大消息长度 |
| `observe_initial_threshold` | int | 20 | 初始触发阈值（条） |
| `observe_max_threshold` | int | 60 | 最大触发阈值 |
| `observe_min_threshold` | int | 5 | 最小触发阈值 |
| `observe_max_records` | int | 30 | 每群最多保留观察数 |
| `observe_max_buffer_size` | int | 60 | 群观察缓冲条数上限 |

---

## 群聊与评分（简要）

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `game_enabled` | bool | true | 是否启用好感度玩法相关逻辑 |
| `scoring_interval` | int | 5 | 每多少轮对话做一次批量评分（轮 = 用户+助手各一条算一轮增量） |
| `group_chat_enabled` | bool | true | 群聊是否可走 Persona |
| `group_simple_scoring` | bool | true | 群聊是否使用简化好感度 |
