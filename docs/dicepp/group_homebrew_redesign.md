# 群级私设系统重写 — 设计稿

> 状态：**设计征求评审**（不含代码）
> 作者：nubeslove + Claude
> 目的：解决现有 `.私设` / `.hb` 配置不便的问题，把私设升级为「群级附加数据库」+「群级宏」

---

## 目标与现状

### 用户痛点（现状）
- 现有 `.hb` / `.私设` 系列基于 xlsx 上传 → SQLite 转换的流程，**配置门槛高**
- 私设只能放数据，**没法放宏**（自定义指令）
- 私设数据库跟主查询库的关系是「替换」而非「附加」，跑团有时希望两者共存

### 目标（本次重写）
1. **私设即附加** — 群里 `.查询 xxx` 时，先扫公共 query DB（如 DND5E2024），再扫该群的附加 DB，**两边结果合并返回**（带来源标记）
2. **群级宏** — 主持人能在群里定义群级宏（不是用户级），整个群共享。例：`.hb 宏 我方阵营=队友A,队友B,队友C`
3. **配置零摩擦**：
   - admin WebUI 拖一个 xlsx 文件就能上传成群私设
   - 命令式 `.hb add 名称|内容|分类|标签` 在群里直接录入单条
   - 命令式 `.hb del 关键字` 删除
4. **群之间隔离** — 群 A 的私设对群 B 不可见；admin 后台也能复制/导出

---

## 数据模型设计

### 1. 群附加查询数据库

文件布局（沿用 α 的 `Paths.DATA_BOTS_DIR`）：
```
data/bots/<bot_id>/group_homebrew/
└── <group_id>/
    ├── main.db             # 默认私设数据库
    ├── npc.db              # 主持人可建多个分类 db
    └── meta.json           # 启用顺序、显示名、是否启用
```

**Schema**：完全复用 α 的 `core/data/query_store.py` 表结构（`data` + `redirect`），这样 query 查询引擎可以零修改地接入。

**meta.json 示例**：
```json
{
  "databases": [
    {"file": "main.db", "name": "主私设", "enabled": true, "priority": 10},
    {"file": "npc.db",  "name": "我家 NPC", "enabled": true, "priority": 20}
  ]
}
```

### 2. 群级宏

引入新 pydantic 模型 `GroupMacro`（区别于已有的 `UserMacro`）：
```python
class GroupMacro(BaseModel):
    group_id: str
    key: str
    raw: str
    args: list[str] = Field(default_factory=list)
    target: str
    command_split: str = ""
    creator_id: str = ""  # 创建者，用于审计
    created_at: datetime = Field(default_factory=datetime.now)
```

`BotDatabase` 加 `group_macro: Repository[GroupMacro]`，key 为 `(group_id, key)`。

Migration v5_group_homebrew 创建 `group_macro` 表。

---

## 查询引擎扩展

### 现有查询路径（α 当前）

```
.查询 xxx
  → query_command.process_msg
  → QueryStore.find_in(database=DND5E2024, term=xxx)
  → 命中 → 返回结果
```

### 新查询路径（重写后）

```
.查询 xxx in <群 G>
  → query_command.process_msg
  → 收集候选 db：
      1. BotConfig.query.private_database（默认主 db）
      2. data/bots/<bot_id>/group_homebrew/<G>/*.db (按 meta.json priority 排序)
  → 并发查每个 db
  → 合并 + 去重 + 按来源标记
  → 渲染输出：
       【DND5E2024】法术 火球术 ...
       【主私设】法术 火球术（霓石精家自创） ...
```

**核心改动**：`QueryStore.find_in` 接受一个 `db_names: list[str]` 而非单个 `database: str`。

为不破坏现有调用方，保留旧 `find_in(database=...)` 签名作为单 db 兼容入口。

---

## 宏执行扩展

### 用户宏 vs 群宏

`apply_user_macros()` 已经在 `dicebot.process_message` 入口集成（PR #47）。本次升级为：

```python
async def apply_user_and_group_macros(bot, user_id, group_id, text):
    # 1. 群级宏（如有）
    if group_id:
        gm = await bot.db.group_macro.list_by(group_id=group_id)
        for m in gm:
            text = apply_macro_once(m, text)
    # 2. 用户级宏
    return await apply_user_macros(bot, user_id, text)
```

**优先级**：群宏**先于**用户宏展开（因为群宏可以引用基础宏，用户宏可能引用群宏的输出）。

`dicebot.process_message` 中替换之前的调用：
```diff
- msg = await apply_user_macros(self, meta.user_id, msg)
+ msg = await apply_user_and_group_macros(self, meta.user_id, meta.group_id, msg)
```

---

## 用户面命令

| 命令 | 作用 |
|---|---|
| `.hb add 名称\|英文\|来源\|分类\|标签\|内容` | 群里直接录入一条私设条目（写入该群默认 db `main.db`） |
| `.hb del 关键字` | 删除条目（按名称匹配） |
| `.hb list [分类]` | 查看群私设条目（可按分类过滤，分页） |
| `.hb 宏 关键字 = 目标` | 定义群级宏 |
| `.hb 宏 del 关键字` | 删除群宏 |
| `.hb 宏 list` | 查看群宏列表 |
| `.hb db add <name>` | 新建分类 db（如 `npc.db`） |
| `.hb db list` | 列群下的 db |
| `.hb db enable/disable <name>` | 启停某个 db |
| `.hb 导入 <url>` | 从 URL 下载 xlsx 导入（保留兼容旧流程） |

所有 `.hb *` 命令要求 `permission_require=1`（群管理员/骰主）。

---

## admin WebUI 集成

WebUI 加新 tab **「私设」**（在「查询库」tab 旁）：

- 顶部下拉「选择群」（列实例下所有有私设的群）
- 中间是 **「条目」** 子 tab：跟「查询库」tab 类似，CRUD 一条条目
- 旁边 **「宏」** 子 tab：管理该群的 `GroupMacro`
- 顶部按钮：「📤 上传 xlsx」（拖拽或点击）触发 `POST /api/homebrew/<instance>/<bot>/<group>/upload` 后端把 xlsx 转 db

---

## 文件改动清单（实施时）

| 类型 | 文件 |
|---|---|
| 模型 | `core/data/models/extended.py` 加 `GroupMacro` |
| 模型导出 | `core/data/models/__init__.py` |
| Repository | `core/data/database.py` 加 `group_macro` |
| Migration | `core/data/migrations/v5_group_homebrew.py` 创建 `group_macro` 表 |
| Query 引擎 | `core/data/query_store.py` 增强 `find_in(db_names=...)` |
| 群宏引擎 | `module/common/macro_command.py` 加 `apply_user_and_group_macros` |
| dicebot 集成 | `core/bot/dicebot.py` process_message 切到新 helper |
| 群宏指令 | 部分合到现有 `.hb` 命令（hb_command.py） |
| 命令重写 | `module/query/homebrew_command.py` 大改 |
| admin 后端 | `src/dicepp_admin/homebrew_api.py` 新增 |
| admin 前端 | `static/admin.html` 加「私设」tab |
| 文档 | `docs/dicepp/group_homebrew.md` 用户指南 |

---

## 兼容性

- **现有 `.hb` 命令保留**：`.hb 导入` 等老指令继续可用，只是流程接到新引擎
- **现有 xlsx 私设迁移**：admin 启动时检测 `data/bots/<bot>/Homebrew/` 旧目录，提示用户一键迁移到新结构
- **数据库 schema**：v5 migration 只新增 `group_macro` 表，不动现有表
- **BotConfig.query.private_database** 保留，作为「主 db」，新私设是**附加**而非替代

---

## 工程量估算

| 阶段 | 工作量 |
|---|---|
| 数据层 + migration v5 | 1 小时 |
| query 引擎 `find_in` 重构（保兼容） | 2 小时 |
| `apply_user_and_group_macros` + dicebot 集成 | 1 小时 |
| `.hb` 命令族重写 | 半天 |
| admin 后端 `/api/homebrew/*` | 半天 |
| admin 前端「私设」tab | 半天 |
| 文档 + 旧 xlsx 迁移工具 | 半天 |
| **总计** | **~2.5 个工作日** |

---

## 设计决策（已确认）

| # | 决策点 | 最终方案 | 来源 |
|---|---|---|---|
| 1 | 数据库优先级 | **群私设在前**，同名条目以私设为准 | maintainer 反馈 |
| 2 | 群宏 vs 用户宏 | **先群宏后用户宏**（群宏铺路，用户宏个人定制） | 默认提案 |
| 3 | xlsx 字段 schema | **保留现有 6 字段**（名称/英文/来源/分类/标签/内容），不引入新字段 | 默认提案，避免破坏现有私设包 |
| 4 | 命令开头 | **`.hb` / `.私设` / `.房规` / `.homebrew` 全部并存**（HBExtCommand 同时接受） | 默认提案 |
| 5 | 跨群共享 | **不做**，每个群独立目录 `group_homebrew/<group>/` | maintainer 反馈 |
| 6 | 权限模型 | **群内本群管理员**（`permission_require=1`），不开放跨群远控 | 默认提案 |
| 7 | mode 切换影响 | **完全独立**，私设不会随 `.mode DND5E ↔ COC` 切换失效 | maintainer 反馈 |

---

## 实施进度

设计采纳后已拆三个实施 PR：

| PR | 主题 | 状态 |
|---|---|---|
| `feat/homebrew-data-layer` (#51) | GroupMacro 模型 + migration v5 + QueryStore.search 私设优先重写 + Paths.group_homebrew_dir | ✅ 已开 |
| `feat/homebrew-commands` (#52) | `.hb add/del/list/宏/db` 子指令 + 群宏 helper + dicebot 集成 | ✅ 已开 |
| `feat/homebrew-admin-ui` (#53) | admin 后台「私设」tab + `/api/homebrew/*` + xlsx 拖拽上传 | ✅ 已开 |

老 xlsx 迁移工具未做（v5 阶段决定保留旧 `.hb 导入` 命令兼容，旧目录里的 db 用户可手动复制到新位置 `data/bots/<bot>/group_homebrew/<group>/` 即可）。

---

🤖 Generated with [Claude Code](https://claude.com/claude-code)
