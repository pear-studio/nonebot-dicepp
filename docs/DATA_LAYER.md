# 数据层架构文档

## 概述

本文档说明 DicePP 数据层的架构设计，包括传统的 `DataManager` API 和新的 `BotDatabase` API。

## 架构演进

### 旧架构 (DataManager)

```
┌─────────────────────────────────────────┐
│              Bot                        │
│  ┌─────────────────────────────────┐   │
│  │        DataManager              │   │
│  │  ┌───────────────────────────┐  │   │
│  │  │    DataChunk 存储         │  │   │
│  │  │  - DC_INIT               │  │   │
│  │  │  - DC_KARMA              │  │   │
│  │  │  - DC_CHAR_DND           │  │   │
│  │  │  - DC_GROUPCONFIG       │  │   │
│  │  │  - ...                   │  │   │
│  │  └───────────────────────────┘  │   │
│  └─────────────────────────────────┘   │
└─────────────────────────────────────────┘
```

**特点**:
- 同步 API
- 基于 JSON 文件存储
- 使用 `JsonObject` 类进行序列化/反序列化

### 新架构 (BotDatabase)

```
┌─────────────────────────────────────────┐
│              Bot                        │
│  ┌─────────────────────────────────┐   │
│  │        BotDatabase              │   │
│  │  ┌───────────────────────────┐  │   │
│  │  │    aiosqlite 连接池       │  │   │
│  │  └───────────────────────────┘  │   │
│  │  ┌───────────────────────────┐  │   │
│  │  │    Repository 层          │  │   │
│  │  │  - karma Repository       │  │   │
│  │  │  - initiative Repository  │  │   │
│  │  │  - characters_dnd         │  │   │
│  │  │  - log Repository        │  │   │
│  │  └───────────────────────────┘  │   │
│  └─────────────────────────────────┘   │
└─────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│           SQLite 数据库                  │
│  - karma (业力配置)                      │
│  - initiative (先攻列表)                  │
│  - characters_dnd (DND角色)              │
│  - characters_coc (COC角色)              │
│  - log_session (日志会话)                 │
│  - log_record (日志记录)                  │
└─────────────────────────────────────────┘
```

**特点**:
- 异步 API (async/await)
- 基于 SQLite + aiosqlite
- 使用 Pydantic 模型进行数据验证
- 自动表结构创建

## 数据模型

### Pydantic 模型位置

所有数据模型位于 `core/data/models/`:

- `karma.py` - `UserKarma` 业力配置
- `initiative.py` - `InitList`, `InitEntity` 先攻列表
- `log.py` - `LogSession`, `LogRecord` 日志
- `macro.py` - `Macro` 宏
- `variable.py` - `Variable` 变量
- `character.py` - `DNDCharacter`, `COCCharacter`, `HPInfo`, `AbilityInfo`

### 模型示例

```python
from core.data.models import InitList, InitEntity

# 创建先攻列表
init_list = InitList(group_id="123456")
init_list.add_entity("NPC1", "", 15)
init_list.add_entity("PC1", "user_123", 18)

# 序列化存储
await db.initiative.save(init_list)
```

## Repository API

### 通用操作

```python
# 获取
data = await db.xxx.get(key1, key2, ...)

# 保存 (upsert)
await db.xxx.save(data)

# 删除
await db.xxx.delete(key1, key2, ...)

# 列表查询
all_data = await db.xxx.list_all()
filtered_data = await db.xxx.list_by(field=value)
```

### 各 Repository 详解

#### Initiative Repository

```python
# 获取先攻列表
init_list = await self.bot.db.initiative.get(group_id)

# 保存先攻列表
await self.bot.db.initiative.save(init_list)

# 删除先攻列表
await self.bot.db.initiative.delete(group_id)
```

#### Karma Repository

```python
# 获取业力配置
karma = await self.bot.db.karma.get(group_id)

# 保存业力配置
await self.bot.db.karma.save(karma)
```

#### Log Repository

```python
# 获取日志会话
session = await self.bot.db.log.get_session(log_id)

# 保存日志会话
await self.bot.db.log.save_session(session)

# 添加日志记录
await self.bot.db.log.add_record(record)

# 查询记录
records = await self.bot.db.log.get_records(session_id)

# 按群查询会话
sessions = await self.bot.db.log.list_sessions_by_group(group_id)
```

## 命令中使用新 API

### 异步命令

由于命令的 `process_msg` 已改为 async，可以直接使用 await:

```python
async def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
    group_id = meta.group_id

    # 使用新 API
    init_data = await self.bot.db.initiative.get(group_id)
    if init_data is None:
        init_data = InitList(group_id=group_id)

    # 修改数据
    init_data.add_entity(name, owner, roll_value)

    # 保存
    await self.bot.db.initiative.save(init_data)

    return [BotSendMsgCommand(...)]
```

### 同步调用场景

某些模块 (如 karma) 由于深层调用链是同步的，仍使用旧 API:

```python
# karma_manager.py 中仍使用 data_manager
def _get_config(self, group_id: str) -> KarmaConfig:
    default_cfg = KarmaConfig().to_dict()
    data = self.bot.data_manager.get_data(DC_KARMA, [group_id], default_val=default_cfg)
    return KarmaConfig.from_dict(data)
```

## 数据迁移状态

| 模块 | 新 API | 旧 API | 说明 |
|------|--------|--------|------|
| initiative | ✅ | - | 完全迁移 |
| battleroll | ✅ | - | 完全迁移 |
| karma | - | ✅ | 同步调用链限制 |
| log | - | ✅ | 数据结构复杂 |
| character (dnd5e) | - | ✅ | JsonObject 不兼容 |
| character (coc) | - | ✅ | JsonObject 不兼容 |
| macro | - | ✅ | 复杂初始化逻辑 |
| variable | - | ✅ | 复杂初始化逻辑 |
| groupconfig | - | ✅ | 使用广泛 |
| statistics | - | ✅ | 使用广泛 |

## 未来规划

1. **渐进式迁移**: 继续将更多模块迁移到新 API
2. **数据兼容**: 保留旧 API 直到所有模块迁移完成
3. **性能优化**: 利用 SQLite 的事务和索引提升性能

## 附录: 测试

运行数据层测试:

```bash
uv run pytest tests/core/data/ -v
```

测试结果: **86 passed, 36 skipped**
