# 数据层架构说明

## 概述

DicePP 业务数据由 **`BotDatabase`**（`src/plugins/DicePP/core/data/database.py`）统一管理：

| 文件 | 连接 | 内容 |
|------|------|------|
| `DATA_PATH/Bot/<bot_id>/bot_data.db` | `aiosqlite` | 各业务表：复合 TEXT 主键 + `data`（JSON，对应 Pydantic 模型）+ `updated_at` |
| `DATA_PATH/Bot/<bot_id>/log.db` | `aiosqlite` | 跑团日志：`logs` / `records` 表，`LogRepository` |

启动时在 `Bot.delay_init_command()` 中执行 `await self.db.connect()`，并设置 WAL、`synchronous=NORMAL`、`foreign_keys=ON`。

**历史**：早期版本使用基于多文件 JSON 的 `DataManager` + `DataChunk`。**该实现已从代码库移除**；若在其他资料中见到 `data_manager.get_data(...)`，请改读各 `Repository` 的异步 API。

## 架构示意

```
┌─────────────────────────────────────────┐
│              Bot                        │
│  self.db: BotDatabase                   │
│    ├── bot_data.db → Repository<T>    │
│    └── log.db → LogRepository           │
└─────────────────────────────────────────┘
```

## 数据模型

Pydantic 模型位于 `src/plugins/DicePP/core/data/models/`（如 `UserKarma`、`InitList`、`DNDCharacter`、`GroupConfig`、`UserStat` 等）。  
统计、部分嵌套结构在内存中仍可使用 **`JsonObject`**（`core/data/json_object.py`），序列化后再写入 `data` 列或由上层拼装。

## Repository API

### 通用操作

```python
row = await db.xxx.get(key1, key2, ...)
await db.xxx.save(item)      # 或 upsert（语义同 INSERT OR REPLACE）
await db.xxx.delete(key1, ...)
rows = await db.xxx.list_all()
rows = await db.xxx.list_by(group_id=value)
keys = await db.xxx.list_key_values_by("user_id", group_id=gid)
```

键的个数与顺序须与该表在 `BotDatabase._ensure_all_tables` 中注册的 `key_fields` 一致。

### 示例：先攻

```python
init_data = await self.bot.db.initiative.get(group_id)
if init_data is None:
    init_data = InitList(group_id=group_id)
init_data.add_entity(name, owner, roll_value)
await self.bot.db.initiative.save(init_data)
```

### 示例：业力

```python
karma = await self.bot.db.karma.get(user_id, group_id)
await self.bot.db.karma.save(karma_record)
```

### 日志（LogRepository）

```python
session = await self.bot.db.log.get_session(log_id)
await self.bot.db.log.save_session(session)
await self.bot.db.log.add_record(record)
records = await self.bot.db.log.get_records(log_id)
recent = await self.bot.db.log.query_by_group(group_id, limit=100)
```

具体方法以 `core/data/log_repository.py` 为准。

## 在命令中使用

`UserCommandBase.process_msg` 为 **`async def`**，可直接 `await self.bot.db....`。

```python
async def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
    init_data = await self.bot.db.initiative.get(meta.group_id)
    ...
```

## 命名常量

`core/data/basic.py` 中的 `DC_USER_DATA`、`DCK_USER_STAT`、`DC_NICKNAME` 等表示**逻辑数据域标识**，与部分模块常量（如 `DC_GROUPCONFIG`）一起用于代码与文档对齐；**不等同于**旧版「每个 DataChunk 一个 JSON 文件」的存储方式。

## 测试

```bash
uv run pytest tests/core/data/ -v
```

## 相关文档

- 框架总览：`src/plugins/DicePP/docs/architecture.md`
- 命令内访问模式：`src/plugins/DicePP/docs/command_pattern.md`
