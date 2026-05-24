# β 版本独有功能合并指南

α 经过较大重构（Pydantic + Repository 模式），β 的代码（JsonObject + DataChunk 模式）**不能直接拷贝**到 α —— 导入路径全部失配。

本指南给出每个 β 独有模块的：
1. 它在 β 里依赖了什么 core 设施
2. α 里对应的等价物
3. 重写工作量评估

β 模块源文件已归档到 `docs/beta_merge_reference/`，按子目录分类，方便对照原文重写。

---

## 合并优先级表（第二轮更新）

| 模块 | 价值 | 工作量 | 状态 |
|---|---|---|---|
| `DND5E2024.db` (DND5E 2024 规则查询库) | ⭐⭐⭐⭐⭐ | 已完成 | ✅ 已复制到 `content/queries/` |
| 查询数据库管理 UI | ⭐⭐⭐ | 已完成 | ✅ admin 后台「查询库」tab，可对 content/queries/*.db 增删改查 |
| 宏指令 (define) | ⭐⭐⭐⭐ | 已完成 | ✅ `.define` 命令已注册（增/删/查），存储在 `macros` 表。展开执行需 hook 到 dicebot |
| 变量指令 (.set/.get/.del) | ⭐⭐⭐ | 已完成 | ✅ `.set` `.get` `.del` 已注册，支持 = / + / - 操作和骰子表达式赋值 |
| 点数指令 (.point) | ⭐⭐ | 已完成 | ✅ `.point` 已注册，跨天自动补发，master 可 set/get |
| COC 角色卡 | ⭐⭐⭐⭐ | 已取消 | ❌ β 的 character/coc/ 其实是 dnd5e 的复制粘贴（字段、技能、检定全一样），未实现真正 COC7 规则。α 的 DND5E 已覆盖。真要支持 COC 需从零实现 9 特征/D100 体系 |
| log_db (日志落 SQLite) | ⭐⭐⭐ | 不合并 | α 已有 LogRepository（admin 后台日志检索基于它） |
| bot_presence (在线状态) | ⭐⭐ | 不合并 | admin 内置实例状态展示已覆盖 |
| 先攻 entity/list 拆分 | ⭐ | 不合并 | α initiative 已能工作，没必要重构 |
| DiceHub 加密/数据层 | ⭐⭐ | 待评估 | 看 α 是否已有等价物 |

---

## 1. COC 角色卡（推荐先做）

**源文件**：`docs/beta_merge_reference/character_coc/`
- `__init__.py`, `ability.py`, `character.py`, `health.py`, `hp_command.py`, `money.py`, `spell.py`
- 注：β 的 COC 是参照 DND5E 的拆分结构做的，几乎对称

### β 依赖
```python
from core.data import JsonObject, custom_json_object       # ❌ α 没有
from core.data import DataChunkBase, custom_data_chunk     # ❌ α 没有
from core.command.const import *                            # ✅ α 有
from core.command import UserCommandBase, custom_user_command  # ✅ α 有
from module.roll import exec_roll_exp, RollDiceError       # ✅ α 有
```

### α 中的等价物
- `JsonObject` → α 使用 `pydantic.BaseModel`（看 `core/data/models/character.py`）
- `custom_data_chunk` + `DataChunkBase` → α 使用 `Repository[T]`（看 `core/data/repository.py`）
- 角色卡数据落库方式不一样：
  - β：`DataChunk` 序列化为 JSON 存进 bot_data
  - α：`Repository` 直接管理 SQLite 表，data 字段是 pydantic model 的 JSON

### 重写步骤
1. 在 `core/data/models/` 新建 `character_coc.py`，定义 `COCCharacter(BaseModel)`，字段照搬 β 的 `DNDCharInfo` 但改成 pydantic
2. 在 `core/data/database.py` 的 `BotDatabase` 加一个 `characters_coc: Repository[COCCharacter]` 属性
3. 在 `core/data/migrations/` 加一个 v3_coc_character.py 创建表
4. 在 `src/plugins/DicePP/module/character/coc/` 新建命令文件，照搬 β 的命令逻辑，把 DataChunk 操作改成 Repository 操作

预计 1-2 天工作量。

---

## 2. 宏指令（define）

**源文件**：`docs/beta_merge_reference/common/macro_command.py` + `docs/beta_merge_reference/core_bot/macro.py`

### β 依赖
```python
from core.bot import Bot, BotMacro, MACRO_COMMAND_SPLIT   # ❌ α 的 core.bot 没暴露
from core.data import DataManagerError, DC_MACRO          # ❌ α 没这个 DC
```

### 重写步骤
1. 把 `core_bot/macro.py` 改写为 `core/bot/macro.py`，把里面的 `JsonObject` 改成 `pydantic.BaseModel`
2. 在 `core/data/models/` 新建 `macro.py`（如 `UserMacro(BaseModel)`）
3. 在 `BotDatabase` 加 `macros: Repository[UserMacro]`
4. 在 `module/common/` 新建 `macro_command.py`，照搬 β 的命令注册逻辑

预计 1 天工作量。

---

## 3. 变量指令（.var）

**源文件**：`docs/beta_merge_reference/common/variable_command.py` + `docs/beta_merge_reference/core_bot/variable.py`

跟宏指令几乎是孪生关系，重写步骤同上。

α 的 `core/data/models/extended.py` 里已有 `UserVariable`，可以复用。

预计半天。

---

## 4. 点数指令（.point）

**源文件**：`docs/beta_merge_reference/common/point_command.py`

跟变量类似，但更简单（只是计数器）。预计半天。

---

## 5. log_db

**源文件**：`docs/beta_merge_reference/common/log_db.py`

α 已有 `core/data/log_repository.py`，提供了完整的 logs/records 表管理（admin 后台的日志检索已经基于它）。

**建议：不合并 β 的 log_db**，α 的 LogRepository 已经够用。

如果非要合并，把 β 的 `DATA_PATH` 引用改成 α 的 `Paths.DATA_DIR` 即可。

---

## 6. bot_presence

**源文件**：`docs/beta_merge_reference/utils/bot_presence.py`

依赖 `from core.config import DATA_PATH`（β 风格），α 没有这个变量。

**建议：不直接合并**。α 的管理后台已经通过 `instance_manager.is_running()` 提供了实例状态展示。

如果想保留"按 QQ 号显示在线状态"，把它改写到 `utils/` 下，把 `DATA_PATH` 换成 `Paths.DATA_DIR`。

---

## 7. DiceHub 加密 / 数据层

**源文件**：`docs/beta_merge_reference/dice_hub/data.py` + `encrypt.py`

α 已有 `module/dice_hub/api_client.py` + `manager.py`。看是否有加密需求，按需移植 `encrypt.py` 的工具函数。

`data.py` 主要是数据结构定义，对照 α 的 dice_hub 改一下即可。

---

## 8. 先攻 entity/list 拆分

**源文件**：`docs/beta_merge_reference/initiative/`

α 的 `module/initiative/initiative_command.py` 自身已经实现先攻逻辑。β 把 entity/list 拆成单独的 dataclass，是更"工程化"的做法。

**建议：不合并**。α 当前实现能跑，没必要重构。

---

## 9. query_database 管理（DND5E2024.db 配套）

**源文件**：`docs/beta_merge_reference/query/query_database.py`

α 已有 `module/query/query_command.py` 和 `homebrew_command.py`，但缺乏对 SQLite 查询库（即 `content/queries/DND5E2024.db`）的 CRUD 管理界面。

**建议**：α 的 query_command 应该已经能从 `content/queries/*.db` 读数据（如果它本来就支持的话）；如果需要可视化编辑数据库，可以在管理后台的「SQLite 浏览器」tab 里直接打开 `DND5E2024.db`（admin 后台已经支持任意 SQLite CRUD，把数据库路径传过去即可）。

---

## 总结：什么"白送"了

合并这次直接到位的：
- ✅ **DND5E2024 规则查询库**（10MB 数据，已落到 `content/queries/`）
- ✅ **可视化后台**全套（取代 β 后台、设计更轻量）
- ✅ **一键部署 + 多实例管理**（取代 β 整合包，5MB 包代替 500MB）

需要 1-3 天手工重写的：
- COC 角色卡（DND5E2024 已有数据，但 COC 还是空白）
- 宏 / 变量 / 点数指令

可以不合并（α 已有等价物）：
- log_db、initiative 拆分、bot_presence
