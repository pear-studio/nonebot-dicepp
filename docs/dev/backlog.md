# 延后项 Backlog

记录所有需要后续 PR 处理的延后项。
对应实现 commit 自行删除条目；脚本只负责追加与排序。

每条包含：
- **问题表现**：症状、错误日志、量化指标、复现路径
- **工作计划**：可能的修复方向、需先验证的假设、影响面、风险点

---

## persona

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

### [B-260515-7e4aa0] persona_inspect 新增 tables 和 trace 子命令，减少 sqlite3 手工排查
- 创建: 2026-05-15
- 问题表现:
  - 手工 sqlite3 排查每次需 .schema 查列名，persona_llm_traces 19 列、persona_unified_messages 10 列，反复试错
  - trace 表 user_id 字段可能为空或格式不统一，直接过滤无结果，需绕路日期范围查询
  - trace 表 response 列只存摘要不存原文（如 "回复了梨子的问题，表示走神了，问梨子说的是去哪里。"），排查重复回复时无法直接看到实际输出
  - 实际排查中 persona_inspect.py user 已能覆盖 80% 场景，但缺少 trace 级别的查询入口
- 工作计划:
  - 新增 tables 子命令：读取 sqlite_master，输出所有 persona_ 前缀表的 DDL
  - 新增 trace 子命令：支持 --id / --user-id / --limit 过滤，输出格式化的 round_messages（每轮 think 摘要 + tool_call 名称/参数 + tool_results），绕过 response 摘要字段直接展示 LLM 实际行为
  - 影响面：scripts/dev/persona_inspect.py、skill 文档 docs/agent/skills/persona-inspect/

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

### [B-260518-abc123] 图片生成输出链路完善（generate_image）
- 创建: 2026-05-18
- 问题表现: generate_image 工具和 MiniMaxImageProvider 已实现，gen category 路由也已就位，但图片生成后仅返回 URL 字符串，send_reply_segment 不支持发送图片 segment，用户无法实际看到生成的图片。当前已将工具注册从 factory 中移除，保留 provider 和路由代码。
- 工作计划: send_reply_segment 增加 image 类型支持（QQ 适配器的 CQ:image 格式）。生成完图片后 LLM 可通过 send_reply_segment 将图片 URL 以 image segment 发送。完成后重新注册 generate_image 工具。影响面: module/persona/tools/send_reply_segment.py、module/persona/tools/generate_image.py、module/persona/factory.py。

