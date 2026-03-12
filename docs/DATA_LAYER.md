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

### 已完成迁移 (100%)

| 模块 | 新 API | 旧 API | 说明 |
|------|--------|--------|------|
| initiative | ✅ | - | 完全迁移，process_msg 已异步化 |
| battleroll | ✅ | - | 完全迁移 |
| macro | ✅ | - | 完全迁移，使用 await bot.db.macro |
| variable | ✅ | - | 完全迁移，使用 await bot.db.variable |
| log (统一) | ✅ | - | log_db.py 已删除，统一使用 BotDatabase.log |

### 部分迁移 (50-80%)

| 模块 | 新 API | 旧 API | 说明 |
|------|--------|--------|------|
| karma | 部分 | ✅ | 配置已从 DB 加载，但状态未持久化 (待完成 5.5) |
| groupconfig | 部分 | ✅ | Karma 配置从 DB 读取，其他配置仍用旧 API |
| nickname | 部分 | ✅ | 部分命令已迁移，dicebot.py 中仍用旧 API |
| log (命令) | 部分 | ✅ | log_command.py 已迁移，但保留旧结构兼容 |

### 未迁移 (0%)

| 模块 | 新 API | 旧 API | 说明 |
|------|--------|--------|------|
| character (dnd5e) | - | ✅ | NPCHealth 模型已添加，命令未迁移 |
| character (coc) | - | ✅ | NPCHealth 模型已添加，命令未迁移 |
| statistics | - | ✅ | dicebot.py 中广泛使用，待整体迁移 |
| point/favor | - | ✅ | 仍使用 data_manager |
| activate/welcome | - | ✅ | 模型已添加，命令未迁移 |
| chat_record | - | ✅ | 模型已添加，命令未迁移 |
| bot_control | - | ✅ | 模型已添加，命令未迁移 |

## 待完成工作

### 高优先级 (影响数据完整性)

#### 1. Karma 状态持久化 (任务 5.5)

**位置**: `module/roll/roll_dice_command.py`

**问题**: Karma 配置已从 DB 加载并注入运行时，但掷骰后的 Karma 状态变化未写回数据库

**需要添加的代码**:
```python
# 在 process_msg 末尾，记录掷骰结果后添加
if karma_enabled:
    karma_entry = await self.bot.db.karma.get(meta.user_id, meta.group_id)
    # 更新 karma_entry 的统计信息
    # await self.bot.db.karma.upsert(karma_entry)
```

**风险**: 当前每次掷骰的 Karma 修正数据未被持久化，重启后丢失

---

#### 2. dicebot.py 核心流程迁移 (任务 3.2-3.4)

**位置**: `core/bot/dicebot.py`

**需要迁移的热路径**:

1. **统计更新** (L112, L267-277):
   ```python
   # 当前代码
   meta_stat: MetaStatInfo = self.data_manager.get_data(DC_META, [DCK_META_STAT], default_gen=MetaStatInfo)
   
   # 目标代码
   meta_stat_db = await self.db.meta_stat.get("meta")
   if meta_stat_db:
       meta_stat = MetaStatInfo.deserialize(meta_stat_db.data)
   else:
       meta_stat = MetaStatInfo()
   ```

2. **Macro 展开** (process_message 中):
   ```python
   # 当前代码
   macro_list = self.data_manager.get_data(DC_MACRO, [user_id], default_val=[])
   
   # 目标代码
   macro_db = await self.db.macro.get(user_id)
   macro_list = json.loads(macro_db.content) if macro_db else []
   ```

3. **Variable 替换** (process_message 中):
   ```python
   # 当前代码
   var_value = self.data_manager.get_data(DC_VARIABLE, [user_id, var_name])
   
   # 目标代码
   var_db = await self.db.variable.get(user_id, var_name)
   var_value = var_db.value if var_db else None
   ```

4. **Nickname 更新** (process_message 中):
   ```python
   # 当前代码
   nickname = self.data_manager.get_data(DC_NICKNAME, [user_id, group_id])
   
   # 目标代码
   nickname_db = await self.db.nickname.get(user_id, group_id)
   nickname = nickname_db.nickname if nickname_db else None
   ```

**风险**: dicebot.py 是核心消息处理循环，需要充分测试

---

### 中优先级 (功能完整性)

#### 3. 统计数据查询命令迁移 (任务 3.5)

**位置**: `module/misc/statistics_cmd.py`

**需要修改**:
```python
# 当前代码
user_stat: UserStatInfo = self.bot.data_manager.get_data(DC_USER_DATA, [user_id, DCK_USER_STAT])

# 目标代码
user_stat_db = await self.bot.db.user_stat.get(user_id)
if user_stat_db:
    user_stat = UserStatInfo.deserialize(user_stat_db.data)
```

---

#### 4. 角色系统迁移 (任务 6.1-6.3)

**位置**: 
- `module/character/dnd5e/char_command.py`
- `module/character/dnd5e/hp_command.py`
- `module/character/coc/hp_command.py`
- `module/character/char_command.py`

**需要迁移**:
1. DND 角色卡 CRUD 操作 → `await bot.db.characters_dnd.<method>()`
2. COC 角色卡 CRUD 操作 → `await bot.db.characters_coc.<method>()`
3. NPC 生命值操作 → `await bot.db.npc_health.<method>()`

**示例**:
```python
# HP 命令中
# 当前代码
hp_info = self.bot.data_manager.get_data(DC_CHAR_HP, [group_id, npc_name])

# 目标代码
npc_health = await self.bot.db.npc_health.get(group_id, npc_name)
if npc_health:
    hp_info = HPInfo.deserialize(json.loads(npc_health.hp_data))
```

---

#### 5. GroupConfig 全面迁移

**位置**: 多个命令文件中使用群配置的地方

**需要迁移的命令**:
- `module/common/groupconfig_command.py`
- `module/roll/dice_set_command.py`
- `module/common/mode_command.py`
- `module/query/query_command.py`
- `module/query/homebrew_command.py`
- `module/common/chat_command.py`

**示例**:
```python
# 读取群配置
# 当前代码
config = self.bot.data_manager.get_data(DC_GROUPCONFIG, [group_id])

# 目标代码
group_config = await self.bot.db.group_config.get(group_id)
if group_config:
    config = group_config.data
```

---

### 低优先级 (清理与优化)

#### 6. DataManager 退役评估 (任务 7.3)

**位置**: `core/data/manager.py`, `core/bot/dicebot.py`

**需要评估**:
1. 是否还有模块必须依赖 DataManager
2. 是否可以标记为 deprecated
3. 是否可以移除或替换为 BotDatabase 包装器

**当前依赖 DataManager 的模块**:
- karma_manager (部分)
- statistics (dicebot.py tick_daily)
- log_command.py (保留兼容)

---

#### 7. 文档更新 (任务 7.5)

**需要更新的文档**:
- ✅ `docs/DATA_LAYER.md` (本文档)
- `src/plugins/DicePP/docs/README.md` - 添加异步命令示例
- `docs/agent/rules/dicepp.md` - 更新开发规范

---

## 迁移进度追踪

### Phase 1 — 数据层扩展 ✅
- [x] 新增所有 Pydantic Model
- [x] 注册所有 Repository
- [x] 编写单元测试

### Phase 2 — 快速修复 ✅
- [x] initiative_command.py 异步化

### Phase 3 — 常规 Command 迁移 ⏳
- [x] macro_command.py
- [x] variable_command.py
- [x] log_command.py (统一日志系统)
- [ ] nickname_command.py (部分完成)
- [ ] groupconfig_command.py (部分完成)
- [ ] 其他 common 命令

### Phase 4 — 角色系统迁移 ❌
- [ ] dnd5e/char_command.py
- [ ] dnd5e/hp_command.py
- [ ] coc/hp_command.py

### Phase 5 — 日志系统统一 ✅
- [x] log_command.py 改写为调用 bot.db.log
- [x] log_db.py 删除

### Phase 6 — 统计数据迁移 ❌
- [ ] dicebot.py 统计读写改为异步
- [ ] statistics_cmd.py 迁移

### Phase 7 — dicebot.py 核心流程迁移 ❌
- [ ] process_message macro/variable/nickname 改为异步

### Phase 8 — Karma 异步重构 ⏳
- [x] karma_manager 重构
- [x] DB 访问移到 process_msg 层
- [ ] Karma 状态持久化 (5.5)

---

## 测试覆盖率

当前测试状态 (`uv run pytest -v`):
- **总计**: 129 个测试
- **通过**: 86 个
- **跳过**: 36 个 (集成测试需要完整环境)
- **失败**: 0 个

**数据层测试**:
- `test_models.py`: ✅ 所有 Pydantic 模型测试通过
- `test_repository.py`: ✅ Repository CRUD 测试通过
- `test_log_repository.py`: ✅ 日志 Repository 测试通过
- `test_database.py`: ✅ BotDatabase 连接测试通过

**模块测试**:
- `test_karma.py`: ✅ Karma 管理器测试通过
- `test_roll.py`: ✅ 掷骰功能测试通过
- `test_coc.py`: ✅ COC 角色测试通过

---

## 未来规划

1. **完成剩余迁移**: 按优先级完成高、中优先级任务
2. **性能基准测试**: 对比新旧 API 的性能差异
3. **数据迁移工具**: 考虑编写 JSON → SQLite 的迁移脚本 (当前不做)
4. **文档完善**: 为每个 Repository 编写使用示例
5. **废弃 DataManager**: 在所有迁移完成后评估移除

## 附录: 测试

运行数据层测试:

```bash
uv run pytest tests/core/data/ -v
```

测试结果: **86 passed, 36 skipped**
