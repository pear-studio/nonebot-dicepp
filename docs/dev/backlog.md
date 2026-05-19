# 延后项 Backlog

记录所有需要后续 PR 处理的延后项。
对应实现 commit 自行删除条目；脚本只负责追加与排序。

每条包含：
- **问题表现**：症状、错误日志、量化指标、复现路径
- **工作计划**：可能的修复方向、需先验证的假设、影响面、风险点

---

## persona

### [B-260519-7673f7] CharacterLoader 遗漏 PersonaExtensions 8 个字段
- 创建: 2026-05-19
- 问题表现:
    - character/loader.py _parse_character() 仅映射了 PersonaExtensions 的 7 个字段
    - 遗漏 refuse_messages、sleep_messages、share_message_examples、image_gen_style、image_gen_appearance、event_day_start_jitter_minutes、event_day_end_jitter_minutes 等 8 个字段
    - 角色卡 YAML 中配置这些字段静默失效，角色表现与预期不符
    - 参考: data-analyzer 报告 D6
- 工作计划:
    - 方案: 在 loader.py _parse_character() 中补全所有 PersonaExtensions 字段映射
    - 验证: 编写单元测试覆盖所有字段的加载路径
    - 影响面: character/loader.py、character/models.py
    - 风险: 低——纯增量字段补全，不影响已有行为

### [B-260519-d36eb5] SegmentDispatcher Worker 退出竞态导致消息丢失
- 创建: 2026-05-19
- 问题表现:
    - segment_dispatcher.py _worker_loop() finally 块以 workers.pop() -> wake_events.pop() -> queues.pop() 顺序清理
    - 若 notify() 在 queues.pop() 之前向队列插入 segment，随后队列被销毁，segment 永久丢失
    - 时序: (1) notify 插入 segment -> (2) notify 发现 worker 在 workers dict 中不创建新 worker -> (3) worker finally 执行 queues.pop() 删除队列 -> segment 丢失
    - 参考: chat-analyzer 报告 CH2
- 工作计划:
    - 方案: 在 queues.pop() 前检查队列是否为空，非空则重新调度或直接处理
    - 备选: 调整清理顺序为先 pop queue 再 pop workers/wake_events
    - 验证: 编写并发竞态测试（模拟 notify 与 worker exit 的交叉时序）
    - 影响面: chat/segment_dispatcher.py
    - 风险: 中——并发逻辑修改需仔细验证，不当修改可能引入死锁

### [B-260519-457fea] LLMRouter provider 创建硬编码 if/elif 改为注册表模式
- 创建: 2026-05-19
- 问题表现:
    - router.py _build_providers() 中 category->provider class 映射为硬编码 if/elif 分支
    - 代码自身 L90 注释承认此技术债: "未来若新增 category 需重构为 registry/factory 模式"
    - 新增 provider 类型（如 Anthropic、Google AI）必须修改此方法
    - 参考: llm-analyzer 报告 L2
- 工作计划:
    - 方案: _PROVIDER_CLASSES: Dict[str, Type] = {"llm": OpenAIProvider, "gen": MiniMaxImageProvider}，_build_providers 改为查表创建
    - 验证: 现有 provider 注册与路由测试通过
    - 影响面: llm/router.py、llm/providers/__init__.py
    - 风险: 低——纯重构，不改变运行时行为

### [B-260519-ffb8d3] AgentLoop 提升为独立模块供 chat/life/scoring 复用
- 创建: 2026-05-19
- 问题表现:
    - AgentLoop (llm/loop.py) 设计质量高但被 Router 内部化 (router.run_via_loop())
    - EventGenerationAgent 中各自新建独立 AgentLoop 实例，与 chat 路径无法共享 hook pipeline
    - 三种任务执行路径（Chat/Event/Scoring）各自管理 LLM 调用，无统一抽象
    - 参考: core-analyzer 报告 + ref-researcher RF1
- 工作计划:
    - 方案: 将 AgentLoop 移至 persona/agent/loop.py 作为一等公民，router.run_via_loop() 改为委托调用
    - EventGenerationAgent 和 ScoringAgent 复用同一个 AgentLoop 实例
    - 验证: 现有 chat/life/scoring 端到端测试通过
    - 影响面: llm/loop.py -> agent/loop.py（新建），llm/router.py，life/event_agent.py，chat/session.py
    - 风险: 中——涉及多子系统调用路径变更，需全量回归

### [B-260519-a515e5] create_persona() 工厂函数拆分（183行->Builder模式）
- 创建: 2026-05-19
- 问题表现:
    - factory.py create_persona() 183 行单函数，9 步线性组装，步骤间有隐式依赖
    - 每增加子系统（如 ActionEvaluator）都需修改工厂函数，违反开闭原则
    - _build_chat 12 个参数，位置传参脆弱
    - 参考: core-analyzer 报告 C1、C2
- 工作计划:
    - 方案: 拆分为 Phase Builder（_Phase1InfraBuilder / _Phase2ToolBuilder / _Phase3AppAssembler）
    - 备选: 引入简单 DI 容器或 Builder 模式分段构建
    - _build_chat 参数改为 dataclass 收纳可选依赖
    - 验证: 启动探针通过、模块初始化无回归
    - 影响面: factory.py 主要重写，command.py 调用方适配
    - 风险: 中——初始化流程重构，需覆盖 enabled/disabled/probe_failed 等分支

### [B-260519-7bdae0] ChatSession 职责拆分（编排/回复处理/评分触发）
- 创建: 2026-05-19
- 问题表现:
    - ChatSession 880 行，同时承担: 对话编排、coordinator 回调、工具调用委托、评分触发、历史管理、消息持久化、去重、睡眠门控、冷淡拒绝
    - 违反单一职责原则，单测困难（13 个构造参数）
    - 评分问题（CH4）: _pending_messages 仅以 user_id 为 key，同用户不同群的对话混合送入 batch_analyze，跨群上下文混杂导致评分误判
    - 评分问题（CH6）: 评分失败两条错误路径行为不一致——batch_analyze 抛异常直接 pop 丢弃 pending 消息 → 永久丢失；parse_error 保留 pending 重试 → 可能无限重试
    - 参考: chat-analyzer 报告 CH4、CH5、CH6
- 工作计划:
    - 方案: 拆为 ChatOrchestrator（编排+门控）+ ResponseHandler（回复持久化+发送）+ ScoringTrigger（评分调度）
    - 构造参数按职责分入子组件，减少 ChatOrchestrator 的直接依赖
    - ScoringTrigger 内部: pending 消息 key 改为 (user_id, group_id) 解决跨群混合评分（CH4）；统一异常与 parse_error 处理路径——都保留 pending 但设重试上限（CH6）
    - 验证: chat 路径端到端测试、评分正确性测试、跨群评分隔离测试
    - 影响面: chat/session.py（主要拆分），factory.py 组装逻辑适配
    - 风险: 中——ChatSession 是核心热路径，拆分需仔细保证行为不变

### [B-260519-da1d8e] 引入 Hook Pipeline 替代硬编码 Agent Loop
- 创建: 2026-05-19
- 问题表现:
    - 当前 AgentLoop while-loop 硬编码，扩展（如新增权限检查、上下文压缩）需修改核心循环
    - Hook 协议仅 3 个钩子（pre_llm/post_llm/post_tool），缺少 agent 生命周期事件
    - looplet composable_loop() 用 generator + hook pipeline 可在不修改核心循环的情况下新增能力
    - 参考: ref-researcher 报告 RF1
- 工作计划:
    - 方案: 引入 HookPipeline，含 pre_dispatch/post_dispatch/check_done/should_stop 等标准钩子点
    - 每个 hook 返回 HookDecision（None 放行 / Inject 注入 / Block 阻断 / Stop 终止）
    - 兼容现有 hook 协议，渐进迁移
    - 验证: 现有 hook（QuotaHook/BillingHook/TraceHook/SegmentCorrectionHook）在 pipeline 下行为一致
    - 影响面: llm/loop.py（AgentLoop）、llm/hook_protocol.py（扩展）、llm/hooks.py（适配）
    - 风险: 中——核心循环变更，需全量 AgentLoop 测试覆盖

### [B-260519-fcac7d] 统一工具执行模型（EventGenerationAgent 走 ToolRegistry）
- 创建: 2026-05-19
- 问题表现:
    - ToolRegistry 设计 ToolDomain.CHAT 和 ToolDomain.LIFE，但 life 域从未注册任何工具
    - EventGenerationAgent 使用 collecting.py 的 make_collecting_executor 绕过 ToolRegistry
    - 工具系统存在两套执行路径：chat 域走 ToolRegistry，life 域走 collecting executor
    - 参考: tools-analyzer 报告 T1、T7
- 工作计划:
    - 方案: 为 life 域注册正式工具（record_event/record_reaction 等），替换 collecting executor
    - EventGenerationAgent 复用 ToolRegistry 和 AgentLoop，不再自建实例
    - 验证: life 事件生成端到端测试，工具调用结果与 collecting 模式一致
    - 影响面: tools/registry.py、life/event_agent.py、tools/collecting.py（可移除）
    - 风险: 中——life 路径涉及 LLM 调用 + 工具交互，需仔细回归

### [B-260515-dd50eb] 用户自带 API Key 功能（.ai key config）
- 创建: 2026-05-15
- 问题表现: 用户可通过 .ai key config 命令提供自己的 LLM API key，覆盖全局配置。涉及计费体系、配额管理、滥用防护等一整套体系，当前设计对安全边界覆盖不足。该功能与 provider 路由重构的候选池调度、熔断器、探针等核心机制耦合过深，增加了不必要的复杂度。当前从本分支 scope 中移出，需求保留待后续独立实施。
- 工作计划: 独立设计用户 Key 管理子系统：安全存储（加密）、配额追踪、滥用检测。实现 UserLLMConfig 数据模型（含 API key 加密存储）。实现 .ai key config 命令交互流程。Router 层集成用户 Key 覆盖逻辑（优先级高于全局配置）。影响面: module/persona/data/models.py、module/persona/command.py、module/persona/llm/router.py。

### [B-260519-bbd19f] PersonaDataStore 按职责拆分为 4 个窄接口（Message/Relationship/Profile/Event）
- 创建: 2026-05-19
- 问题表现:
  - PersonaDataStore 1700 行 60+ 方法，被无差别塞给 ChatSession、LifeSimulator、ProactiveScheduler 等所有组件
  - ChatSession 实际只用 14 个方法，却持有整个 Store 引用，无法从签名判断数据访问边界
  - 导致越界调用风险（如 ChatSession 可能不小心调 list_all_relationships_raw）、单测困难（必须拉起完整 SQLite）
  - 症状案例: D4 search_memory 在数据层混入展示格式化逻辑，根源是 Store 缺乏接口约束
- 工作计划:
  - MessageStore: add_message / get_private_messages / get_group_messages / clear_messages / mark_sent
  - RelationshipStore: get_relationship / init_relationship / update_relationship / list_all_relationships / add_score_event
  - ProfileStore: get_profile / save_profile
  - EventStore: get_daily_events / add_daily_event / get_diary / save_diary
  - PersonaDataStore 同时实现 4 个接口，零额外对象分配
  - ChatSession 构造参数改为 (message_store, relationship_store, profile_store, event_store)，其他组件同理按需注入
  - 单测可精准 mock 单个窄接口，无需拉起 SQLite
  - 影响面: data/store.py（拆分接口定义）、factory.py（注入适配）、chat/session.py、life/simulator.py、life/proactive_scheduler.py 等依赖方
  - 风险: 中——接口拆分不改运行时行为，但涉及多个调用方签名变更

### [B-260519-164b83] LLM 错误分类体系重构：ErrorKind 枚举 + 分类唯一入口 + 分级恢复策略
- 创建: 2026-05-19
- 问题表现:
  - classify_error 逻辑在 router.py:237、router.py:335、loop.py:286 三处重复
  - 分类粒度只有 RETRYABLE/NON_RETRYABLE 两极，所有可重试错误一刀切切换候选模型
  - 用户侧错误反馈千篇一律（"抱歉我出错了"），不区分配额用尽/内容过滤/网络超时
  - Life 路径异常全部 logger.exception 后静默吞掉，调度器不感知连续失败
  - NON_RETRYABLE_EXCEPTIONS 在 coordinator.py:21 硬编码元组，新增错误类型需改多处
- 工作计划:
  - 新建 llm/errors.py，定义 ErrorKind 枚举（QUOTA_EXCEEDED/CONTENT_FILTERED/CONTEXT_TOO_LONG/RATE_LIMITED/TEMPORARILY_DOWN/NETWORK_ERROR/PROVIDER_ERROR）
  - 每种 ErrorKind 绑定 recovery action：QUOTA_EXCEEDED→跳过用户告知引导/CONTEXT_TOO_LONG→compact重试/RATE_LIMITED→退避等待/TEMPORARILY_DOWN→切候选/NETWORK_ERROR→退避连败标记/PROVIDER_ERROR→标记dead告警
  - classify() 唯一入口函数，router/loop/coordinator 统一调用
  - router.run_via_loop 中按 ErrorKind 分支执行对应 recovery，替换当前统一的候选切换
  - Life 路径通过 classify() 感知错误类型，连续 TEMPORARILY_DOWN 超阈值可暂停调度器
  - 用户可见错误信息按 ErrorKind 差异化（如 QUOTA_EXCEEDED 引导配置 key）
  - 影响面: llm/errors.py（新建）、llm/router.py、llm/loop.py、llm/coordinator.py、chat/session.py（兜底文案）、life/simulator.py（错误感知）
  - 风险: 中——重试策略变更需要全量 LLM 调用路径回归

### [B-260519-1601ee] 消解 EventShareTaskQueue，延迟分享逻辑合并到 ProactiveScheduler
- 创建: 2026-05-19
- 问题表现:
  - 事件从生成到发送经过 5 个组件、3 次调度决策（CharacterLife→LifeSimulator→EventShareTaskQueue→Scheduler→EventAgent→发送）
  - EventShareTaskQueue 的核心价值是 SQLite 持久化 1-5 分钟延迟任务防重启丢消息
  - 实际影响: 重启丢失未发送的事件分享对用户几乎无感知——事件已存入 persona_daily_events，分享只是主动消息通知
  - 延迟任务队列引入额外复杂度: DelayedTask 模型、store CRUD、独立的阈值检查（与 CharacterLife 中阈值可能不一致，见 LF5）
  - 参考: life-analyzer 报告 LF5
- 工作计划:
  - ProactiveScheduler 内部用 asyncio.create_task + asyncio.sleep 替代 EventShareTaskQueue
  - 移除 event_share_queue.py（228行）、data/models.py 中 DelayedTask 模型、store.py 中对应 CRUD、migrations.py 中 persona_delayed_tasks 表定义
  - LifeSimulator.tick() 中: event_share_queue.enqueue() 调用改为 scheduler.schedule_share(event, delay)
  - ProactiveScheduler 新增 schedule_share() 方法，内部管理 _pending_shares: set[asyncio.Task]
  - shutdown 时 cancel 所有 pending tasks
  - 影响面: life/event_share_queue.py（删除）、life/proactive_scheduler.py、life/simulator.py、life/character_life.py、data/models.py、data/store.py、data/migrations.py、factory.py
  - 风险: 中——删除持久化队列后，重启会丢失未发送的分享，但业务影响极低；需确保 Task 生命周期管理正确

### [B-260519-721203] 解除 CharacterLife 与 ProactiveScheduler 的双向耦合
- 创建: 2026-05-19
- 问题表现:
  - CharacterLife 当前持有 event_share_queue 依赖并自行判断 share_threshold，职责越界
  - CharacterLife.set_boundary_receiver(scheduler) 建立双向耦合——CharacterLife 需要通知 Scheduler 时间边界
  - CharacterLife 构造参数包含 share_threshold、share_delay_min、share_delay_max 等调度域参数（factory.py:391-393）
  - 事件分享的"是否发、何时发、发给谁"决策分散在 CharacterLife 和 ProactiveScheduler 两处
- 工作计划:
  - CharacterLife 职责收敛为仅事件生成，tick() 返回 EventResult（含 share_desire），不做调度决策
  - 移除 CharacterLife 对 event_share_queue 的依赖，移除 share_threshold/share_delay 参数
  - set_boundary_receiver() 保留但简化——仅同步 jittered 时间边界到 Scheduler，不在 CharacterLife 内部触发分享
  - LifeSimulator.tick() 统一负责决策: 获取事件 → 判断 share_desire ≥ threshold → 调用 scheduler.schedule_share()
  - ProactiveScheduler 成为所有外发消息的唯一调度入口
  - 影响面: life/character_life.py、life/simulator.py、life/proactive_scheduler.py、factory.py
  - 风险: 中——CharacterLife 接口变更需更新所有调用方和测试

### [B-260519-8c972e] persona_unified_messages 改名为 message_stream 并简化双写路径
- 创建: 2026-05-19
- 问题表现:
  - persona_unified_messages 实际存储了 bot 全局消息流（含非 persona 的群闲聊和指令回复），却以 persona_ 前缀命名，语义歪曲
  - 当前 sent_ok 走双写: ChatSession._persist_assistant_message 先写入 sent_ok=0，然后出站 hook _group_chat_recorder 再回填 sent_ok=1。若 hook 异常被吞，消息永远 sent_ok=0
  - 每次 add_unified_message 都触发 _retain_unified 做 DELETE+COUNT 裁剪，高频写入时浪费
  - Row→UnifiedMessage 反序列化（row[0]..row[8]）在 get_recent/get_group/search 三处重复
- 工作计划:
  - migrations.py: 表名 persona_unified_messages → message_stream，索引名同步改为 idx_msgstream_*
  - models.py: UnifiedMessage 类保持不变
  - store.py: 所有方法名从 unified_message 改为 message_stream（add_message_stream/get_recent_messages 等），消除 sent_ok 双写——ChatSession 等调用方在发送结果确认后一步写入 sent_ok=1
  - store.py: _retain_unified 降频为每 N 次写入或每 M 秒触发一次
  - store.py: 抽取 _row_to_message(row) 辅助方法消除 row[0]..row[8] 重复
  - command.py: _group_chat_recorder 对 msg_id 非 None 的分支逻辑改为不再回填，仅负责非 persona 回复路径的写入
  - factory.py: 构造参数 message_max_per_group 命名更新
  - 影响面: data/migrations.py、data/store.py、data/models.py、command.py、chat/session.py、life/simulator.py、life/proactive_scheduler.py、tools/search_history.py、factory.py（共 9 个文件）
  - 风险: 中——表名变更需要 ALTER TABLE RENAME TO 或创建新表迁移数据；双写简化需仔细验证各发送路径

## test

### [B-260519-285317] 补全零覆盖/测试不足模块的单元测试
- 创建: 2026-05-19
- 问题表现:
  覆盖率扫描发现以下模块完全没有测试或严重不足：
  - core/communication/ (7文件)、core/localization/ (4文件)、core/statistics/ (4文件) — 零覆盖
  - module/character/base/ (6文件)、module/dice_hub/ (4文件) — 零覆盖
  - persona/life/ action_evaluator/protocols/utils、persona/tools/ collecting/generate_image/search_history/search_query — 零覆盖
  - module/character/dnd5e/ (9源文件仅2有测试)、module/common/ (12命令仅2有测试) — 严重不足
  合计约 40+ 源文件缺乏测试保护
- 工作计划:
  按优先级分批补充：
  1. module/character/base/ (health/ability 等核心计算)
  2. module/common/ (help/log/welcome 等高频用户命令)
  3. core/communication/ (MessageMetaData/MessageSender)
  4. module/character/dnd5e/ (hp_command/spell)
  5. 其余模块按需补充
  影响面: tests/ 下对应新建测试目录/文件，纯新增不碰源码

