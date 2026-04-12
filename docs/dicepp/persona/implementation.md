# Persona 模块 — 实现手册

> nonebot-dicepp 人格化 AI 系统 · 技术实现指南
> 2026-04-08
> 更新时间: 2026-04-10

---
---

## 修订记录

### 2026-04-10 阶段重划

根据实际开发情况调整阶段划分：

| 原计划 | 新计划 | 说明 |
|--------|--------|------|
| Phase 1 (4天) | Phase 1 (6天，已完成) | 包含原 Phase 2a/2b/2c，这些功能在实现基础对话时已完成 |
| Phase 2 (2天) | Phase 2 (5天) | 仅保留 2d 时间衰减，其余已完成 |
| Phase 3 (4天) | 合并到 Phase 2 | 主动消息、群聊观察等功能合并到新的 Phase 2 |
| Phase 4+ | Phase 3+ | 编号顺延 |

**背景**: 在实现 Phase 1 的过程中发现好感度系统（2a/2b/2c）与基础对话紧密耦合，提前实现反而更自然。因此将原计划 Phase 2 的核心功能合并到 Phase 1，时间衰减和主动消息合并为新的 Phase 2。

---

## 一、模块文件结构

```
src/plugins/DicePP/module/persona/
├── __init__.py                    # 模块注册（import command 触发装饰器）
├── command.py                     # PersonaCommand — DicePP 命令入口
├── orchestrator.py                # PersonaOrchestrator — 核心编排层
│
├── character/
│   ├── models.py                  # Character, CharacterBook, LoreEntry
│   └── loader.py                  # 从 YAML 加载角色卡
│
├── agents/
│   ├── persona_agent.py           # 主人格 Agent（生成对话回复）
│   ├── scoring_agent.py           # 评分 Agent（批量分析好感度变化）
│   └── event_agent.py             # 事件 Agent（角色生活事件生成）
│
├── llm/
│   ├── client.py                  # AsyncOpenAI 封装
│   └── router.py                  # LLMRouter — 多模型路由 + 配额 + 用户 Key
│
├── memory/
│   └── context_builder.py         # ContextBuilder — 四层记忆 → messages 列表
│
├── game/
│   ├── relationship.py            # RelationshipState（4 维好感度）
│   └── decay.py                   # 时间衰减计算
│
├── proactive/
│   ├── scheduler.py               # ProactiveScheduler — 定时问候 + 事件分享
│   └── character_life.py          # CharacterLife — 角色生活模拟（双 Agent）
│
└── data/
    ├── models.py                  # 所有 Pydantic 数据模型
    ├── store.py                   # PersonaDataStore — 统一数据访问层
    └── migrations.py              # 数据库表创建脚本
```

---

## 二、DicePP 集成要点

### 2.1 命令注册方式

PersonaCommand 继承 `UserCommandBase`，用 `@custom_user_command` 装饰器注册。装饰器在 import 时自动将类写入全局注册表，Bot 启动时实例化。

关键生命周期方法：
- `delay_init()` — Bot 初始化完成后调用，此时可访问 config 和 db
- `can_process_msg()` — 判断是否处理消息（@bot / .ai 前缀 / 群聊旁听）
- `process_msg()` — 异步处理消息，返回 `List[BotCommandBase]`
- `tick()` — 每秒调用，用于主动消息调度（内部节流到 60 秒）。实现为 **单槽异步任务**：同一时刻最多一个未完成的 `orchestrator.tick()`，属 **at-most-once** 语义；进程崩溃或任务被取消时可能少发一轮主动消息，更强保证需发件箱等机制。
- `tick_daily()` — 每天调用，用于日记总结

### 2.2 配置注入

在 `core/config/pydantic_models.py` 的 BotConfig 中新增 `persona_ai: PersonaConfig`。

配置优先级（高→低）：环境变量 `DICE_PERSONA_*` → `config.local.json` → `config.json`

### 2.3 数据库共享

使用现有 `bot_data.db`，通过 `self.bot.db` 获取 aiosqlite 连接。在 `delay_init()` 中调用 `PersonaDataStore.ensure_tables()` 创建新表。`PersonaDataStore` 写入的各类时间戳（消息、白名单、评分日志、群活跃、观察、日记与事件等）与 `persona_ai.timezone` 下的 `persona_wall_now` 对齐，避免容器 UTC 与业务时区混用导致日界偏差。

### 2.4 消息发送

所有输出通过返回 `BotSendMsgCommand(bot_id, text, [port])` 实现。port 分 `PrivateMessagePort` 和 `GroupMessagePort`。

### 2.5 异步任务

`tick()` 是同步方法，LLM 调用需要异步。通过 `self.bot.register_task(async_func, is_async=True, timeout=N)` 注册异步任务。

---

## 三、分阶段实施细节

### Phase 1: 骨架与基础对话 + 用户档案 + 好感度系统（已完成）

> **状态**: 已完成。包含原计划 Phase 2a/2b/2c（好感度系统核心功能），这些功能在实现 Phase 1 时已完成。
>
> **实际工作量**: 约 6 天（原 4 天 + Phase 2 核心功能 2 天）

---

#### Phase 1a: 模块骨架

**目标**：`.ai ping` 能返回 "pong"，验证命令注册成功

**任务**：
1. 创建 `module/persona/` 目录结构（空 `__init__.py`）
2. 编写 `command.py` 最小 PersonaCommand — `can_process_msg` 识别 `.ai`，`process_msg` 返回固定文本
3. 在 `module/__init__.py` 中 `import module.persona`
4. 编写 `data/models.py` — PersonaConfig 最小版（enabled, character_name, whitelist_enabled）
5. 在 `core/config/pydantic_models.py` 的 BotConfig 中添加 `persona_ai` 字段
6. 注释掉原 `import module.llm`

**验证**：启动 bot，发送 `.ai ping`，收到回复

---

#### Phase 1b: 角色系统

**目标**：角色卡 YAML 加载成功

**任务**：
1. 定义 `character/models.py` — Character 数据模型
   - 兼容 SillyTavern V2 的标准字段：name, description, personality, scenario, first_mes, mes_example, system_prompt
   - CharacterBook + LoreEntry（世界书，Phase 6 使用）
   - `extensions.persona` 字段：persona 模块专用扩展（见下方）
2. 编写 `character/loader.py` — 从 YAML 文件加载 Character
   - 路径：`{character_path}/{character_name}.yaml`
   - 使用 PyYAML（DicePP 已有依赖）
3. 创建示例角色卡 `content/characters/default.yaml`
4. 在 `delay_init()` 中加载角色卡，打印角色名

**角色卡完整字段示例**（`default.yaml`）：

```yaml
# ── SillyTavern V2 标准字段 ──────────────────────
name: "苏晓"
description: |
  27岁的出版社编辑，温柔而细腻。白天在出版社处理稿件，
  晚上喜欢读书和听雨。养了一只叫"墨墨"的橘猫。
personality: |
  温柔体贴，偶尔迷糊。对文学有浓厚兴趣，记性很好，
  朋友说过的话她都会记在心里。
scenario: |
  现代都市，苏晓是一家中型出版社的编辑，生活平静但充实。
first_mes: |
  啊，是你呀。我正好在整理稿子，有点累了……你今天过得怎么样？
mes_example: |
  <START>
  {{user}}: 最近怎么样？
  {{char}}: 还不错，就是稿子有点多。对了你上次说的那本书我找到了，真的很有意思！
system_prompt: |
  你是苏晓，一个温柔细腻的出版社编辑。说话自然亲切，
  记住用户说过的每一件事，会在适当时候提及。不承认自己是AI。

# ── 世界书（Phase 6）────────────────────────────
character_book:
  entries:
    - keys: ["墨墨", "橘猫"]
      content: "苏晓的猫，2岁橘猫，很黏人，喜欢趴在稿子上睡觉。"
      enabled: true

# ── Persona 模块扩展字段 ─────────────────────────
extensions:
  persona:
    # 好感度初始值（0-100）
    initial_relationship: 30

    # 好感度区间标签（6个列表，依次对应 0-10/10-20/20-40/40-60/60-80/80-100）
    # 留空则使用系统默认：["厌倦","冷淡","疏远","友好","亲近","亲密"]
    warmth_labels: ["漠然", "疏冷", "普通", "友好", "亲近", "心意相通"]

    # 世界观（System Agent 生成生活事件时的背景设定）
    world: "现代都市出版社，工作节奏适中，同事友好，偶有作者截稿压力"

    # 生活模拟
    daily_events_count: 5
    event_day_start_hour: 8    # 活跃时段起始（小时）
    event_day_end_hour: 22     # 活跃时段结束（小时）
    event_jitter_minutes: 60   # 每个时间槽的随机抖动范围（±分钟）

    # 定时触发事件（time_range 内随机时刻生成，不保证发给所有用户）
    scheduled_events:
      - type: "wake_up"
        time_range: "07:00-07:30"
      - type: "morning_greeting"
        time_range: "08:00-08:30"
      - type: "lunch_break"
        time_range: "12:00-13:00"
      - type: "off_work"
        time_range: "18:00-19:00"
      - type: "night_reading"
        time_range: "21:00-22:00"
```

**数据模型要点**：
- `warmth_labels` 留空时 loader 补充系统默认值，不要求角色卡必填
- `mes_example` 中的 `{{user}}` `{{char}}` 模板变量在组装时替换
- `extensions.persona` 以外的字段保持 SillyTavern V2 兼容，可复用社区角色卡

---

#### Phase 1c: LLM 路由

**目标**：能通过 LLMRouter 调用 LLM 并获得回复

**任务**：
1. 编写 `llm/client.py` — AsyncOpenAI 封装
   - 超时处理（asyncio.wait_for）
   - 异常捕获（TimeoutError, APIError）→ 友好错误消息
2. 编写 `llm/router.py` — LLMRouter
   - 初始化：根据 PersonaConfig 创建 primary 和 auxiliary 两个客户端
   - `generate(messages, model_tier, user_id, timeout)` 统一接口
    - **并发控制**：`asyncio.Semaphore(max_concurrent_requests)` 限制同时发出的 LLM 请求数
      - 超出并发数的请求自动排队等待，用户只会感到"回复慢了一点"而不是报错
    - **指数退避重试**：对 429 RateLimitError、503 ServiceUnavailable、TimeoutError 自动重试
      - 第 1 次重试：等待 2 秒
      - 第 2 次重试：等待 4 秒
      - 第 3 次重试：等待 8 秒
      - 最多 3 次，之后抛出异常返回错误提示
      - 日志记录每次重试原因和等待时间
   - Phase 1 简化版：不做配额检查，不做用户 Key 路由
3. 扩展 PersonaConfig：primary_api_key, primary_base_url, primary_model, auxiliary_*, timeout, max_concurrent_requests(默认 2)

**model_tier 逻辑**：
- `"primary"` → 用主模型客户端和模型名
- `"auxiliary"` → 用辅助模型客户端和模型名
- auxiliary 的 key/url 留空时自动复用 primary

---

#### Phase 1d: 基础对话

**目标**：`.ai 你好` 返回角色化回复，支持多轮上下文

**任务**：
1. 编写 `orchestrator.py` Phase 1 简化版
   - `initialize()` — 加载角色卡 + 初始化 LLMRouter
   - `chat(user_id, group_id, message, nickname)` — 核心对话方法
2. chat 流程（Phase 1 简化）：
   - **白名单检查**（Phase 1g 完成后接入）：群聊时检查 group_id 是否在群白名单；私聊时检查 user_id 是否在用户白名单；不在则静默忽略
   - **消息去重**：检查上一条用户消息的内容和时间，5 秒内完全相同的消息直接忽略（防手抖/网络重试）
   - 从 PersonaDataStore 加载最近 N 轮对话历史
   - 组装 messages：[system_prompt] + 历史 + 当前消息
   - 调用 LLMRouter.generate(model_tier="primary")
   - 保存用户消息和角色回复到 DB
   - 返回回复文本
3. 首次对话检测：**消息历史为空**时返回 `first_mes`（而非检查 RelationshipState，这样 `.ai clear` 后会重新触发首次问候）
4. 特殊命令处理：`.ai clear`（清空对话历史，不清除好感度 — 调试用）、`.ai status`（显示状态）

**system_prompt 组装**（Phase 1 简化版）：
- 角色卡的 system_prompt
- 示例对话（mes_example）
- 暂不注入好感度/记忆/日记等（Phase 2+ 加入）
- **Prompt caching 友好**：角色卡 system_prompt 始终放在 messages[0]（system role），内容固定不变，主流 API 自动缓存

---

#### Phase 1f: 白名单与访问控制

**目标**：AI 对话默认关闭；用户私聊用口令自助加入；群聊由管理员直接添加；无口令时白名单不激活

**DB 表**：

1. `persona_whitelist`（id TEXT, type TEXT, joined_at TEXT, PRIMARY KEY(id, type)）
   - type = `'user'`：用户白名单，id = user_id
   - type = `'group'`：群白名单，id = group_id
2. `persona_settings`（key TEXT PRIMARY KEY, value TEXT）— 运行时 KV
   - key = `code`，value = 口令明文（NULL 或不存在 = 未设置）

**PersonaDataStore 新增方法**：

```python
is_user_whitelisted(user_id) → bool
is_group_whitelisted(group_id) → bool
add_user_to_whitelist(user_id)
add_group_to_whitelist(group_id)
remove_from_whitelist(entry_id, entry_type)   # entry_type='user'|'group'
list_whitelist() → list[dict]     # [{id, type, joined_at}, ...]
clear_whitelist()
get_code() → str | None
set_code(code: str)               # None = 清除口令
```

**白名单激活条件**：

```
whitelist_active = (whitelist_enabled=True) AND (get_code() is not None)

if not whitelist_active:
    所有用户/群均可访问 AI（视为开发/测试状态）
    已有白名单数据保留，不清除
```

**`can_process_msg()` 访问控制逻辑**：

```
消息到达
├── .ai join        → 任何人可执行（仅私聊）
├── .ai admin *     → 仅管理员，无需白名单
└── 其余 AI 命令 / @mention
    ├── whitelist_active = False → 放行
    ├── 私聊 → 检查 user_id in 用户白名单
    └── 群聊 → 检查 group_id in 群白名单
        （群聊不检查用户白名单：群级别管控优先）

群聊旁听（未被@，静默记录）→ 不受白名单限制
```

**命令实现**：

用户命令 `.ai join <口令>`（仅私聊）：
- 白名单未激活（无口令）→ 回复"AI 功能暂未开放，请联系管理员"
- 口令不匹配 → 回复"口令不对哦~"（不提示接近正确与否）
- 口令匹配且未在白名单 → 加入用户白名单，回复"已开启 AI 对话，开始聊天吧！"
- 已在白名单 → 回复"你已经在啦~"
- 群聊中发送 → 回复"请私聊发送此命令"（不暴露口令相关信息）

管理员命令（`is_admin(user_id)` 鉴权）：
- `.ai admin code <新口令>` — 设置/更新口令，不踢出现有成员，回复"已更新，白名单功能已激活"
- `.ai admin code clear` — 清除口令，白名单停用但数据保留，回复"口令已清除，白名单功能已停用"
- `.ai admin whitelist` — 显示用户白名单 + 群白名单（含加入时间，> 20 条时截断并提示总数）
- `.ai admin whitelist add group <group_id>` — 将群加入白名单，回复"已添加"
- `.ai admin whitelist remove <user_id>` — 移除用户
- `.ai admin whitelist remove group <group_id>` — 移除群
- `.ai admin whitelist clear` — 二次确认：先回复"确认清空？再发 `.ai admin whitelist confirm` 执行"
- `.ai admin whitelist confirm` — 执行清空

**PersonaConfig 相关**：
- `whitelist_enabled: bool = true` — 全局开关，设为 false 完全跳过白名单检查（开发用）
- 口令不放配置文件，只存 DB，避免意外提交到版本控制

**验证**：
1. 无口令时任何人都能用 AI（`whitelist_active = False`）
2. 管理员设口令后，未加入用户 `.ai 你好` → 静默忽略
3. 用户私聊输入正确口令 → 加入用户白名单 → `.ai 你好` 正常回复
4. 管理员添加群 → 该群 @ bot 正常回复，非白名单群仍忽略
5. 清除口令后白名单停用，已有条目不丢失，重新设口令后恢复

---

#### Phase 1e: 持久化记忆

**任务**：
1. 编写 `data/store.py` — PersonaDataStore
   - `ensure_tables()` — CREATE TABLE IF NOT EXISTS
   - `add_message(user_id, group_id, role, content)` — INSERT
   - `get_recent_messages(user_id, group_id, limit)` — SELECT ORDER BY DESC LIMIT
   - `count_messages(user_id, group_id)` — SELECT COUNT
   - `clear_messages(user_id, group_id)` — DELETE
   - `prune_old_messages(user_id, group_id, keep)` — 保留最近 N 条
2. DB 表：`persona_messages`（id, user_id, group_id, role, content, created_at）
3. 索引：`(user_id, group_id, created_at DESC)` 加速查询
4. 在 orchestrator.initialize() 中调用 ensure_tables()
5. 在 orchestrator.chat() 中保存和加载消息

**数据库连接获取**：
- 通过 `self.bot.db` 获取 BotDatabase 实例
- 需要访问其内部 `_db`（aiosqlite.Connection）
- 或者在 BotDatabase 上新增 property 暴露 raw connection

---

#### Phase 1g: 用户档案（提前实现）

**目标**：从对话中提取用户结构化信息，是核心情感价值所在

**任务**：
1. 定义 `data/models.py` — UserProfile
    - user_id, facts(dict), updated_at（跨群共享，不含 group_id）
    - facts 示例：`{"name": "小明", "pet": "柯基豆豆", "likes": ["科幻", "咖啡"]}`
2. 使用 Repository\<UserProfile\> 模式持久化
    - key_fields: ["user_id"]
3. 提取时机：每 5 轮对话批量提取（与评分 Agent 合并为一次调用）
4. 增量更新：新 facts merge 到已有 dict，不覆盖
5. ContextBuilder 中注入：`【你对用户的了解】` + facts 列表

**DB表**：`persona_user_profile_kv`（Repository模式）

**验证**：对话中提到"我喜欢猫"，后续对话中角色能提及此事

---

#### Phase 1h: 好感度关系模型（原 Phase 2a，提前实现）

**状态**: ✅ 已完成（`data/models.py` 中的 `RelationshipState`）

**实现内容**:
- 四维好感度（亲密度、激情、信任、安全感）
- 综合分数计算与区间标签映射
- `apply_deltas()` 方法应用评分变化
- 初始值从角色卡 `initial_relationship` 读取（默认 30）
- 关系状态 key: 私聊 `(user_id, "")`，群聊 `(user_id, group_id)`

---

#### Phase 1i: 好感度感知回复（原 Phase 2b，提前实现）

**状态**: ✅ 已完成（`memory/context_builder.py` 和 `orchestrator.py`）

**实现内容**:
- `ContextBuilder.build()` 接收 `warmth_label` 参数
- system_prompt 中注入当前好感度区间的行为描述
- orchestrator 根据 `RelationshipState` 计算并传递 warmth_label
- 角色回复会随好感度变化而调整语气和深度

**注意**: "厌倦"拒绝机制和恶意用户冷却机制尚未实现（移至 Phase 2）

---

#### Phase 1j: 批量评分（原 Phase 2c，提前实现）

**状态**: ✅ 已完成（`agents/scoring_agent.py` 和 `orchestrator.py`）

**实现内容**:
- `ScoringAgent.batch_analyze()` 批量分析对话
- 每 5 轮对话触发一次评分（`scoring_interval=5`）
- 3 级 JSON 解析容错（直接解析 → 去 markdown → 正则提取）
- Pydantic 校验 + clamp 值域限制
- 评分事件记录到 `persona_score_history`
- 同时提取用户档案 facts（与评分合并为一次 LLM 调用）

**注意**: 首次对话平滑（前 10 轮衰减系数 0.5）尚未实现

---

### Phase 2: 主动消息 + 群聊观察 + 时间衰减（已完成）

> **说明**: 由原 Phase 2d + Phase 3 合并而成。Phase 2 的核心功能（2a/2b/2c）已在 Phase 1 中提前实现。


---

#### Phase 2a: 时间衰减（已完成）

**目标**：长时间不互动好感度缓慢下降

**状态**: ✅ 已完成（`game/decay.py`、`orchestrator.py`、`PersonaDataStore`；批处理与对话共用 `last_relationship_decay_applied_at` 增量计费）

**配置语义**：`decay_daily_cap` 表示**单次衰减计算**中、按增量空闲时长算出的衰减量上限（`min(原始衰减量, cap)`），**不是**按日历日累计的总帽；一日内多次触发批处理或对话侧计费时，各次分别受该 cap 约束。若产品需要「每个自然日衰减总量上限」，需另加水位字段或按日聚合逻辑。

---

### 以下是原计划 Phase 3 的内容（合并到 Phase 2）

#### Phase 2b: 一定触发事件（已完成）

**状态**: ✅ 已完成（`proactive/scheduler.py` 定时问候与配置化 `proactive_greeting_schedule`）

**目标**：角色在指定时间段内生成特定事件（问候/作息等）

**任务**：
1. 编写 `proactive/scheduler.py` — ProactiveScheduler
   - tick() 方法：每秒被调用，内部节流 60 秒
   - 检查 scheduled_events 是否到达触发时间
   - 安静时段根据角色作息动态调整
2. 角色卡配置 scheduled_events：
   ```yaml
   scheduled_events:
     - type: "wake_up"
       time_range: "07:00-08:00"
     - type: "morning_greeting"
       time_range: "08:00-09:00"
   ```
3. 到达 time_range 时，System Agent 生成对应事件（ worldview 驱动）
4. 事件生成后进入分享流程（Phase 3d）

**注**：一定触发事件只保证生成，不保证发给所有用户（受好感度和概率影响）

**`time_range` 限制**：实现为**同一自然日内**的 `HH:MM` 闭区间比较，**不支持跨午夜**（例如 `22:00-06:00` 不会按预期工作）；跨日时段请拆成两条条目（如 `22:00-23:59` 与 `00:00-06:00`）。

---

#### Phase 2c: 好感度驱动分享（已完成）

**状态**: ✅ 已完成（调度器内目标筛选与概率；与上文「档案个性化」细项可能仍有差距）

**目标**：根据好感度决定事件分享的优先级和个性化程度

**任务**：
1. 事件生成后，筛选分享目标（按优先级）：
   - 私聊高好感度用户（≥60）
   - 私聊中好感度用户（40-60）
   - 群聊（活跃度≥60）
2. 计算触发概率：`P = 0.40 + 0.40 × (score/100)`（40分→40%，100分→80%）
3. 内容个性化：
   - 私聊：结合用户档案，提及用户相关的事
   - 群聊：通用版本，不强制提及群名/话题


---

#### Phase 2d: 角色生活模拟（已完成）

**状态**: ✅ 已完成（`proactive/character_life.py`、事件与日记管线）

**目标**：角色每天有自己的生活事件，基于世界观实时模拟

**任务**：
1. 编写 `proactive/character_life.py` — CharacterLife
   - `tick()` — 由 `PersonaOrchestrator.tick()` 调用（先于主动调度器运行）
   - **事件时刻（唯一权威）**：角色卡 `extensions.persona` 的 `daily_events_count`、`event_day_start_hour`、`event_day_end_hour`、`event_jitter_minutes`；`PersonaExtensions.generate_event_times()` 在活跃时段内均分槽位并施加 ± 抖动，返回**自 0 点起的分钟戳**。当日槽位会持久化（`persona_settings` / `PERSONA_SK_CHARACTER_LIFE`），避免进程重启后同一自然日重复采样。`persona_ai.character_life_jitter_minutes` 仅表示：当前本地「时:分」与某一计划槽相差不超过该分钟数即触发（配合约 60s 的 `tick`）。
   - **时区**：`PersonaConfig.timezone`（与全模块墙钟一致；默认示例为 `Asia/Shanghai`）
   - **日记触发时刻**：可在 `persona_ai.character_life_diary_time` 配置（运营向全局默认值，与「事件在一天内的分布」无关）

2. 事件生成（System Agent）：
   - **输入**：
     - 角色卡世界观设定（world/scenario）
     - 角色背景（description/personality）
     - 今日已发生事件（确定当前状态）
     - 过去3天日记内容（保持连续性）
     - 永久状态（文本，通过工具读取）
   - **输出**：一句话事件描述（20-50字），符合世界观
   - 使用辅助模型

3. 角色反应（Character Agent）：
   - 输入：角色 system_prompt + 事件 + 当天上下文
   - 输出：第一人称内心反应（30-80字）
   - 使用辅助模型

4. 永久状态系统：
   - DB表：`persona_character_state`（text 格式）
   - 工具：`read_state()` / `write_state(new_state)`
   - 更新：重大事件后 Character Agent 判断并更新
   - 示例："等级5的资深编辑，身体健康。最近完成了畅销书编辑..."

5. 日记总结：
   - 触发：每天 23:30
   - 读取全天 events + reactions → Character Agent 总结为日记
   - 存入 `persona_diary`，清理当日 events

6. 角色卡配置（与 Phase 1 角色卡扩展示例一致）：
   ```yaml
   daily_events_count: 5          # 一天内生成几条生活事件
   event_day_start_hour: 8        # 活跃时段起点（小时）
   event_day_end_hour: 22         # 活跃时段终点（小时，开区间上界与 generate_event_times 实现一致）
   event_jitter_minutes: 60       # 每个时间槽 ± 抖动（分钟）
   world: "现代都市，主角是出版社编辑"
   ```

---

#### Phase 2e: 事件分享（已完成）

**状态**: ✅ 已完成（分享窗口、限额与群活跃度门槛等与配置联动）

**目标**：角色生活事件生成后，按策略分享给用户

**任务**：
1. CharacterLife 生成事件后，调用分享逻辑

2. **发送限制**：
   - 总发送数 ≤ 10条
   - 时间窗口：5分钟内
   - 每个用户/群最多1条

3. **发送优先级**：
   - 私聊高好感度用户（≥60）
   - 私聊中等好感度用户（40-60）
   - **探索配额**：近 7 天有互动的低好感度用户（20-40），保留 1-2 条名额；完全不活跃的低好感度用户不发（防止打扰陌生人）
   - 群聊（活跃度≥60）最后发送

5. **群聊活跃度计算**：
   ```python
   activity = min(100, max(0, 50 + interactions * 2 - decay))
   # interactions: 每日@/聊天次数，上限10次（+20分）
   # decay: 1天无互动-10，3天-30，7天-50
   ```

6. **等级划分**：
   - ≥60：活跃，全功能
   - 30-60：一般，简化功能
   - <30：冷清，仅响应@和指令
   - <10持续7天：休眠，关闭AI功能

---

#### Phase 2f: 想念触发（已完成）

**状态**: ✅ 已完成（`_check_missed_users`；由头主要为未分享生活事件）

**目标**：长时间未互动时，用日记事件作为"由头"联系用户

**任务**：
1. 在 ProactiveScheduler.tick() 中添加 `_check_missed_users()` 检查

2. **触发条件**（全部满足）：
   - 用户 ≥3天未互动
   - 存在**未分享**的日记事件
   - 好感度 >= 40（最低门槛）
   - 概率触发：`P = 0.40 + 0.40 × (score/100)`（40分→40%，100分→80%）
   - 冷却检查：距上次主动消息 >= min_interval_hours
   - 私聊only

3. **消息内容**：
   - 提及该日记事件
   - 结合用户档案表达关心
   - 示例："前几天我在出版社门口看到一只流浪猫，突然想到你以前说过喜欢猫..."

4. **与事件分享的区别**：
   - 事件分享：事件生成后立即触发，面向活跃用户
   - 想念触发：3天未互动才触发，用积压的事件作为联系借口

5. **不占用事件分享的10条限额**

**与 Phase 6b Drive 系统的关系**：
- Phase 3e 是简化版，基于固定规则（时间 + 好感度）
- Phase 6b Drive 系统是进阶版，基于内在驱动力（connection drive 的 frustration）
- Phase 6b 完成后可替换 Phase 3e，或两者并存（Drive 触发更个性化）

---

#### Phase 2g: 群聊观察（已完成）

**状态**: ✅ 已完成（`ObservationBuffer`、提取与持久化；缓冲写库带节流）

**目标**：角色被动旁听群消息，动态触发提取

**任务**：
1. PersonaCommand.can_process_msg() 中：群聊未被 @ 时，静默记录到内存缓冲区
   - **过滤规则**：丢弃图片/表情包/语音、纯emoji、太短(<5字)、太长(>500字)、指令消息
   - 缓冲区结构：`{group_id, user_id, nickname, content, timestamp}`

2. **动态触发条件**（实时调整）：
   ```python
   # 初始阈值
   threshold_msgs = 20
   threshold_hours = 2
   
   # 每次触发后调整
   fill_time = 本次触发时间 - 上次触发时间
   if fill_time < 30分钟:
       threshold_msgs += 10  # 最多60条
   elif fill_time > 3小时:
       threshold_msgs -= 5   # 最少5条
   ```

3. **提取与存储**：
   - 批量提取1-3条值得记住的事
   - DB：`persona_observations`（group_id, participants, who_names, what, why_remember, observed_at）
   - 每群最多保留30条

4. **使用方式**（LLM按需查询）：
   - `get_observations_by_group(group_id, limit)` — 群聊中使用
   - `get_observations_involving(user_id, limit)` — 私聊中使用
   - 不直接注入上下文，提供查询接口供LLM调用

---

### Phase 3: 上下文升级 + Function Calling（约 3 天）

**注**：原 Phase 4 重新编号为 Phase 3；去掉对话摘要，四层记忆集成 + 工具调用

---

#### Phase 3.0: `.ai mute` 用户主动消息开关

**目标**：用户可关闭/开启主动消息，防止骚扰

**DB 表**：
- `persona_user_mute`（user_id TEXT PRIMARY KEY, muted_at TEXT, reason TEXT）

**PersonaDataStore 新增方法**：
- `is_user_muted(user_id) -> bool`
- `mute_user(user_id, reason)`
- `unmute_user(user_id)`

**命令实现**：
- `.ai mute` — 关闭主动消息，回复"已关闭主动消息，我不会再主动发消息给你了~"
- `.ai unmute` — 开启主动消息，回复"已开启主动消息，想我的时候可以找我聊天哦~"
- 再次发送相同命令 → 提示当前状态

**集成点**：
- `ProactiveScheduler._should_send_to_user()` 检查用户是否 muted
- 群聊中用户被 mute 不影响群内其他用户接收消息

---

#### Phase 3a: 上下文组装优化

**目标**：ContextBuilder 集成四层记忆，日记真正注入上下文

**组装顺序**（system_prompt）：
1. 角色卡 system_prompt
2. warmth_labels 行为描述（对应当前区间）
3. 角色今日生活（events 或昨日日记）— **修复：实际传入 diary_context**
4. 用户档案
5. 群聊观察（Phase 3c 实现后接入，LLM 按需查询）
6. 世界书命中（Phase 6a 实现，此处预留）

然后追加：短期记忆（字数限制 **1500** 字）+ 当前消息

**调整**：
- `max_short_term_chars`: 3000 → **1500**（约 10-15 轮对话）
- `max_messages`: 20 → **15**

**理由**：配合工具调用，LLM 可主动搜索深层记忆，不需要太长的短期记忆

---

#### Phase 3b: Function Calling 基础设施

**目标**：支持 LLM 通过工具调用按需查询记忆，支持多轮工具调用

**工具定义**（OpenAI function calling 格式）：

```json
{
  "type": "function",
  "function": {
    "name": "search_memory",
    "description": "搜索关于用户或特定话题的记忆，包括用户档案、群聊观察记录、日记等",
    "parameters": {
      "type": "object",
      "properties": {
        "query": {
          "type": "string",
          "description": "搜索关键词，如用户提到的内容、话题、名字等"
        },
        "type": {
          "type": "string",
          "enum": ["all", "profile", "observation", "diary"],
          "description": "搜索类型：all=全部, profile=用户档案, observation=群聊观察, diary=日记",
          "default": "all"
        },
        "days": {
          "type": "integer",
          "description": "日记搜索天数限制（仅对 diary 有效）",
          "default": 7
        }
      },
      "required": ["query"]
    }
  }
}
```

**LLMClient 新增方法**：

```python
async def chat_with_tools(
    self,
    messages: List[Dict],
    tools: List[Dict],
    max_tool_rounds: int = 5,  # 最多5轮工具调用
    timeout: int = 60,
) -> tuple[str, dict]:
    """
    支持多轮工具调用的对话
    
    Returns:
        (最终回复文本, 元数据字典)
    """
    # 循环调用 LLM，直到：
    # 1. LLM 不调用工具（返回普通回复）
    # 2. 达到最大轮次限制
    # 每次工具调用结果追加到 messages，继续下一轮
```

**LLMRouter 新增方法**：

```python
async def generate_with_tools(
    self,
    messages: List[Dict],
    tools: List[Dict],
    model_tier: ModelTier = ModelTier.PRIMARY,
    timeout: Optional[int] = None,
) -> str:
    """生成回复，支持工具调用"""
```

**多轮工具调用流程**：

```
第1轮: User Message → LLM → Tool Call (search_memory)
            ↓
      执行工具，结果追加到 messages
            ↓
第2轮: Messages (含工具结果) → LLM → Tool Call (再次 search_memory)
            ↓
      执行工具，结果追加到 messages
            ↓
第3轮: Messages (含工具结果) → LLM → 普通回复（无 tool_calls）
            ↓
      返回给用户
```

**Prompt Caching 数据收集**：

在 `LLMClient._get_cached_tokens()` 中已实现对 OpenAI 和 Anthropic 格式的缓存 token 提取，数据目前仅用于日志输出。

```python
# 当前仅用于日志，未持久化
def _get_cached_tokens(self, response) -> int:
    # OpenAI 格式 (GPT-4o+): response.usage.prompt_tokens_details.cached_tokens
    # Anthropic 格式: response.usage.cache_read_input_tokens
    ...
```

**TODO**: 后续可添加 `persona_llm_cache_stats` 表持久化缓存数据，用于：
1. 统计缓存命中率，优化 Prompt 结构
2. 分析不同模型的缓存效率差异
3. 计算实际成本节省

---

#### Phase 3c: Memory 工具实现与集成

**目标**：实现 `search_memory` 工具，在 Orchestrator 中处理工具调用

**PersonaDataStore 新增方法**：

```python
async def search_memory(
    self,
    user_id: str,
    group_id: str,
    query: str,
    search_type: str = "all",
    days: int = 7,
    limit: int = 5,
) -> str:
    """
    搜索记忆，返回格式化的文本结果
    
    Args:
        search_type: all/profile/observation/diary
        days: 日记搜索天数
        limit: 最多返回几条
    
    Returns:
        格式化的搜索结果文本，或"未找到相关记忆"

    **返回格式说明**：当前实现直接拼接搜索结果字符串。后续可优化为带结构化标记的格式，
    如 `[相关度:高] [时间] 内容 (来源)`，提升 LLM 对记忆质量的判断能力。
    """
```

**搜索范围策略**：

| 场景 | 用户档案 | 群聊观察 | 日记 |
|------|---------|---------|------|
| **私聊** | 全部 | 只检索该用户参与过的 | 近7天 |
| **群聊** | 全部 | 该群全部记录 | 近3天 |

**Orchestrator 工具调用处理**：

```python
async def _handle_tool_calls(
    self,
    tool_calls: List[Dict],
    user_id: str,
    group_id: str,
) -> List[Dict]:
    """处理工具调用，返回 tool 结果消息列表"""
    results = []
    for tc in tool_calls:
        if tc["function"]["name"] == "search_memory":
            args = json.loads(tc["function"]["arguments"])
            result = await self.data_store.search_memory(
                user_id=user_id,
                group_id=group_id,
                query=args["query"],
                search_type=args.get("type", "all"),
                days=args.get("days", 7),
            )
            results.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })
    return results
```

**配置项**：

```python
# PersonaConfig
class PersonaConfig:
    tools_enabled: bool = True  # 关闭时回退纯上下文模式
    max_tool_rounds: int = 5
```

---

#### Phase 3d: 厌倦拒绝机制（补充 Phase 2）

**目标**：好感度跌至"厌倦"区间（0-10）时，角色有概率拒绝回复

**实现**：
1. `orchestrator.chat()` 中检查 `RelationshipState.get_warmth_level()`
2. 如果 warmth_level == 0（厌倦区间）：
   - 普通骰子指令：正常响应（TRPG 优先）
   - AI 对话/聊天：根据概率拒绝
     - `P_refuse = 0.5 + 0.4 * (1 - score/10)`  # 50%-90% 拒绝概率
     - 拒绝时返回随机拒绝语（角色卡配置或默认）
3. 拒绝不消耗配额

**骰子指令判断**：当前仅以 `.` 开头判断，可能误判 `.ai` 等 AI 模块命令。精确识别已注册骰子指令格式的改进延后到 Phase 5c（掷骰工具实现阶段）统一处理。

**默认拒绝语**：
- "...（对方似乎没有兴趣理你）"
- "...（已读不回）"
- "嗯。"（冷淡）

---

### Phase 4: 成本与配置（约 2 天）

（原 Phase 5，编号顺延）

---

### Phase 5: 成本与配置（约 2 天）

---

#### Phase 5a: 配额系统

**目标**：每人每天主模型调用限额 20 次；白名单用户免限额

**任务**：
1. DB 表：`persona_usage`（user_id, date, count）
2. PersonaDataStore 新增：
   - `get_daily_usage(user_id, date)` → int
   - `increment_daily_usage(user_id, date)`
3. LLMRouter.generate() 中配额检查条件：model_tier == "primary" **且**无用户 Key **且**非白名单用户
4. 超额时抛出 QuotaExceeded 异常
5. PersonaCommand.process_msg() 捕获异常 → 返回友好提示 + 引导配 Key
6. PersonaConfig 新增 `daily_limit: int = 20`

**配额豁免优先级**（满足任一即跳过配额检查）：
- 用户自带 API Key → 用自己的 Key，不占公共配额
- 用户在白名单内（`whitelist_active = True` 且 `is_user_whitelisted`）→ 免限额
- 群聊（group_id 在群白名单）→ 免限额（群聊消耗的是群级别的信任，不计入个人）

---

#### Phase 5b: 用户自带 Key

**目标**：用户可通过 `.ai key config` 配置自己的 LLM 模型

**任务**：
1. 定义 UserLLMConfig 数据模型
   - user_id, primary_api_key_encrypted, primary_base_url, primary_model
   - auxiliary_api_key_encrypted, auxiliary_base_url, auxiliary_model
2. 使用 Repository\<UserLLMConfig\> 持久化
3. 加密：AES 对称加密，密钥从 `DICE_PERSONA_SECRET` 环境变量读取
   - **退化策略**：`DICE_PERSONA_SECRET` 未设置时，拒绝保存用户 Key，`.ai key config` 返回提示"请联系管理员先配置加密密钥"
4. 命令实现：
   - `.ai key` — 显示当前配置（Key 脱敏 `sk-***xxx`）
   - `.ai key config\n primary_key: ...\n ...` — 表单式配置解析
   - `.ai key clear` — 清除配置
   - 群聊中发送 → 提示"请私聊配置"
5. LLMRouter 路由逻辑完整版：
   - 有用户 Key → 用用户的 → **跳过配额**
   - 用户在白名单 / 群在群白名单 → 用默认 Key → **跳过配额**
   - 无用户 Key 且不在白名单 → 用默认 Key → **检查配额**
   - auxiliary 未单独配时复用 primary

---

#### Phase 5c: 掷骰工具

**目标**：角色可以掷骰子（TRPG 场景）

**任务**：
1. 定义 `roll_dice` 工具（function calling 格式）
2. 复用 DicePP 已有的骰子引擎解析和执行表达式
3. 在 Orchestrator._handle_tool_calls() 中添加处理分支

---

### Phase 6: 深度人格（可选，约 5 天）

**注**：Phase 6 为可选进阶功能，不影响核心体验

---

#### Phase 6a: 世界书引擎（优先）

**目标**：关键词触发知识注入

**任务**：
1. 读取角色卡的 CharacterBook.entries
2. 扫描当前消息和最近 N 轮历史
3. 关键词命中 → 将对应 content 注入 system_prompt
4. Token 预算控制（不超过 token_budget）
5. selective 模式：需要同时匹配 primary_keys 和 secondary_keys

---

#### Phase 6b: Drive 系统（可选）

**目标**：角色有内在驱动力，影响行为和主动消息

**任务**：
1. 5 维 Drive 模型：connection, novelty, expression, safety, play
2. 每个 Drive 有 baseline（角色卡定义）和 frustration（运行时状态）
3. 时间代谢：冷却（衰减）+ 饥饿（随时间增长）
4. Drive 状态注入 system_prompt

参考：OpenHer 的 DriveMetabolism 系统（简化版）

---

#### Phase 6c: 偏好学习（可选）

**目标**：识别和记住用户的偏好倾向

**任务**：
1. 定义偏好类别（参考 nikita Vice 系统，可简化）
2. 每 N 轮 LLM 分析用户倾向 → 累积到用户档案
3. 偏好注入 prompt，影响角色话题选择和表达方式

---

### Phase 7: 发布（约 3 天）

---

#### Phase 7a: 角色卡兼容

**目标**：支持导入 SillyTavern V2 格式的 JSON 和 PNG 角色卡

**任务**：
1. CharacterLoader 新增 JSON 格式支持
2. PNG 角色卡解析：从 PNG 的 tEXt chunk 中提取 base64 编码的 JSON
3. 格式转换：SillyTavern V2 字段 → Character 模型

---

#### Phase 7b: 敏感词过滤

**目标**：过滤不当内容

**任务**：
1. 使用 `sensitive-word-filter` 库
2. 本地词库文件，用户可自定义
3. 对角色回复进行过滤

---

#### Phase 7c: 文档与测试

**任务**：
1. 部署指南（配置 API Key、创建角色卡、启动 bot）
2. 角色卡编写指南（YAML 格式、各字段说明、示例）
3. 单元测试（数据层、衰减计算、配额检查、角色卡加载）
4. 集成测试（Mock LLM，验证对话流程）

---

#### Phase 7d: pip 包发布

**目标**：persona 引擎可脱离 DicePP 独立使用

**任务**：
1. 抽离核心逻辑（Character, LLMRouter, MemorySystem, Relationship）为独立包
2. DicePP 集成作为适配层
3. pyproject.toml 配置
4. 发布到 PyPI

---

## 四、数据库表总览

| 表名 | 类型 | Phase | 说明 |
|---|---|---|---|
| `persona_whitelist` | 自定义 SQL | 1f | 白名单：用户(type='user') + 群(type='group')，id + type 联合主键 |
| `persona_settings` | 自定义 SQL | 1f | 运行时 KV 配置（口令等） |
| `persona_messages` | 自定义 SQL | 1e | 对话历史，追加+排序 |
| `persona_user_profile_kv` | Repository\<T\> | 1g | 用户档案，key-value（从Phase 4提前） |
| `persona_relationship_kv` | Repository\<T\> | 2a | 4 维好感度，key-value |
| `persona_score_history` | 自定义 SQL | 2c | 评分审计日志 |
| `persona_daily_events` | 自定义 SQL | 2d | 当日事件缓冲 |
| `persona_diary` | 自定义 SQL | 2d | 每日日记 |
| `persona_character_state` | 自定义 SQL | 2d | 角色永久状态（文本） |
| `persona_observations` | 自定义 SQL | 2g | 群聊观察记录（动态触发） |
| `persona_usage` | 自定义 SQL | 5a | 每日用量追踪 |
| `persona_user_llm_kv` | Repository\<T\> | 5b | 用户自带 Key 配置 |

---

## 五、PersonaConfig 完整字段

**角色生活（配置分层）**：`persona_ai` 含 `character_life_enabled`、`character_life_diary_time`、`character_life_jitter_minutes`（触发容差，见 Phase 2g）。**一天内生成几条生活事件、落在什么时段、槽位抖动**仅由角色卡 `extensions.persona` 的 `daily_events_count` / `event_day_start_hour` / `event_day_end_hour` / `event_jitter_minutes` 与 `PersonaExtensions.generate_event_times()` 决定；**不存在** `persona_ai.character_life_event_hours` 字段。

下列 YAML 为结构说明，部分键名与当前仓库 `PersonaConfig` 可能略有出入，以 `core/config/pydantic_models.py` 为准；角色生活相关请以本节说明与 Phase 2g 为准。

```yaml
persona_ai:
  enabled: false
  character_name: "default"
  character_path: "./content/characters"

  # 访问控制（白名单）
  whitelist_enabled: true           # false = 完全跳过白名单检查（开发/调试用）
  # 口令存储在 DB（persona_settings 表），不在此处配置，支持运行时由管理员设置/清除
  # 口令未设置时白名单不激活，所有用户均可访问（已有白名单数据保留）

  # 主模型
  primary_api_key: ""
  primary_base_url: "https://api.openai.com/v1"
  primary_model: "gpt-4o"

  # 辅助模型
  auxiliary_api_key: ""           # 留空复用 primary
  auxiliary_base_url: ""
  auxiliary_model: "gpt-4o-mini"

  # 并发与超时
  max_concurrent_requests: 2      # LLM 并发上限（Semaphore）
  timeout: 30

  # 时区
  timezone: "Asia/Shanghai"       # 事件/日记/安静时段使用的时区

  # 记忆
  max_short_term_chars: 3000      # 短期记忆字数限制（替代轮数限制）
  max_messages: 200               # 最大保留消息数
  
  # 群聊活跃度
  group_activity_decay_days: [1, 3, 7]
  group_activity_decay_values: [10, 30, 50]
  group_activity_min: 10          # 关闭AI阈值

  # 好感度
  game_enabled: true
  scoring_interval: 5             # 每 N 轮批量评分
  decay_enabled: true
  grace_period_hours: 8
  decay_rate_per_hour: 0.5
  decay_daily_cap: 5.0
  cooldown_minutes: 30            # 恶意用户冷却时长

  # 群聊
  group_chat_enabled: true
  group_simple_scoring: true      # 群聊简化好感度
  observe_group: true             # 是否旁听群消息
  observe_min_length: 5           # 旁听消息最小字数
  observe_max_length: 500         # 旁听消息最大字数
  observe_initial_threshold: 20   # 初始触发阈值（条数）
  observe_max_threshold: 60       # 最大触发阈值
  observe_min_threshold: 5        # 最小触发阈值
  observe_max_records: 30         # 每群最多保留观察记录

  # 配额
  daily_limit: 20
  allow_user_key: true
  encryption_secret: ""           # 从 DICE_PERSONA_SECRET 读取

  # 工具调用（Phase 2+）
  # tools_enabled: true

  # 日志
  log_level: "INFO"               # persona.* logger 级别，DEBUG 可看完整 prompt

  # 主动消息
  proactive:
    enabled: true
    quiet_hours: [23, 7]
    min_interval_hours: 4
    max_shares_per_event: 10          # 每次事件最多分享10条
    share_time_window_minutes: 15     # 15 分钟内发送完毕
    # 想念触发（Phase 3e）
    miss_enabled: true                # 是否开启想念触发
    miss_min_hours: 72                # 最少3天未互动才触发
    miss_min_score: 40                # 最低好感度40

  # ── 角色生活模拟（persona_ai：开关、日记时刻、槽位触发容差；事件分布见角色卡 extensions.persona）──
  # character_life_enabled: true
  # character_life_jitter_minutes: 15   # 当前时刻与计划槽（日内分钟）之差 ≤ 该值则触发
  # character_life_diary_time: "23:30"
  
  # 角色卡配置示例（非 PersonaConfig 字段；extensions.persona）：
  # scheduled_events:
  #   - type: "wake_up"
  #     time_range: "07:00-08:00"
  #   - type: "morning_greeting"
  #     time_range: "08:00-09:00"
```

---

## 六、关键数据流

### 6.1 对话流程（Phase 2 完成后）

```
用户消息 → PersonaCommand.process_msg()
  → WhitelistGuard.check(user_id)      白名单检查（whitelist_enabled 时）
  → QuotaManager.check()              检查配额
  → DecayCalculator.calculate()        计算时间衰减
  → ContextBuilder.build()             组装四层记忆上下文
  → LLMRouter.generate(primary)        调用主模型
  → PersonaDataStore.add_message()     保存消息
  → ScoringAgent.batch_analyze()       可能触发评分（每 5 轮；输入关系快照与对话一致，为惰性 **effective** 视图）
  → RelationshipState.apply_deltas()   更新好感度
  → 返回回复文本
```

### 6.2 主动消息流程

```
tick() (每秒)
  → ProactiveScheduler.tick() (节流 60s)
    → _check_scheduled_events()        检查一定触发事件（问候/作息）
    → _check_missed_users()            检查想念触发（Phase 3e）
      → 筛选：≥3天未互动 + 有未分享事件 + 好感度≥40（**effective** 综合分，与对话展示一致）
      → 概率触发：P = 0.40 + 0.40 × (score/100)
      → 提及日记事件，私聊only
    → CharacterLife.tick()              检查角色事件生成（时刻分布以角色卡 generate_event_times 为规范）
      → 世界观驱动，含永久状态
      → register_task(async):
        → System Agent 生成事件
        → Character Agent 反应
        → 存入 persona_daily_events
        → 可能触发主动分享给高好感度用户
```

### 6.3 角色日记流程

```
tick_daily()
  → CharacterLife.generate_diary()
    → 读取全天 events + reactions
    → Character Agent 总结为日记
    → 存入 persona_diary
    → 清理 persona_daily_events
```

---

## 七、日志与可观测性

从 Phase 1 起贯穿所有阶段，不单独作为一个 Phase，而是每个 Phase 的标配。

### 7.1 日志层级

使用 Python 标准 `logging` 模块，logger 名统一以 `persona.` 为前缀：

```python
import logging
logger = logging.getLogger("persona.orchestrator")
logger = logging.getLogger("persona.llm.router")
logger = logging.getLogger("persona.scoring")
```

### 7.2 LLM 调用日志（Phase 1c 起）

每次 LLM 调用记录：

```
[persona.llm] model=gpt-4o-mini tier=auxiliary latency=1.2s tokens_in=850 tokens_out=120 user=12345 status=ok
[persona.llm] model=gpt-4o tier=primary latency=3.5s tokens_in=2100 tokens_out=350 user=12345 status=ok
[persona.llm] model=gpt-4o tier=primary latency=30.0s user=12345 status=timeout
[persona.llm] model=gpt-4o tier=primary user=12345 status=rate_limited retry_after=2s
```

实现方式：在 `LLMRouter.generate()` 中用 `time.monotonic()` 计时，response 的 `usage` 字段取 token 数。

### 7.3 评分变化日志（Phase 2c 起）

```
[persona.scoring] user=12345 deltas={int:+1.5, pas:-0.5, tru:+2.0, sec:0.0} composite=42→44 warmth=友好
[persona.scoring] user=12345 parse_failed raw="这是无效输出" fallback=zero_delta
```

评分变化同时写入 `persona_score_history` 表（持久化审计），日志只做运行时观察。

### 7.4 生命周期日志

```
[persona.init] character=default loaded relations=5 users
[persona.proactive] greeting sent to user=12345 type=template
[persona.proactive] event generated: "在出版社门口看到一只流浪猫"
[persona.decay] user=12345 hours_idle=12 decay=-2.0 composite=44→42
[persona.observation] extracted 2 observations from 30 messages in group=67890
```

### 7.5 日志级别规范

| 级别 | 用途 |
|---|---|
| ERROR | LLM 连续失败、数据库写入失败、不可恢复错误 |
| WARNING | JSON 解析失败（已降级）、配额耗尽、单次超时 |
| INFO | LLM 调用完成、评分变化、摘要生成、主动消息发送 |
| DEBUG | 完整 prompt 内容、LLM 原始响应、ContextBuilder 各层 token 数 |

### 7.6 实现要求

- Phase 1c：LLM 调用日志
- Phase 1f：白名单操作日志
- Phase 2c：评分日志 + JSON 解析失败警告
- Phase 3a：主动消息日志
- Phase 3f：群聊观察日志
- **不引入额外依赖**，用 Python 标准 logging 即可
- Debug 日志默认关闭（`persona.*` logger 默认 INFO），开发时可通过 `DICE_PERSONA_LOG_LEVEL=DEBUG` 开启

