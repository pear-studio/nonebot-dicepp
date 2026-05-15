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

### [B-260515-a48112] Bot 自主消息未纳入 LLM 对话上下文，导致记忆割裂
- 创建: 2026-05-15
- 问题表现:
    - bot 自主消息 (user_id=bot_id, role=assistant) 存储在 persona_unified_messages 中，但构建用户对话 LLM 上下文时被排除
    - trace #767 (2026-05-15 17:55) 的 LLM messages 不含 bot 自主消息 #35–#37，用户引用 #37 中的"有人在吵架"，LLM 因缺失上下文只能从 daily events 强行解释，否认说过吵架并编造不存在的记忆
    - sent_ok 字段在所有消息中恒为 0，从未被更新，是死字段——消息送达实际走其他路径，字段不可信
    - LLM 回复中把今天 17:40 发生的事说成"昨天采药回来时"，时间引用存在幻觉
- 工作计划:
    - 在构建 LLM 上下文时将 bot 近期自主消息纳入对话历史（或至少检查最近 N 条 bot 自主消息的相关性）
    - 评估 sent_ok 字段：要么修复使其正确反映发送状态，要么删除以消除误导
    - 检查 system prompt 中 daily events 的时间表述，考虑添加相对时间提示（如"刚刚/今天下午"）减少 LLM 时间幻觉
    - 影响面：上下文构建逻辑、自主消息生成与存储、system prompt 模板

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

