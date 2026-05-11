# 延后项 Backlog

记录所有需要后续 PR 处理的延后项。
对应实现 commit 自行删除条目；脚本只负责追加与排序。

每条包含：
- **问题表现**：症状、错误日志、量化指标、复现路径
- **工作计划**：可能的修复方向、需先验证的假设、影响面、风险点

---

## persona

### [B-260511-d4a6e3] persona_llm_traces 只记录初始消息和最终响应，中间工具调用轮次不可见
- 创建: 2026-05-11
- 问题表现:
  - `router.py:511` `_execute_and_trace` 接收的 `messages` 是 `generate_with_tools` 入口的原始参数（仅 system + user 两条）
  - `client.py:292` `chat_with_tools` 内部的 `current_messages` 在循环中追加 assistant/tool/callback，但永远不返回
  - `router.py:337-352` `_maybe_record_trace` 写入 `trace.messages` 时序列化的始终是初始那两份消息
  - 导致 trace 丢失中间全部工具调用轮次信息：每轮 tool_calls 参数、tool 执行结果、回调注入内容、`<think>` 块等，无法用于事后调试
- 工作计划:
  - 方案A: `chat_with_tools` 返回 `current_messages` 作为 metadata 的新字段 `round_messages`，由 `_execute_and_trace` 写入 trace
  - 方案B: 在 `chat_with_tools` 循环内部逐轮记录 trace_event（条数会爆炸，不推荐）
  - 优先方案A，改动范围: `client.py` `chat_with_tools` 返回值扩展、`router.py` `_execute_and_trace` 处理新字段、`data/models.py` `LLMTraceRecord` 或新增列
  - 注意：完整 `current_messages` 可能很大（8 轮 × N 条消息），需评估是否压缩或只保留摘要

### [B-260511-a7f8b2] 事件描述在入库时被硬截断为 57 字符，LLM 看到的"今天发生的事"是碎片
- 创建: 2026-05-11
- 问题表现:
  - `event_agent.py:24` `_EVENT_DESCRIPTION_MAX_LEN = 60`，在事件生成后入库前（line 305-306）硬截断为 57 字符 + `"..."`
  - 截断发生在数据写入层，不是 prompt 构建层，原始描述永久丢失
  - 实测当天 13 条事件有一半以上带 `...`，例如：`"七七醒来，发现自己躺在不卜庐药柜旁的地上。右手握着的短铅笔滚落，笔记摊开在面前。她眨了眨眼，盯着笔记上的字迹，花了..."` → 丢失关键信息
  - LLM 通过 `_build_diary_context` 看到的"今天发生的事"是不完整的碎片，无法正确理解角色状态
- 工作计划:
  - 根因：60 字符对中文叙事太短（约等于一句话），MM2.7 生成的事件通常 80–200 字符
  - 移除 `_EVENT_DESCRIPTION_MAX_LEN` 硬截断，改为在 `_build_diary_context` 中用 `max_diary_context_chars` 做整体截断（已有此逻辑，line 812/822）
  - 或者 `_EVENT_DESCRIPTION_MAX_LEN` 上调到合理值（如 300），但既然 `_build_diary_context` 已有整体预算控制，直接移除硬截断更干净
  - 影响面: `event_agent.py` 事件生成、`session.py` `_build_diary_context` 截断逻辑

### [B-260511-e2f3c7] 主动分享 prompt 中 recent_history 引发跨时段"补答"
- 创建: 2026-05-11
- 问题表现:
  - 详细分析见 `.temp/1802_conversation_analysis.md`
  - 18:02 用户问"前两个观察是什么"，对话因工具调用超限未正常结束
  - 20:33 主动分享消息内容变成了回答 18:02 的问题："前两个……第一个是，体温比周围低。第二个是……长时间不活动会变僵硬..."
  - 根因: `proactive_scheduler.py:432-456` 构建 `ShareMessageContext` 时传入了 `recent_history`（最近 5 条对话），`generate_share_message()` 的 prompt 包含此板块，LLM 看到未完成的问答就继续回答
- 工作计划:
  - 方案A: 在 `generate_share_message` 的 system prompt 中明确指示 recent_history 仅供参考，禁止回答历史问题
  - 方案B: 将 `recent_history` 改为"关系背景"摘要而非原始对话，从源头消除补答动机
  - 影响面: `event_agent.py` `generate_share_message` prompt、`proactive_scheduler.py` `_format_recent_history`

### [B-260511-d9b2e4] 批量评分偶发类型错误，浪费 LLM 配额
- 创建: 2026-05-11
- 问题表现:
  - 日志: `05-10 18:03:35 [WARNING] 批量评分失败（不影响对话）: sequence index must be integer, not 'slice'`
  - 每次对话结束后 `_process_batch_scoring()` 触发一次 LLM 调用做批量评分，若连续失败则每次浪费 1 次辅助模型配额
  - 错误被 `except Exception` 吞掉，未记录 traceback 和输入数据，难以定位具体触发位置
- 工作计划:
  - 在 `session.py:603-606` 的 `except Exception` 块中补上 `logger.exception()`（带完整 traceback），复现后定位具体行
  - 怀疑位置: `scoring.py:73` `rel.get_warmth_level()` 若 `rel` 类型不对可能异常，或 `_build_analysis_prompt` 中 `isinstance` 检查遗漏
  - 定位后修复 + 加防御性类型检查
  - 影响面: `session.py` `_process_batch_scoring`、`scoring.py` `batch_analyze`

### [B-260511-a0c5f6] system prompt 中"近期对话"占比过高，缺乏摘要压缩
- 创建: 2026-05-11
- 问题表现:
  - 18:02 对话的 system prompt 实测 2821 字符，"近期对话"板块占约 70%
  - 7 轮完整角色扮演对话（含换行、括号动作描述）全部原文注入 system prompt
  - `max_short_term_chars=1500` 按字符截断但仍是原文，不区分信息密度
  - LLM 需要从大量对话噪音中提取关系线索，token 利用率低
- 工作计划:
  - 对超过 N 轮的历史对话做摘要压缩：`"{日期} 聊了关于{主题}的内容，关系{变化}"`
  - 或考虑将历史对话放在 `user` 消息而非 `system` 消息中，区分角色设定层和对话上下文层
  - 短期可降低 `max_short_term_chars` 缓解，但长期需要摘要机制
  - 影响面: `context.py` `ContextBuilder.build`、`_format_short_term`、session 截断策略

