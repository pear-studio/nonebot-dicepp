# Persona 模块架构文档

> 面向开发者的技术架构说明
> 对应代码目录：`src/plugins/DicePP/module/persona/`

---

## 一、整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                    QQ / OneBot V11                           │
└────────────────────────┬────────────────────────────────────┘
                         │
              ┌──────────▼──────────┐
              │   PersonaCommand    │  @bot 触发聊天 / .ai 命令 / .ai admin 调试
              │   + tick() 定时钩子  │  主动消息 / 生活模拟 / 日记总结
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────┐
              │  PersonaOrchestrator│  核心编排层：调度各子系统
              └────┬────┬────┬─────┘
                   │    │    │
      ┌────────────┼────┼────┼────────────┐
      │            │    │    │            │
   ┌──▼──┐    ┌───▼──┐ │ ┌──▼───┐   ┌───▼────┐
   │character│ │  llm │ │ │ memory│   │  data  │
   └──┬────┘   └───┬──┘ │ └──┬───┘   └───┬────┘
      │            │    │    │           │
   ┌──▼──┐    ┌───▼──┐ │ ┌──▼───┐   ┌───▼────┐
   │ game │    │agents│ │ │proactive│   │utils   │
   └─────┘    └──────┘ │ └──┬────┘   └────────┘
                         │
                  ┌──────▼──────┐
                  │DelayedTaskQueue
                  └─────────────┘
```

### 1.1 模块分层职责

| 层级 | 目录 | 核心职责 |
|------|------|----------|
| **入口层** | `command.py` | 消息路由、命令解析、白名单检查、权限控制 |
| **编排层** | `orchestrator.py` | 初始化各组件、编排对话流程、驱动定时任务 |
| **角色系统** | `character/` | 角色卡 YAML 加载、SillyTavern V2 模型、世界书匹配 |
| **LLM 层** | `llm/` | 多模型客户端封装、路由与配额管理、trace 记录 |
| **记忆层** | `memory/` | 将四层记忆组装为 LLM messages 列表 |
| **数据层** | `data/` | Pydantic 模型、SQLite 存储访问、迁移脚本 |
| **游戏层** | `game/` | 四维好感度模型、时间衰减计算 |
| **主动层** | `proactive/` | 主动消息调度、角色生活模拟、延迟任务队列、群聊观察缓冲 |
| **Agent 层** | `agents/` | 评分 Agent（批量分析）、事件 Agent（生活事件生成） |
| **工具层** | `utils/` | 隐私脱敏、掷骰适配器、其他辅助函数 |

---

## 二、核心流程

### 2.1 对话流程

```
用户消息 → PersonaCommand.can_process_msg()
  → 白名单检查 → 权限检查 → 命令分发

PersonaCommand.process_msg() / 聊天触发
  → PersonaOrchestrator.chat()
    → 5秒消息去重
    → 首次对话：返回 first_mes
    → 厌倦拒绝检查（好感度 0-10 区间概率拒绝）
    → _build_messages(): ContextBuilder 组装上下文
      - system_prompt（角色设定 + 当前时间 + 关系标签）
      - 世界书扫描（关键词命中注入）
      - 用户档案注入
      - 今日事件 / 昨日日记注入
      - 短期记忆（按轮次截断）
    → LLMRouter.generate(primary) / generate_with_tools
    → 保存消息到 persona_messages
    → _update_interaction()
      - 应用时间衰减（如需要）
      - 更新最后互动时间
      - 触发批量评分（每 scoring_interval 轮）
    → 返回回复文本
```

### 2.2 批量评分流程

```
_update_interaction() 中消息数达到 scoring_interval * 2 时
  → _process_batch_scoring()
    → ScoringAgent.batch_analyze()
      - 输入：待评分消息 + 当前用户档案 + 关系状态（已应用衰减）
      - 调用辅助模型分析对话
      - 输出：ScoreDeltas + 新提取 facts
    → 更新 relationship 四维分数
    → 写入 persona_score_history
    → 合并并保存用户档案到 persona_user_profiles
```

评分 Agent 使用 3 级 JSON 容错策略：直接解析 → 去除 markdown 围栏 → 括号计数提取，全部失败则 zero-delta 兜底。

### 2.3 主动消息流程

```
Command.tick() 每秒调用
  → 单槽异步任务限制（at-most-once）
  → Orchestrator.tick()
    → CharacterLife.tick() 生成生活事件
      - 按角色卡配置的事件时间槽触发
      - System Agent 生成客观事件（含 duration_minutes）
      - Character Agent 生成角色反应（含 share_desire）
      - 存入 persona_daily_events
      - 持续时间 > 0 的事件加入 _ongoing_activities
      - 将事件加入 DelayedTaskQueue（延迟 1~5 分钟后分享）
    → ProactiveScheduler.tick()（60秒节流）
      - _check_scheduled_events(): 按角色卡 `scheduled_events` 配置触发的定时事件（问候/作息等）
        - 命中时间窗口后，由 EventAgent 现场生成事件描述和反应
        - 保存到 persona_daily_events，并标记 event_type 为今日已触发（解耦生成与发送）
        - 根据 `share` 策略（required/optional/never）和 `share_desire` 决定是否发送
      - _check_missed_users(): 想念触发（≥3天未互动）
        - 从当日 daily_events 中随机选取素材，不再依赖 pending_shares
      - 生成主动消息并返回
    → DelayedTaskQueue.tick()
      - 扫描到期的 pending 任务
      - share_desire ≥ proactive_event_share_threshold 才执行分享
      - 按好感度优先级选择分享目标（复用 scheduler 目标选择）
      - 生成主动消息并返回
```

### 2.4 日记生成流程

```
Command.tick_daily() 每天调用
  → Orchestrator.tick_daily()
    → apply_relationship_decay_batch(): 批量写回长时间未互动用户的衰减
    → CharacterLife.generate_diary()
      - 读取当日所有 events + reactions
      - Character Agent 总结为日记（100-300字）
      - 存入 persona_diary
    → _prune_traces(): 清理过期 LLM trace
```

### 2.5 群聊观察流程

```
群聊消息 → can_process_msg()（非 @bot 时）
  → _handle_group_observation()
    → ObservationBuffer 累积消息
    → 动态阈值触发（初始 20 条，根据填充速度自适应调整）
    → LLM 提取值得记忆的内容
    → 生成 Observation（who / what / why_remember）
    → 存入 persona_observations（每群最多保留 observe_max_records 条）
    → 更新群活跃度
```

---

## 三、各子系统详细说明

### 3.1 角色系统（`character/`）

#### 模型

- **`Character`**：完整角色卡模型，包含 SillyTavern V2 标准字段 + `extensions.persona` 扩展
- **`PersonaExtensions`**：控制好感度系统与生活模拟的参数（初始值、标签、事件时间分布、`scheduled_events` 分享策略等）
- **`CharacterBook` / `LoreEntry`**：世界书引擎，支持关键词触发、selective 二次筛选、优先级排序、token 预算控制

#### 加载

- **`CharacterLoader`**：从 `content/characters/{name}.yaml` 加载角色卡

#### 世界书匹配规则

1. 扫描当前消息 + 最近短期记忆文本
2. 任一 `keys` 命中即触发（受 `exact_match` / `min_match_length` 控制）
3. `selective=True` 时，还需 `secondary_keys` 中至少一个命中
4. 按 `order` 降序排列，在 `lore_token_budget` 内依次注入

---

### 3.2 LLM 层（`llm/`）

#### `LLMClient`

基于 `AsyncOpenAI` 的轻量封装，提供：
- `chat()`: 普通对话
- `chat_with_tools()`: 支持工具调用的多轮对话（最多 `max_tool_rounds` 轮）

#### `LLMRouter`

多模型路由与资源管理：
- **双模型支持**：primary（主模型，用于对话）+ auxiliary（辅助模型，用于评分/事件）
- **用户自定义 Key**：读取用户配置的独立 API Key / URL / Model
- **并发控制**：`asyncio.Semaphore(max_concurrent)`
- **配额检查**：`daily_limit`，白名单用户 / 自带 Key 用户豁免
- **Trace 记录**：`trace_enabled` 时持久化完整 messages / response / latency / tokens
- **滑动窗口统计**：最近 100 次调用的延迟分位值（p50/p90/p99）和错误汇总

---

### 3.3 记忆层（`memory/`）

#### `ContextBuilder`

将四层记忆组装为 LLM 可用的 `messages` 列表：

| 记忆类型 | 来源 | 注入位置 |
|----------|------|----------|
| 角色设定 | `Character` | system 消息 |
| 当前时间 | `persona_wall_now()` | system 消息 |
| 关系标签 | `RelationshipState.get_warmth_level()` | system 消息 |
| 用户档案 | `persona_user_profiles` | system 消息 |
| 世界书 | `Character.search_lore_entries()` | system 消息（before_char / after_char）|
| 今日事件/昨日日记 | `persona_daily_events` / `persona_diary` | system 消息 |
| 短期记忆 | `persona_messages` | system 消息（格式化为近期对话）|
| 当前用户消息 | 输入 | user 消息 |

**截断策略**：短期记忆按完整对话轮次从后往前截断，保留完整的 user-assistant 对，避免上下文断裂。

---

### 3.4 数据层（`data/`）

#### `PersonaDataStore`

统一数据访问接口，直接操作 `aiosqlite.Connection`（复用 DicePP 主数据库 `bot_data.db`）。

#### 数据库表一览

| 表名 | 用途 | 对应模型 |
|------|------|----------|
| `persona_messages` | 对话历史 | `Message` |
| `persona_whitelist` | 用户/群白名单 | `WhitelistEntry` |
| `persona_settings` | 运行时 KV（口令、调度器状态等）| - |
| `persona_user_profiles` | 用户档案（跨群共享）| `UserProfile` |
| `persona_user_relationships` | 四维好感度 | `RelationshipState` |
| `persona_score_history` | 评分审计日志 | `ScoreEvent` |
| `persona_daily_events` | 当日生活事件（含 `share_desire`、`duration_minutes`） | `DailyEvent` |
| `persona_diary` | 每日日记 | `DiaryEntry` |
| `persona_character_state` | 角色永久状态（LLM 维护的文本）| `CharacterState` |
| `persona_observations` | 群聊观察记录 | `Observation` |
| `persona_group_activity` | 群活跃度 | `GroupActivity` |
| `persona_usage` | 每日主模型用量 | `DailyUsage` |
| `persona_user_llm_config` | 用户自带 Key（加密存储）| `UserLLMConfig` |
| `persona_user_mute` | 用户主动消息静音开关 | - |
| `persona_delayed_tasks` | 延迟任务队列（random event share 等） | `DelayedTask` |
| `persona_llm_traces` | LLM 调用完整 trace | `LLMTraceRecord` |

#### 迁移机制

- `migrations.py` 中定义所有 `CREATE TABLE/INDEX` 语句
- `ALL_MIGRATIONS` 列表在 `PersonaDataStore.ensure_tables()` 中顺序执行
- 对已存在数据库的条件 `ALTER` 放在 `PersonaDataStore._apply_runtime_schema_patches()` 中
- **禁止在 `store.py` 中为尚未上线的表添加运行时 patch**：新表 schema 变更只通过 `migrations.py` 完成
- **两处须同步维护**

---

### 3.5 游戏层（`game/`）

#### `RelationshipState`

四维好感度模型：
- `intimacy`（亲密度，权重 0.3）
- `passion`（激情，权重 0.2）
- `trust`（信任，权重 0.3）
- `secureness`（安全感，权重 0.2）

`composite_score` = 加权平均，映射到 6 个温暖度等级和角色卡自定义标签。

#### `DecayCalculator`

时间衰减计算：
- 超过 `grace_period_hours` 后开始衰减
- 衰减率 `decay_rate_per_hour`，单次上限 `decay_daily_cap`
- 衰减下限 = `initial_relationship + decay_floor_offset`
- **惰性计算**：对话展示和评分时使用 `effective_relationship()` 计算当前应得分数，不立即写库
- **每日批处理**：`tick_daily()` 中将长时间未互动用户的衰减批量持久化

---

### 3.6 主动层（`proactive/`）

#### `ProactiveScheduler`

管理所有主动消息的发送策略：
- **安静时段**：可配置（默认 23:00-07:00），期间不发送
- **最小间隔**：同一用户两次主动消息之间的最小间隔
- **定时事件**：按角色卡 `scheduled_events` 配置触发
  - 命中时间窗口后，由 `EventGenerationAgent` 现场生成事件描述和反应
  - 保存到 `persona_daily_events`，并将 `event_type` 标记为今日已触发（发送失败不影响此状态）
  - 根据 `share` 策略（`required`/`optional`/`never`）和 `share_desire` 阈值决定是否发送
- **想念触发**：≥`miss_min_hours` 未互动 + 好感度 ≥`miss_min_score`，概率发送
  - 素材从当日 `persona_daily_events` 中随机选取，不再依赖 `_pending_shares`
- **目标选择**：按好感度优先级选择私聊用户和活跃群聊

#### `CharacterLife`

角色生活模拟：
- 按角色卡 `extensions.persona` 中配置的时间槽生成事件
- `event_day_start_hour` ~ `event_day_end_hour` 窗口内均分 `daily_events_count` 个槽位
- 每个槽位 ±`event_jitter_minutes` 随机抖动
- `tick()` 中当前时间与计划槽位差 ≤ `character_life_jitter_minutes` 时触发
- 事件生成后调用 `EventGenerationAgent.generate_event_result()` 和 `generate_event_reaction()`
- 事件包含 `share_desire`（分享欲望 0~1）和 `duration_minutes`（持续时间，0 表示瞬时）
- `duration_minutes > 0` 的事件会加入 `_ongoing_activities`，在后续事件生成时作为上下文注入

#### `DelayedTaskQueue`

通用延迟任务队列（SQLite 持久化）：
- 承载 random event share 等异步延迟任务
- `enqueue_event_share()`: 将生活事件按配置延迟（默认 1~5 分钟）入队
- `tick()`: 扫描并处理到期的 `pending` 任务，支持 `share_desire` 阈值过滤
- 任务处理成功后标记为 `completed`，失败则按 `max_retries` 重试，超限标记 `failed`
- 与 `ProactiveScheduler` 解耦：Orchestrator 负责将 scheduler 的目标选择能力注入 `on_share` 回调

#### `ObservationBuffer`

群聊观察缓冲：
- 每群独立缓冲，过滤无效消息（太短/太长/纯 emoji/指令等）
- 动态阈值触发提取，避免 API 浪费
- 提取结果写入 `persona_observations`

---

### 3.7 Agent 层（`agents/`）

#### `ScoringAgent`

批量分析对话，输出：
- `ScoreDeltas`：四维好感度变化（范围 -5.0 ~ +5.0）
- `facts`：从对话中提取的用户结构化信息

使用辅助模型，prompt 中注入当前关系状态和已知用户档案。

#### `EventGenerationAgent`

包含三种生成任务：
- `generate_event_result(context)`: System Agent，通过 `record_event` 强制工具调用生成结构化事件，返回 `EventGenerationResult`（含 `description` 和 `duration_minutes`）
- `generate_event_reaction(event, character_name, character_description, share_policy)`: Character Agent，通过 `record_reaction` 强制工具调用生成结构化反应，返回 `EventReactionResult`（含 `reaction` 和 `share_desire`）
- `generate_diary()`: Character Agent，总结全天事件为日记（100-300字）

旧方法 `generate_event()` 和 `generate_reaction()` 已标记为 DEPRECATED，保留兼容直至所有调用方迁移完成。均使用辅助模型，失败时返回安全兜底文本。

---

### 3.8 工具层（`utils/`）

- **`privacy.py`**: `mask_sensitive_string()` 用于日志中脱敏 API Key
- **`roll_adapter.py`**: `RollAdapter` 桥接 DicePP 掷骰引擎，为 `roll_dice` 工具提供支持

---

## 四、扩展点

### 4.1 新增工具

在 `orchestrator.py` 的 `_get_tools()` 中定义工具 schema，在 `_execute_tool()` 中实现执行逻辑。

### 4.2 新增 Agent

创建 `agents/{name}_agent.py`，在 `orchestrator.initialize()` 中初始化，通过 `orchestrator` 暴露方法供 `command.py` 调用。

### 4.3 新增数据表

1. 在 `data/migrations.py` 中添加 `CREATE TABLE/INDEX`
2. 在 `data/models.py` 中添加对应的 Pydantic 模型
3. 在 `data/store.py` 中实现 CRUD 方法
4. 如需兼容旧数据库，在 `_apply_runtime_schema_patches()` 中添加条件 `ALTER`

### 4.4 新增命令

在 `command.py` 的 `can_process_msg()` 中解析命令，在 `process_msg()` 中实现处理逻辑。管理员命令需检查 `_is_admin()`。

---

## 五、时间处理策略

Persona 模块采用 **naive local datetime** 策略：

- 所有业务时间统一通过 `wall_clock.persona_wall_now(timezone)` 获取
- 返回值为**不带 `tzinfo` 的本地时间**，与 SQLite `fromisoformat` 存储行为保持一致
- 禁止在业务代码中直接使用 `datetime.now()` 或 `datetime.min`，以防止 naive/aware 混用导致的 `TypeError`
- 排序或兜底场景使用安全的 naive 基准时间（如 `datetime(2000, 1, 1)`）代替 `datetime.min`

> 当前未迁移到 `tzinfo-aware` datetime，该方向涉及衰减、日记、trace、调度器等多个子系统，回归面较大，如需切换应作为独立变更推进。

## 六、关键设计决策

| 决策 | 结论 | 理由 |
|------|------|------|
| 编排框架 | 自研轻量编排 | 对话场景不需要 ReAct 等重框架 |
| 数据库 | SQLite（复用 DicePP 主库）| 与现有 Repository 模式一致 |
| 角色卡格式 | YAML，兼容 SillyTavern V2 | 手写友好，可复用社区生态 |
| 好感度计算 | 四维加权 + 六区间标签 | 自然渐变，标签由角色卡定义 |
| 时间衰减 | 惰性计算 + 每日批处理 | 避免每次对话都写库 |
| 主动消息触发 | 生活事件驱动 + 想念触发 + 角色卡定时事件 | 有"由头"更自然 |
| 群聊观察 | 动态阈值 + 批量提取 | 适应跑团消息波动，节省 API 成本 |
| LLM 容错 | 3 级 JSON 解析 + zero-delta 兜底 | 提高系统鲁棒性 |
| 并发控制 | asyncio.Semaphore | 便宜模型通常并发低，排队比报错好 |
| Trace 记录 | 默认关闭，异步写入 | 生产环境不影响主流程性能 |
