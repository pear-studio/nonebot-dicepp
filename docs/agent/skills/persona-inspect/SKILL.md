---
name: persona-inspect
description: "使用 Persona 数据库 Inspection CLI 工具快速排查 persona 模块的 SQLite 数据问题。定位聚合画像查询，单表查询请直接用 sqlite3 CLI。"
license: MIT
metadata:
  author: DicePP
  version: "2.0"
---

## 使用时机

- 排查 persona 模块异常（角色状态、用户关系、评分历史、消息记录等）
- 检查 LLM 健康状态（错误分布、延迟、max_rounds、用量）
- 查看群活跃度概览
- 快速了解某用户的完整 persona 数据画像

## 前提条件

1. 在项目根目录（包含 `scripts/dev/persona_inspect.py` 的目录）
2. 目标 SQLite 数据库可访问（`data/bots/<bot_id>/bot_data.db` 或通过 `--db` 指定）
3. 单表查询推荐直接用 `sqlite3` CLI，本工具专注跨表聚合

## 子命令

| 子命令 | 说明 |
|--------|------|
| `user <id>` | 用户全貌：画像 + 关系分 + 最近消息 + 评分变化 + 今日用量 |
| `state` | 角色永久状态（JSON pretty-print） |
| `llm-health` | LLM 健康概览：错误分布 + 延迟 p50/p95/p99 + max_rounds + 今日用量 |
| `active` | 群活跃度 Top N + 最近群聊观察 |

## 通用选项

| 选项 | 说明 | 默认值 |
|------|------|--------|
| `--db PATH` | SQLite 数据库文件路径 | 自动推断 |
| `--bot-id ID` | Bot ID，自动查找对应数据库 | 无 |
| `--limit N` | 返回记录数上限 | 10 |

## 典型排查场景

### 场景1：用户反馈角色状态异常

```bash
python scripts/dev/persona_inspect.py state --bot-id <bot_id>
python scripts/dev/persona_inspect.py user <user_id> --bot-id <bot_id>
```

### 场景2：某用户好感度异常

```bash
python scripts/dev/persona_inspect.py user <user_id> --bot-id <bot_id>
# 评分变化在输出末尾，按时间升序排列
```

### 场景3：LLM 调用问题 / 配额检查

```bash
python scripts/dev/persona_inspect.py llm-health --bot-id <bot_id>
```

### 场景4：群聊活跃度

```bash
python scripts/dev/persona_inspect.py active --bot-id <bot_id>
```

## 单表查询

以下常见需求直接用 `sqlite3` 更快：

```bash
# 日记列表
sqlite3 data/bots/<id>/bot_data.db \
  "SELECT date, substr(content,1,100) FROM persona_diary ORDER BY date DESC LIMIT 5;"

# 每日事件
sqlite3 data/bots/<id>/bot_data.db \
  "SELECT date, event_type, description FROM persona_daily_events ORDER BY date DESC LIMIT 5;"

# 某用户的消息历史
sqlite3 data/bots/<id>/bot_data.db \
  "SELECT role, substr(content,1,100), created_at FROM persona_unified_messages
   WHERE user_id='<id>' ORDER BY created_at DESC LIMIT 10;"

# LLM trace 详情
sqlite3 data/bots/<id>/bot_data.db \
  "SELECT id, status, latency_ms, tokens_in, tokens_out, error, created_at
   FROM persona_llm_traces ORDER BY created_at DESC LIMIT 10;"
```

sqlite3 配置建议 (`~/.sqliterc`):
```
.headers on
.mode column
.nullvalue NULL
```

## 注意事项

- 该工具**只读**，不会修改数据库
- `--db` 和 `--bot-id` 同时存在时，`--db` 优先
- 本工具不做单表 CRUD 替代，复杂查询请直接用 sqlite3
