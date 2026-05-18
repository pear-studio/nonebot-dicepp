# 延后项 Backlog

记录所有需要后续 PR 处理的延后项。
对应实现 commit 自行删除条目；脚本只负责追加与排序。

每条包含：
- **问题表现**：症状、错误日志、量化指标、复现路径
- **工作计划**：可能的修复方向、需先验证的假设、影响面、风险点

---

## persona

### [B-260511-e2f3c7] 主动分享 prompt 中 recent_history 引发跨时段"补答"
- 创建: 2026-05-11
- 问题表现:
  - 详细分析见 `.temp/1802_conversation_analysis.md`
  - 18:02 用户问"前两个观察是什么"，对话因工具调用超限未正常结束
  - 20:33 主动分享消息内容变成了回答 18:02 的问题："前两个……第一个是，体温比周围低。第二个是……长时间不活动会变僵硬..."
  - 根因: `life/proactive_scheduler.py` `_format_recent_history` 构建 `ShareMessageContext` 时传入了 `recent_history`（最近 5 条对话），`generate_share_message()` 的 prompt 包含此板块，LLM 看到未完成的问答就继续回答
- 工作计划:
  - 方案A: 在 `generate_share_message` 的 system prompt 中明确指示 recent_history 仅供参考，禁止回答历史问题
  - 方案B: 将 `recent_history` 改为"关系背景"摘要而非原始对话，从源头消除补答动机
  - 影响面: `life/event_agent.py` `generate_share_message` prompt、`life/proactive_scheduler.py` `_format_recent_history`

### [B-260515-9d9701] LLM 上下文末尾用户消息重复
- 创建: 2026-05-15
- 问题表现:
    - trace #767 messages 末尾同一条用户消息以两种格式出现两次：带 [HH:MM] 时间戳前缀的和不带的
    - 具体：{"role": "user", "content": "[17:53] 怎么回事呢?"} 和 {"role": "user", "content": "怎么回事呢?"} 并存
    - 浪费 context window tokens，可能干扰 LLM 理解
- 工作计划:
    - 排查上下文构建逻辑中历史消息格式化与当前消息追加的去重逻辑
    - 确认追加当前用户消息前是否已存在等效条目
    - 影响面：上下文构建 / prompt 组装代码

### [B-260515-15f67d] persona 存储结构优化：索引补全、数据清理策略、JSON 字段规范化
- 创建: 2026-05-15
- 问题表现:
    - 18 张表，分阶段开发遗留旧表（persona_messages / persona_group_conversations / persona_observations），migration 末尾已定义 DROP 但存量数据库可能残留
    - persona_score_history 按 user_id 查询无索引，persona_daily_events 按 date 查询无索引
    - persona_user_profiles.facts 和 persona_delayed_tasks.payload 以 JSON 字符串存储，无法做 SQL 内字段查询
    - persona_llm_traces（全量 messages+response）和 persona_unified_messages 无界增长，无 TTL/归档策略
- 工作计划:
    - 补全缺失索引：persona_score_history(user_id, created_at DESC)、persona_daily_events(date)
    - 评估 llm_traces 和 unified_messages 的合理保留周期，增加定期清理逻辑（保留近 N 天或近 N 条）
    - 评估 facts 字段是否需要拆分为独立属性表，或至少用 json_extract 兼容查询
    - 确认存量数据库的旧表 DROP 是否已生效
    - 影响面：data/migrations.py、data/store.py、可能需要新增 cleanup job

### [B-260518-f7ee13] persona_observation_buffers 是迁移遗留孤立数据
- 创建: 2026-05-18
- 问题表现:
    - persona_settings 中存储了 persona_observation_buffers key
    - 包含 3 个群的观察缓冲状态 (1033246217/861919492/1050935126)
    - 代码中已无任何引用该 key 的读写逻辑
    - 旧 persona_observations 表已删除，缓冲数据已无消费者
    - 影响: 数据库中存在无用的遗留配置数据
- 工作计划:
    - 确认无代码依赖后，可清理 persona_settings 中的 persona_observation_buffers 条目
    - 如新版观察机制仍需缓冲，需实现对应的读写逻辑
    - 影响面: persona_settings 表, persona_inspect.py

### [B-260518-952a69] sent_ok 字段在分段回复/降级回复/proactive 路径恒为 0，整体不可信
- 创建: 2026-05-18
- 问题表现: sent_ok 字段设计为标记消息是否送达，但多条路径不更新它：分段回复路径 (session.py _run_chat_with_tools_segmented) 未捕获 msg_id，sent_ok 恒为 0；降级回复路径 (session.py _coordinator_on_exhausted) 未传 msg_id，sent_ok 恒为 0；proactive 消息路径 (simulator._send_msg → port.send 不传 msg_id)，sent_ok 恒为 0。仅非分段回复路径正确更新 sent_ok=1。消息送达实际走 port.send 返回值和 post-send hook，sent_ok 字段作为送达信号不可信。
- 工作计划: 方案A: 在三条缺失路径补传 msg_id（分段回复/降级回复/proactive），使 sent_ok 全面可信。方案B: 标记 sent_ok 为废弃字段，在新版 schema 中移除。影响面: session.py, simulator.py, store.py, migrations.py

## tests

### [B-260515-4e9762] 测试用例速度优化：消除真实等待、减少冗余、统一风格
- 创建: 2026-05-15
- 问题表现:
    - 1705 条用例总耗时 131s，其中 3 条最慢的占 58.7s（45% 总耗时）：
      test_registration_failure_without_explicit_key_stays_standalone（34s，跑完整 bot lifespan）
      test_retry_exhausted_raises（14s，未 mock asyncio.sleep，真实等待重试退避）
      test_percentile_deviation 3 条参数化（18.3s，蒙特卡洛采样量过大）
    - 31 处 sleep/asyncio.sleep 调用，319 处 @patch 引用仅 unit/persona
    - unit/persona 46 文件 10358 行，6 个文件超 500 行（最大 680 行）
    - 风格不统一：MyTestCase(unittest.TestCase) 与 pytest 函数混用
    - 无 marker 体系区分慢速/集成/E2E 测试，CI 一次性全跑
- 工作计划:
    - 快速止血：test_retry_exhausted_raises mock asyncio.sleep（预计省 12s+）
    - 快速止血：test_percentile_deviation 降低采样量或标注 slow marker 默认跳过
    - 评估 test_registration_failure（34s）是否应从单元测试层移入集成/E2E 层
    - 建立 pytest marker 体系（slow / integration / e2e），CI 分阶段运行
    - 逐步统一测试风格，清理 MyTestCase 旧命名，拆分超大文件
    - 影响面：pytest.ini / pyproject.toml marker 配置、CI 脚本、各测试文件

### [B-260515-dd50eb] 用户自带 API Key 功能（.ai key config）
- 创建: 2026-05-15
- 问题表现: 用户可通过 .ai key config 命令提供自己的 LLM API key，覆盖全局配置。涉及计费体系、配额管理、滥用防护等一整套体系，当前设计对安全边界覆盖不足。该功能与 provider 路由重构的候选池调度、熔断器、探针等核心机制耦合过深，增加了不必要的复杂度。当前从本分支 scope 中移出，需求保留待后续独立实施。
- 工作计划: 独立设计用户 Key 管理子系统：安全存储（加密）、配额追踪、滥用检测。实现 UserLLMConfig 数据模型（含 API key 加密存储）。实现 .ai key config 命令交互流程。Router 层集成用户 Key 覆盖逻辑（优先级高于全局配置）。影响面: module/persona/data/models.py、module/persona/command.py、module/persona/llm/router.py。
