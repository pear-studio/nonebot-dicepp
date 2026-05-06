---
name: persona-inspect
description: "使用 Persona 数据库 Inspection CLI 工具快速排查 persona 模块的 SQLite 数据问题，替代手写 Python 脚本。"
license: MIT
metadata:
  author: DicePP
  version: "1.0"
---

## 使用时机

- 排查 persona 模块异常（角色状态不对、关系分数异常、消息丢失、日记内容异常等）
- 调试延迟任务（delayed_tasks）是否堆积或失败
- 检查 LLM Trace 错误、token 消耗、延迟分布
- 快速了解某用户/某群的完整 persona 数据画像
- 验证数据迁移或 schema 变更后的表状态

## 前提条件

1. 在项目根目录（包含 `scripts/dev/persona_inspect.py` 的目录）
2. 目标 SQLite 数据库可访问（`data/bots/<bot_id>/bot_data.db` 或通过 `--db` 指定）

## 基本用法

```bash
# 列出所有 persona 表及行数
python scripts/dev/persona_inspect.py tables --bot-id <bot_id>

# 一键汇总排查（events → state → diary → relationships → delayed_tasks → messages）
python scripts/dev/persona_inspect.py summary --bot-id <bot_id>

# 查看指定数据库
python scripts/dev/persona_inspect.py tables --db /path/to/bot_data.db
```

## 子命令速查

| 子命令 | 说明 | 常用过滤选项 |
|--------|------|-------------|
| `tables` | 所有 persona 表及行数 | — |
| `summary` | 一键汇总排查信息 | `--limit` |
| `state` | 角色永久状态（JSON） | — |
| `diary` | 角色日记 | `--date`, `--limit` |
| `events` | 每日生活事件 | `--date`, `--limit` |
| `relationships` | 用户关系（四维好感度） | `--user`, `--group`, `--limit` |
| `score-history` | 评分历史（好感度变化明细） | `--user`, `--group`, `--limit` |
| `delayed-tasks` | 延迟任务队列 | `--status`, `--limit` |
| `messages` | 私聊消息历史 | `--user`, `--group`, `--limit` |
| `observations` | 群聊观察记录 | `--group`, `--limit` |
| `llm-traces` | LLM 调用 trace | `--user`, `--group`, `--limit` |
| `group-activity` | 群活跃度 | `--group`, `--limit` |

## 通用选项

| 选项 | 说明 | 默认值 |
|------|------|--------|
| `--db PATH` | SQLite 数据库文件路径 | 自动推断 `data/bots/<bot_id>/bot_data.db` |
| `--bot-id ID` | Bot ID，自动查找对应数据库 | 无 |
| `--limit N` | 返回记录数上限 | 20 |
| `--user ID` | 按用户 ID 过滤 | 无 |
| `--group ID` | 按群 ID 过滤 | 无 |
| `--date YYYY-MM-DD` | 按日期过滤 | 无 |
| `--status STATUS` | 按状态过滤（仅 delayed-tasks）：`pending` / `completed` / `failed` | 无 |
| `--format {table,json}` | 输出格式 | table |

## 典型排查场景

### 场景1：用户反馈角色状态异常

```bash
# 查看角色当前状态
python scripts/dev/persona_inspect.py state --bot-id <bot_id>

# 查看最近日记和事件
python scripts/dev/persona_inspect.py diary --bot-id <bot_id> --limit 3
python scripts/dev/persona_inspect.py events --bot-id <bot_id> --date 2026-05-06
```

### 场景2：某用户好感度异常

```bash
# 查看该用户所有关系
python scripts/dev/persona_inspect.py relationships --bot-id <bot_id> --user <user_id>

# 查看评分历史（好感度变化明细）
python scripts/dev/persona_inspect.py score-history --bot-id <bot_id> --user <user_id> --limit 10
```

### 场景3：延迟任务堆积

```bash
# 查看待处理任务
python scripts/dev/persona_inspect.py delayed-tasks --bot-id <bot_id> --status pending

# 查看失败任务
python scripts/dev/persona_inspect.py delayed-tasks --bot-id <bot_id> --status failed
```

### 场景4：LLM 调用问题

```bash
# 查看最近错误 trace
python scripts/dev/persona_inspect.py llm-traces --bot-id <bot_id> --limit 10

# 查看某用户的 trace
python scripts/dev/persona_inspect.py llm-traces --bot-id <bot_id> --user <user_id>
```

### 场景5：群聊观察异常

```bash
# 查看某群的观察记录
python scripts/dev/persona_inspect.py observations --bot-id <bot_id> --group <group_id>
```

### 场景6：完整数据画像（JSON 输出）

```bash
# 导出某用户所有相关数据为 JSON
python scripts/dev/persona_inspect.py relationships --db bot_data.db --user <user_id> --format json
python scripts/dev/persona_inspect.py messages --db bot_data.db --user <user_id> --format json
```

## 注意事项

- 该工具**只读**，不会修改数据库
- 使用同步 `sqlite3` 直接连接，无需启动 bot 或加载配置
- `--db` 和 `--bot-id` 同时存在时，`--db` 优先
- 字符串字段默认截断至 200~300 字符，避免输出过长；完整内容请用 `--format json` 或直接用 sqlite3 查询
