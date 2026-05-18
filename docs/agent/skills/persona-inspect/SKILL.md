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
- 排查 LLM trace 工具调用链路（think 块、tool_call/tool_result 每轮详情）
- 查看所有 persona_ 前缀表的 DDL

## 前提条件

1. 在项目根目录（包含 `scripts/dev/persona_inspect.py` 的目录）
2. 目标 SQLite 数据库可访问（`data/bots/<bot_id>/bot_data.db` 或通过 `--db` 指定）
3. 单表查询推荐直接用 `sqlite3` CLI，本工具专注跨表聚合和格式化输出

## 子命令

| 子命令 | 说明 |
|--------|------|
| `user <id>` | 用户全貌：画像 + 关系分 + 最近消息 + 评分变化 + 今日用量 |
| `state` | 角色永久状态（JSON pretty-print） |
| `llm-health` | LLM 健康概览：错误分布 + 延迟 p50/p95/p99 + max_rounds + 今日用量 |
| `active` | 群活跃度 Top N + 最近群聊观察 |
| `tables` | 列出所有 `persona_` 前缀表的 DDL |
| `trace` | LLM Trace 详情：轮次级 think/tool_call/tool_result 格式化输出 |

## 通用选项

| 选项 | 说明 | 默认值 |
|------|------|--------|
| `--db PATH` | SQLite 数据库文件路径 | 自动推断 |
| `--bot-id ID` | Bot ID，自动查找对应数据库 | 无 |
| `--limit N` | 返回记录数上限 | 10（trace 默认 5） |

### trace 专属选项

| 选项 | 说明 |
|------|------|
| `--id N` | 精确匹配单条 trace |
| `--user-id UID` | 按 user_id 过滤（自动包含空 user_id 的记录） |
| `--full` | 展开所有返回 trace 的完整内容（默认仅展开最新一条） |

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

### 场景5：排查 LLM 工具调用链路（某轮工具为什么失败 / LLM think 内容）

```bash
# 查看某用户最近的 trace（自动展开最新一条的 round_messages）
python scripts/dev/persona_inspect.py trace --user-id <user_id> --bot-id <bot_id>

# 查看所有 trace 的完整轮次细节
python scripts/dev/persona_inspect.py trace --user-id <user_id> --full --bot-id <bot_id>

# 查看特定 trace
python scripts/dev/persona_inspect.py trace --id 42 --bot-id <bot_id>
```

### 场景6：查看表结构

```bash
python scripts/dev/persona_inspect.py tables --bot-id <bot_id>
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
  "SELECT role, substr(content,1,100), created_at FROM persona_messages
   WHERE user_id='<id>' ORDER BY created_at DESC LIMIT 10;"

# LLM trace 元数据
sqlite3 data/bots/<id>/bot_data.db \
  "SELECT id, status, latency_ms, tokens_in, tokens_out, error, created_at
   FROM persona_llm_traces ORDER BY created_at DESC LIMIT 10;"

# trace 的 round_messages（轮次级工具调用细节）
sqlite3 data/bots/<id>/bot_data.db \
  "SELECT id, status, round_messages FROM persona_llm_traces
   WHERE user_id='<id>' ORDER BY created_at DESC LIMIT 3;"
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
