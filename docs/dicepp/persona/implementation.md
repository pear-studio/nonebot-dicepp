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

### Phase 5: 深度人格（可选，约 5 天）

**注**：Phase 5 为可选进阶功能，不影响核心体验（原 Phase 6，编号顺延）

---

#### Phase 5a: 世界书引擎（优先）

**目标**：关键词触发知识注入

**任务**：
1. 读取角色卡的 CharacterBook.entries
2. 扫描当前消息和最近 N 轮历史
3. 关键词命中 → 将对应 content 注入 system_prompt
4. Token 预算控制（不超过 token_budget）
5. selective 模式：需要同时匹配 primary_keys 和 secondary_keys

---

#### Phase 5b: Drive 系统（可选）

**目标**：角色有内在驱动力，影响行为和主动消息

**任务**：
1. 5 维 Drive 模型：connection, novelty, expression, safety, play
2. 每个 Drive 有 baseline（角色卡定义）和 frustration（运行时状态）
3. 时间代谢：冷却（衰减）+ 饥饿（随时间增长）
4. Drive 状态注入 system_prompt

参考：OpenHer 的 DriveMetabolism 系统（简化版）

---

### Phase 7: 发布与可观测性（约 5 天）

**注**：原 Phase 7，编号顺延；拆分为 7a/7b/7c，新增调试数据建设

---

#### Phase 7a: 调试数据与 LLM Trace

**目标**：为 LLM 功能提供可回溯、可审计、可导出的调试数据

**任务**：
1. `persona_llm_traces` 表：持久化完整 messages / response / tool_calls / latency / tokens / error
2. `persona_score_history` 表增加 `conversation_digest` 字段（评分依据摘要）
3. `persona_daily_events` 表增加 `system_prompt_digest` 和 `raw_response` 字段
4. `persona_observations` 表增加 `source_messages_count` 和 `extract_prompt_digest` 字段
5. LLMRouter 增加滑动窗口延迟统计（最近 100 次），暴露延迟分位值
6. 管理员命令：`.ai admin trace <user_id>` / `.ai admin stats` / `.ai admin errors`
7. ContextBuilder 增加 `build_debug_info()` 返回各层记忆占用
8. trace 自动清理：`trace_max_age_days` 控制保留天数

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

## 四、数据库表总览

| 表名 | 类型 | Phase | 说明 |
|---|---|---|---|
| `persona_whitelist` | 自定义 SQL | 1f | 白名单：用户(type='user') + 群(type='group')，id + type 联合主键 |
| `persona_settings` | 自定义 SQL | 1f | 运行时 KV 配置（口令等） |
| `persona_messages` | 自定义 SQL | 1e | 对话历史，追加+排序 |
| `persona_user_profile_kv` | Repository\<T\> | 1g | 用户档案，key-value（从原Phase 4提前） |
| `persona_relationship_kv` | Repository\<T\> | 2a | 4 维好感度，key-value |
| `persona_score_history` | 自定义 SQL | 2c | 评分审计日志 |
| `persona_daily_events` | 自定义 SQL | 2d | 当日事件缓冲 |
| `persona_diary` | 自定义 SQL | 2d | 每日日记 |
| `persona_character_state` | 自定义 SQL | 2d | 角色永久状态（文本） |
| `persona_observations` | 自定义 SQL | 2g | 群聊观察记录（动态触发） |
| `persona_usage` | 自定义 SQL | 5a | 每日用量追踪 |
| `persona_user_llm_kv` | Repository\<T\> | 5b | 用户自带 Key 配置 |
| `persona_llm_traces` | 自定义 SQL | 7a | LLM 调用完整 trace（messages/response/tool_calls/latency/tokens） |
| `persona_score_history` | 自定义 SQL | 7a | 扩展 `conversation_digest` 字段（评分依据摘要） |
| `persona_daily_events` | 自定义 SQL | 7a | 扩展 `system_prompt_digest`、`raw_response` 字段 |
| `persona_observations` | 自定义 SQL | 7a | 扩展 `source_messages_count`、`extract_prompt_digest` 字段 |

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

  # LLM Trace（Phase 7a）
  trace_enabled: false            # 是否持久化 LLM 调用 trace
  trace_max_age_days: 7           # trace 保留天数，自动清理旧数据

  # 主动消息
  proactive:
    enabled: true
    quiet_hours: [23, 7]
    min_interval_hours: 4
    max_shares_per_event: 10          # 每次事件最多分享10条
    share_time_window_minutes: 15     # 15 分钟内发送完毕
    # 想念触发（Phase 2f）
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
    → _check_missed_users()            检查想念触发（Phase 2f）
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
- Phase 2e/2f：主动消息日志
- Phase 2g：群聊观察日志
- **不引入额外依赖**，用 Python 标准 logging 即可
- Debug 日志默认关闭（`persona.*` logger 默认 INFO），开发时可通过 `DICE_PERSONA_LOG_LEVEL=DEBUG` 开启

---

## 八、Phase 7 调试数据建设

Phase 7a 为 LLM 系统补充结构化调试能力，与运行时日志互补：日志用于实时监控，调试数据用于事后审计与问题定位。

### 8.1 LLM Trace 表（`persona_llm_traces`）

```sql
CREATE TABLE IF NOT EXISTS persona_llm_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,        -- user_id:group_id:timestamp，用于聚合同一会话
    user_id TEXT DEFAULT '',
    group_id TEXT DEFAULT '',
    model TEXT NOT NULL,
    tier TEXT NOT NULL,              -- primary / auxiliary
    messages TEXT NOT NULL,          -- JSON 完整 messages 列表
    response TEXT NOT NULL,          -- LLM 最终回复文本
    tool_calls TEXT DEFAULT '',      -- JSON tool_calls 列表（如有）
    latency_ms INTEGER,              -- 调用耗时（毫秒）
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    temperature REAL,
    status TEXT NOT NULL,            -- ok / timeout / rate_limit / auth_error / content_filter / parse_error / unknown
    error TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_persona_llm_traces_session ON persona_llm_traces(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_persona_llm_traces_user ON persona_llm_traces(user_id, created_at DESC);
```

**写入位置**：`LLMRouter.generate()` 和 `generate_with_tools()` 的 `finally` 块中统一写入，确保无论成功失败都有记录。

**开关控制**：`persona_ai.trace_enabled`（默认 `false`），避免生产环境无限制写库。

**自动清理**：`PersonaDataStore` 提供 `prune_llm_traces(days)` 方法，在 `tick_daily()` 中调用，清理超过 `trace_max_age_days` 的旧数据。

### 8.2 Prompt 快照与调试导出

**ContextBuilder 调试信息**：

```python
def build_debug_info(self) -> Dict[str, Any]:
    return {
        "system_prompt_chars": len(system_prompt),
        "short_term_chars": sum(len(m["content"]) for m in short_term_history),
        "profile_chars": len(profile_text),
        "diary_chars": len(diary_context),
        "total_messages": len(result),
    }
```

**管理员命令**：
- `.ai admin trace <user_id>`：读取该用户最近 5 条 trace，按时间倒序拼接为文本返回（含 model/latency/status/response 前 200 字）
- `.ai admin trace <user_id> full`：返回最近 1 条 trace 的完整 messages + response（可能很长，注意消息截断）

### 8.3 评分与事件调试字段

**`persona_score_history` 扩展**：
```sql
ALTER TABLE persona_score_history ADD COLUMN conversation_digest TEXT DEFAULT '';
```
写入内容：触发评分时的最近 3 轮对话摘要（每轮截取前 80 字），便于复盘"为什么好感度变了"。

**`persona_daily_events` 扩展**：
```sql
ALTER TABLE persona_daily_events ADD COLUMN system_prompt_digest TEXT DEFAULT '';
ALTER TABLE persona_daily_events ADD COLUMN raw_response TEXT DEFAULT '';
```
- `system_prompt_digest`：事件生成时使用的 system prompt 前 200 字
- `raw_response`：LLM 原始输出（未解析前的文本），用于调试"为什么生成了奇怪的事件"

**`persona_observations` 扩展**：
```sql
ALTER TABLE persona_observations ADD COLUMN source_messages_count INTEGER DEFAULT 0;
ALTER TABLE persona_observations ADD COLUMN extract_prompt_digest TEXT DEFAULT '';
```
- `source_messages_count`：触发提取时观察缓冲区内的消息条数
- `extract_prompt_digest`：提取 prompt 的前 200 字

### 8.4 运行时指标（内存统计）

**LLMRouter 滑动窗口统计**：维护一个固定长度队列（如 `collections.deque(maxlen=100)`），记录每次调用的 `(latency_ms, tokens_in, tokens_out, status)`。

暴露方法：
```python
def get_latency_percentiles(self) -> Dict[str, float]:
    """返回 p50/p90/p99 延迟（毫秒）"""

def get_error_summary(self) -> Dict[str, int]:
    """返回最近 100 次调用的错误分类计数"""
```

**管理员命令 `.ai admin stats`**：返回如下格式的文本：
```
今日调用: 142 次
主模型: 38 次, 平均延迟 2.3s, 错误率 2.6%
辅助模型: 104 次, 平均延迟 1.1s, 错误率 0.0%
p50/p90/p99 延迟: 1.2s / 2.8s / 5.1s
Token 消耗: 输入 128k / 输出 34k
```

**管理员命令 `.ai admin errors`**：返回最近 24h 错误摘要：
```
最近 24h 错误: 5 次
- timeout: 3 次
- rate_limit: 2 次
```

### 8.5 错误分类规范

| `status` | 判定条件 |
|---|---|
| `ok` | 正常返回 |
| `timeout` | `asyncio.TimeoutError` 或耗时超过 timeout |
| `rate_limit` | 响应 HTTP 429 或包含 "rate limit" |
| `auth_error` | 响应 HTTP 401/403 或包含 "authentication" |
| `content_filter` | 响应包含 "content_filter" / "moderation" |
| `parse_error` | 返回格式无法解析（JSON/工具调用） |
| `unknown` | 其他异常 |

分类逻辑统一封装在 `LLMRouter._classify_error(e)` 私有方法中。

### 8.6 实现约束

- **不引入额外依赖**：延迟分位值用标准库 `statistics.quantiles` 或简单排序计算
- **trace 默认关闭**：仅在 `trace_enabled=true` 时写库
- **不影响主流程性能**：trace 写入异步执行（`create_task`），失败不抛异常
- **数据安全**：trace 表包含完整 prompt，管理员命令导出时应避免在群聊中返回敏感信息（建议私聊 only 或做截断）

