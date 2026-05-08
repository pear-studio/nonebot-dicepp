# 延后项 Backlog

记录所有需要后续 PR 处理的延后项。
对应实现 commit 自行删除条目；脚本只负责追加与排序。

每条包含：
- **问题表现**：症状、错误日志、量化指标、复现路径
- **工作计划**：可能的修复方向、需先验证的假设、影响面、风险点

---

## persona

### [B-260507-f9ea98] 厂商适配层根据模型类型选择 prompt 注入角色（system/user/developer）
- 创建: 2026-05-07
- 问题表现:
  - `_on_segment_round_complete` 全局硬编码 `user` 角色注入纠正消息
  - 短期 fix 已将前缀改为 `[内部指令: ...]`，但角色仍是 user
  - 兼容约束: MiniMax 等厂商不支持 mid-conversation system，必须用 user 兜底
  - 风险: LLM 可能将 `[内部指令: ...]` 文本误识为用户输入，影响对话连贯性
- 工作计划:
  - 在 ContextBuilder 或厂商适配层加模型类型分支：支持 system/developer 角色的厂商使用对应角色，MiniMax 等保留 user 注入路径
  - 需先观察现有 LLM 是否真的频繁误识 `[内部指令]`，决定是否提前优先级
  - 影响面: ContextBuilder、厂商 adapter、segment dispatcher

### [B-260508-1f1b9e] 消息发送路径统一（合并 send_segmented / send_now / dispatcher）
- 创建: 2026-05-08
- 问题表现:
    - `send_segmented` 三处调用方（`ChatSession._coordinator_on_result`、`PersonaApp.send_message`、`LifeSimulator._send_msg`）当前都是 `[make_segment(content, group_id)]` 单段形态，行为与 `send_now` 重叠
    - 分段路径走 `dispatcher → send_now`，非分段路径走 `send_segmented`，形成两套并存的发送路径，调度/失败回调/可观测点分裂
    - 同一"发送"语义有两套实现，未来扩展失败重试、限流、监控时需要双写
- 工作计划:
    - 修复方向：评估三处调用方是否都能迁移到 dispatcher（含 life 主动消息的 delay 与失败回调语义）；设计统一发送接口（扩展 dispatcher 支持非分段单条 / 合并到 send_now / 让 send_segmented 内部走 dispatcher）；删除 `send_segmented`，迁移所有调用方，更新测试
    - 影响面：persona 模块所有出口消息（chat 非分段、`PersonaApp.send_message`、life 主动消息）
    - 风险点：需保证 life 主动消息行为不退化（delay/失败回调语义一致性）；何时拉起：分段回复主线合入并稳定运行后单独立项推进

### [B-260508-d32c36] timeout 配置碎片化统一收敛
- 创建: 2026-05-08
- 问题表现:
    - EventGenerationAgent 内部 4 个 LLM 方法使用 3 种 timeout 来源（self.timeout / self.config.proactive_share_timeout_seconds / router 默认 30）
    - PersonaConfig 中 timeout 分裂为 4 条独立路径（timeout:30 / proactive_share_timeout_seconds:60 / event_generation_timeout:90 / diary 未传）
- 工作计划:
    - 立项统一后台 proactive 任务 timeout 配置项，覆盖 event、share、diary、observation 等后台路径
    - 评估 diary/observation 是否也应共享同一 timeout，避免"三等公民"

