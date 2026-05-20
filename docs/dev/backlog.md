# 延后项 Backlog

记录所有需要后续 PR 处理的延后项。
对应实现 commit 自行删除条目；脚本只负责追加与排序。

每条包含：
- **问题表现**：症状、错误日志、量化指标、复现路径
- **工作计划**：可能的修复方向、需先验证的假设、影响面、风险点

---

## persona

### [B-260515-dd50eb] 用户自带 API Key 功能（.ai key config）
- 创建: 2026-05-15
- 问题表现: 用户可通过 .ai key config 命令提供自己的 LLM API key，覆盖全局配置。涉及计费体系、配额管理、滥用防护等一整套体系，当前设计对安全边界覆盖不足。该功能与 provider 路由重构的候选池调度、熔断器、探针等核心机制耦合过深，增加了不必要的复杂度。当前从本分支 scope 中移出，需求保留待后续独立实施。
- 工作计划: 独立设计用户 Key 管理子系统：安全存储（加密）、配额追踪、滥用检测。实现 UserLLMConfig 数据模型（含 API key 加密存储）。实现 .ai key config 命令交互流程。Router 层集成用户 Key 覆盖逻辑（优先级高于全局配置）。影响面: module/persona/data/models.py、module/persona/command.py、module/persona/llm/router.py。

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

