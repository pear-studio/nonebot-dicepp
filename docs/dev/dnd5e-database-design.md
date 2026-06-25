# DND5eDatabase 设计文档

本文记录 DicePP 从 5etools-cn 结构化 JSON 数据构建全新 D&D 5e 查询数据库的设计决策。

## 目标

- 以 [5etools-cn](https://github.com/tjliqy/5etools-cn) 的结构化 JSON 数据替代当前 `content/queries/DND5E*.db` 的纯文本数据。
- 深度结构化核心类别（法术、怪物、物品、专长），支持精确字段筛选，为自动战斗、随机 NPC、LLM 查询等高级功能提供数据基础。
- 保持用户侧查询体验简单：`.查询 火球术` 返回可读文本，复杂结构对用户透明。
- 同时服务查询和随机生成两个子系统。

## 不纳入

- 私设 (homebrew) 数据支持，留待后续迭代。
- 版本差异自动合并标注（如 "2014 版和 2024 版逐字段 diff"），当前方案仅在同一表中保留两个版本的条目，用 `edition` 列区分。
- `content/excel`、`content/decks`、`content/characters` 的重构。
- 生产环境部署与分发（导入管线暂为开发者手动脚本，独立仓库分发留待后续）。

## 数据源

### 主数据源：5etools-cn

仓库：`https://github.com/tjliqy/5etools-cn`

- ~502 个 JSON 文件，约 13,000+ 条目，覆盖 ~40 个数据类别。
- 格式为 5etools 标准 JSON schema：每个文件包含一个实体数组（如 `"spell": [...]`），条目包含结构化字段和递归 `entries` 文本树。
- 中文翻译覆盖率：法术 99.3%、专长 100%、技能 100%，怪物 28.3%、物品 47%。所有条目均有中文名 (`name`)，英文原名保留在 `ENG_name` 字段。
- 数据使用 CC BY-NC-SA 4.0 许可（中文翻译部分），源代码 MIT 许可。

### 参考数据源

- 英文原版 5etools (mirror-3)：`https://github.com/5etools-mirror-3/5etools-src`，用于对比数据完整性和翻译缺口。
- wiki-dnd-parser：`https://github.com/pttsw/wiki-dnd-parser`，5etools → MediaWiki 转换管道，其 `copyResolver.ts` 是 `_copy` 继承解析的参考实现。

## 架构概览

```
5etools-cn JSON 数据
  → 导入脚本 (手动)
  → DND5E.db (单文件 SQLite)
     ├── spell         (专用表, ~40 列)
     ├── monster       (专用表, ~75 列)
     ├── item          (专用表, ~50 列)
     ├── feat          (专用表, ~25 列)
     ├── dnd5e_entries (通用 JSON 表, 其余 ~30 类别)
     └── import_manifest (导入元数据)
  → 查询层
     ├── LIKE 搜索 + 精确匹配短路
     ├── 版本过滤 (edition 列)
     └── _copy 继承解析 (查询时)
  → 渲染层
     ├── 核心类别专用渲染器 (spell/monster/item/feat)
     ├── 通用 entries → 纯文本渲染器 (~500 行)
     └── 配置驱动头部字段渲染 (每类别 ~10 行配置)
  → 用户展示 (QQ 消息纯文本)
```

## 核心设计决策

### 1. 数据库文件

单文件 `DND5E.db`，位于 `content/queries/`。所有类别在同一文件中，方便分发、备份和替换。

### 2. 表结构策略

- **核心类别**（法术、怪物、物品、专长）：专用表，每表 20-75 列，支持结构化 WHERE 筛选和随机生成。
- **其他类别**（~30 种：神祇、背景、技能、语言、状态、载具、陷阱、奖励、灵能等）：一张通用 `dnd5e_entries` 表，JSON 列存储完整数据，配置驱动渲染。

取舍理由：28/33 个内容类别共用同一套 `entries` 递归树格式，区别仅在于 entries 正文前显示哪些头部字段。通用渲染器覆盖 85% 的类别，只有 5 个不走 entries 格式的类别（食谱、卡组、生命事件、自制图案、表格）需要定制渲染函数（共 ~200 行）。

### 3. 版本处理

- 每张表设 `edition` 列，值为 `"2014"`、`"2024"` 或 `NULL`（通用条目）。
- 导入时根据来源文件推断版本：`spells-phb.json` → 2014，`spells-xphb.json` → 2024。无明确版本标记的扩展书条目留 NULL。
- 66% 的共享法术在两个版本间无机械变化，85% 的怪物没有版本标记。大多数数据不需要版本区分。
- **默认模式**：混合查询优先返回 2014 版。如果存在 2024 版，结果中显示 `🔄2024` 标记。
- **版本切换**：`.查询 火球术 v2024` 指定查询特定版本。
- 不实现逐字段版本 diff 合并标注，保持导入和存储层简单。

### 4. `_copy` 继承解析

- 采用"查询时解析"策略。原始 JSON 直接入库（含 `_copy` 引用），不展开为完整条目。
- 搜索层预计算一个展平索引（CR、类型、体型、伤害免疫等关键字段），搜索不触发继承解析。
- 展示层在单条查询时按需解析 `_copy` 链（最大深度 2，平均 0.19），结果缓存于内存 LRU。
- 参考实现：wiki-dnd-parser 的 `copyResolver.ts`（~1064 行，支持 12 种 `_mod` 操作），可 port 到 Python。

### 5. 搜索策略

- 使用 SQLite LIKE 替代当前 REGEXP 方案。LIKE 是原生 C 实现，实测快 8-10 倍，对中文子串匹配效果良好。
- 精确名称匹配走索引短路（`WHERE name = ?`），<1ms。
- FTS5 暂不引入：内置分词器（unicode61、trigram）对中文部分匹配均不可用。16K 行规模下 LIKE 性能已足够。

### 6. 渲染架构

**通用 entries 渲染器** (~500 行 Python)：
- 递归处理 5etools 的 entries 树结构：`string`、`entries`、`list`、`table`、`inset`、`insetReadaloud`、`quote`、`variant`、`item`、`inline`、`link`、`image` 等类型。
- `{@tag}` 替换为可读文本（`{@spell 火球术}` → `火球术`，`{@dice 2d6+3}` → `2d6+3`，`{@b 文本}` → `**文本**`）。

**核心类别专用渲染器**：
- 每种核心类别写一个渲染函数，格式化该类别特有的 statblock 结构。
- 法术：环位/学派/施法时间/射程/成分/持续时间 + entries 正文。
- 怪物：属性/AC/HP/速度/豁免/感官/特性/动作/传奇动作。

**配置驱动渲染器** (~10 行配置/类别)：
- 其他类别通过配置 dict 声明"显示哪些头部字段"，复用通用 entries 渲染器处理正文。
- 示例：神祇配置 `[("pantheon", "神系"), ("alignment", "阵营"), ("domains", "领域")]`。

### 7. 输出格式

- 保持现有纯文本 statblock 风格，不引入卡片或表格格式。
- 双版本条目显示版本标记：`火球术 🔄2024`。
- 单版本条目无版本标记。
- 条目开头显示名称、来源、关键属性，正文为 entries 展平的纯文本。

### 8. 未翻译条目的处理

- 全部入库，不因翻译率低而丢弃数据。
- 翻译率低的条目（如怪物 28.3%）：数字字段（HP、AC、属性值、CR 等）天然跨语言，直接可用；`entries` 描述文字若为英文则原文显示。
- 入库时标记 `translator` 字段，展示层可标注"该条目尚未完整翻译"。

### 9. 导入管线

- 第一版为手动脚本（如 `python -m dicepp.build_dnd5e_db`），开发者跑一次更新一次。
- 全量重导策略：`DELETE FROM table` + 批量 `INSERT`。SQLite 上 ~13K 条目预计 <10 秒。
- 中立 `import_manifest` 表记录导入时间、文件 hash、条目数，为后续增量更新预留空间。
- 独立仓库分发留待后续迭代。

### 10. 与现有系统的关系

- `DND5eDatabase` 替代现有 `QueryStore` + `DND5E*.db`。
- 上层指令保持简洁：`.查询 火球术`（名称搜索）、`.查询 v2024 火球术`（指定版本）。
- 随机生成和牌库功能从 `DND5eDatabase` 的结构化字段直接筛选，不再依赖 xlsx 手工维护数据。
- 私设数据库覆盖机制暂不迁移，留待后续。

## 数据类别清单

### 核心表（专用 schema + 专用渲染器）

| 表名 | 5etools 文件 | 条目数 | 字段数 | 复杂度 |
|---|---|---|---|---|
| `spell` | `data/spells/spells-*.json` (17 文件) | 936 | ~40 | 高 |
| `monster` | `data/bestiary/bestiary-*.json` (107 文件) | 4,528 | ~75 | 极高 |
| `item` | `data/items.json`, `items-base.json`, `magicvariants.json` | 2,428+230+214 | ~50 | 极高 |
| `feat` | `data/feats.json` | 276 | ~25 | 高 |

### 通用表（`dnd5e_entries`，JSON 列 + 配置驱动渲染）

| 类别 | 条目数 | 渲染配置复杂度 |
|---|---|---|
| Deity | 563 | ~6 头部字段 |
| Reward | 277 | ~3 头部字段 |
| Recipe | 241 | 定制渲染函数 (~30 行) |
| VariantRule | 230 | ~3 头部字段 |
| OptionalFeature | 213 | ~4 头部字段 |
| Language | 192 | ~4 头部字段 |
| LegendaryGroup | 187 | ~2 头部字段 |
| Background | 161 | ~6 头部字段 |
| Race + Subrace | 160+98 | ~6 头部字段 |
| Trap/Hazard | 110 | ~3 头部字段 |
| Adventure (meta) | 99 | 仅索引 |
| Vehicle + Upgrade | 70 | statblock 风格 |
| Condition/Disease | 64 | ~2 头部字段 |
| Book (meta) | 62 | 仅索引 |
| Facility (Bastion) | 61 | ~5 头部字段 |
| Psionic | 52 | ~4 头部字段 |
| Action | 48 | ~3 头部字段 |
| Cult/Boon | 42 | ~4 头部字段 |
| Object | 37 | statblock 风格 |
| Skill | 36 | ~1 头部字段 |
| Deck/Card | 34+711 | 定制渲染函数 (~15 行) |
| Sense | 8 | 零头部字段 |
| 其余 ~10 小类 | ~80 | 零或极少头部字段 |

## 搜索索引表设计

为支持跨类别关键词搜索和 _copy 继承条目的筛选，预计算一张搜索索引表：

```sql
CREATE TABLE search_index (
    name TEXT NOT NULL,           -- 中文名
    name_en TEXT NOT NULL,        -- 英文名
    category TEXT NOT NULL,       -- "spell", "monster", "item", "feat", "deity", ...
    source TEXT NOT NULL,         -- 来源代码
    edition TEXT,                 -- "2014", "2024", NULL
    search_text TEXT NOT NULL,    -- 预计算的可搜索文本 (名称 + 字段值 + entries 展平)
    key_fields TEXT,              -- JSON: 预展平的关键筛选字段 (CR, level, type, rarity 等)
    entry_id TEXT NOT NULL,       -- 关联到具体表的 rowid 或 JSON 表 id
    translator BOOLEAN DEFAULT 0  -- 是否有中文翻译标记
);
CREATE INDEX idx_search_name ON search_index(name);
CREATE INDEX idx_search_category ON search_index(category);
CREATE INDEX idx_search_edition ON search_index(edition);
```

`_copy` 条目的 `key_fields` 在导入时预展平（继承父条目属性 + 覆盖自身 `_mod`），确保搜索 "find CR 15+ dragons" 时不需要实时解析继承链。

## 实施阶段

### Phase 1：基础设施 + 法术

- 实现通用 entries 渲染器。
- 建 `spell` 表 schema + 导入脚本 + 专用渲染器。
- 建 `search_index` 表 + 基本搜索。
- 指令：`.查询 火球术` 走新数据源。

### Phase 2：怪物

- `monster` 表 schema + 导入 + 渲染器。
- Python port of `_copy` 解析器。
- 搜索索引支持 `_copy` 条目。

### Phase 3：物品 + 专长

- `item` 和 `feat` 表 + 渲染器。
- 配置驱动渲染器框架 + 通用 `dnd5e_entries` 表。

### Phase 4：其余类别 + 随机生成接入

- 导入其余 ~30 类别到 `dnd5e_entries`。
- 配置各分类的头部字段。
- 5 个特殊类别的定制渲染器。
- 随机生成从 xlsx 切换到结构化字段筛选。

### Phase 5：版本与模式切换

- `edition` 列 + 版本过滤逻辑。
- `v2024` 查询参数。
- 版本标记展示。

## 参考

- 5etools-cn: https://github.com/tjliqy/5etools-cn
- 5etools-en-src (mirror-3): https://github.com/5etools-mirror-3/5etools-src
- wiki-dnd-parser: https://github.com/pttsw/wiki-dnd-parser
- 数据对比脚本和分析结果：[本次设计讨论的 subagent 报告]
- `docs/dev/data-architecture-3.0.md` — DicePP 3.0 数据架构（本文档的父文档）
