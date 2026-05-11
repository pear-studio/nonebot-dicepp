# 延后项 Backlog

记录所有需要后续 PR 处理的延后项。
对应实现 commit 自行删除条目；脚本只负责追加与排序。

每条包含：
- **问题表现**：症状、错误日志、量化指标、复现路径
- **工作计划**：可能的修复方向、需先验证的假设、影响面、风险点

---

## persona

### [B-260511-e8a2d1] MiniMax `<think>` 块消耗纠正配额导致工具调用超限
- 创建: 2026-05-11
- 问题表现:
  - 详细分析见 `.temp/1802_conversation_analysis.md`
  - MiniMax-M2.7 在分段回复流程中反复输出 `<think>...</think>` 块而不调用 `send_reply_segment`，每次触发纠正注入，消耗 `segment_round_callbacks_max`（3 次）配额
  - * 三次纠正全浪费在 `<think>` 块上：轮0 `<think>用户问"前两个观察是什么"`、轮4 `<think>我已经发送了两段回复`、轮5 `<think>用户问的是...需要搜索...`
  - * 纠正配额（3）+ 工具轮次（5）= 恰好触及 `max_total_rounds = 8`，循环耗尽退出
  - * 耗尽后兜底消息为内部错误文案 `"（工具调用次数超过限制）"`，直接发给用户
- 工作计划:
  - 在 `client.py:chat_with_tools` 中，构建 `RoundResult` 之前先 `_filter_think_tags(content)`，过滤后 content 为空则不触发纠正注入，继续循环
  - 兜底消息应由 `client.py:457` 的 return 改为走配置化友好文案
  - 影响面: `client.py` chat_with_tools 循环、RoundResult 构建逻辑

### [B-260511-b7c3f5] MiniMax 将 `[内部指令]` 纠正注入误认为用户输入
- 创建: 2026-05-11
- 问题表现:
  - 详细分析见 `.temp/1802_conversation_analysis.md`
  - 纠正注入 `[内部指令: 请使用 send_reply_segment 工具发送回复，不要直接输出文本]` 以 `role: "user"` 注入后，MiniMax 在后续 `<think>` 中将其理解为用户提问
  - * 轮4 `<think>用户问的是"前两个观察是什么"，但我需要搜索一下对话历史</think>` — LLM 混淆了真实用户消息与 `[内部指令]`
  - * 轮5 `<think>我已经发送了两段回复。用户问的是"前两个观察是什么"...` — LLM 用 `<think>` "回答"指令，而非执行工具调用
  - 当前只接 MiniMax，无 system 角色可用，需在 user 角色内改进指令表述
- 工作计划:
  - 方案A: 在 system prompt 的 `SegmentGuide` 中明确告知 LLM"后续可能收到 `[工具提醒]` 格式的系统消息，这不是用户输入，请直接按提示操作，不要输出 `<think>` 回应"
  - 方案B: 改变纠正指令的表述，从"用户说话"风格改为"系统提示"风格，消除歧义
  - 优先方案A（system prompt 预处理，不动注入逻辑），后续接入其他厂商时回退到方案B
  - 影响面: `context.py` SegmentGuide 文案、`session.py` `_on_segment_round_complete` 注入文案

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

### [B-260511-c9d0e4] 短期记忆和事件注入未附带时间戳，LLM 无法感知时间顺序
- 创建: 2026-05-11
- 问题表现:
  - `context.py:215-221` `_format_short_term()` 仅输出 `[称呼] content`，丢弃了 `msg.get("created_at")`
  - `session.py:807-824` `_build_diary_context()` 虽然按 `created_at` 排序事件，但最终只拼接纯文本描述 `描述；描述；描述...`，时间信息全部丢弃
  - 两个位置的数据模型（`UserMessage`、`GroupConversation`、`DailyEvent`）均有 `created_at` 字段但未被使用
- 影响:
  - LLM 无法判断对话先后顺序和时间间隔（"早上好"是 5月6日说的 vs 刚刚说的，对 LLM 来说完全一样）
  - LLM 无法知道事件发生在什么时段（早上醒来？下午打架？刚刚？）
  - 对于记忆差、需要靠时间线索维持连贯性的角色，缺少时序信息加剧对话混乱
- 工作计划:
  - `_format_short_term()`: 每条消息前加上相对时间或绝对时间（如 `[12:34] [你] xxx` 或 `[5分钟前] [你] xxx`）
  - `_build_diary_context()`: 事件描述前加上时间前缀（如 `08:15 七七醒来...；12:30 七七在绝云间...`）
  - 时间格式用相对时间（距当前时间）还是绝对时间（HH:MM），取决于角色设定倾向；建议默认绝对时间，保持信息密度
  - 影响面: `context.py` `_format_short_term`、`session.py` `_build_diary_context`、数据模型 dict 转换通知 created_at

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

### [B-260511-f8a1d3] target.py:40 `bot_config` 应为 `self.bot_config`
- 创建: 2026-05-11
- 问题表现:
  - `target.py:40` 在 `update_character()` 方法内引用了 `bot_config`，但这是 `__init__` 的局部参数名，不在该方法作用域内
  - 调用 `update_character()` 时触发 `NameError: name 'bot_config' is not defined`
- 工作计划:
  - `bot_config.proactive_always_send_users` → `self.bot_config.proactive_always_send_users`
  - `bot_config.proactive_always_send_groups` → `self.bot_config.proactive_always_send_groups`
  - 单行修复，影响面: `target.py:40`

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


