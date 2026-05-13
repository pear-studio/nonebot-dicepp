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
  - 根因: `proactive_scheduler.py:432-456` 构建 `ShareMessageContext` 时传入了 `recent_history`（最近 5 条对话），`generate_share_message()` 的 prompt 包含此板块，LLM 看到未完成的问答就继续回答
- 工作计划:
  - 方案A: 在 `generate_share_message` 的 system prompt 中明确指示 recent_history 仅供参考，禁止回答历史问题
  - 方案B: 将 `recent_history` 改为"关系背景"摘要而非原始对话，从源头消除补答动机
  - 影响面: `event_agent.py` `generate_share_message` prompt、`proactive_scheduler.py` `_format_recent_history`

### [B-260512-f2d8a1] tool_choice="required" 场景缺少显式终止机制
- 创建: 2026-05-12
- 问题表现:
  - `tool_choice="required"` 下 LLM 完成工具调用后无法表达"我已做完"——它必须继续调工具直到 `max_total_rounds` 耗尽
  - 单工具 CollectExecutor 场景首轮收集成功后 100% 浪费
  - 当前通过 `max_tool_rounds=1` + 早退 break 缓解，多工具场景依赖硬上限
- 工作计划:
  - 引入 `finish_task` 工具：LLM 完成后主动调用声明结束，循环检测到后立即终止
  - 作为通用终止方案，统一单工具和多工具场景
  - 影响面: `client.py:_generate_with_tools` 循环终止条件、后台 Agent prompt
  - 前置条件: 当前 `max_tool_rounds=1` 已覆盖，本条为架构扩展预留

### [B-260513-a1b2c3] 统一消息存储：多源消息汇总入库 + LLM 工具检索
- 创建: 2026-05-13
- 问题表现:
  - 当前消息分散存储在三套表：`messages`（私聊对话）、`group_conversations`（群聊对话）、`observation_buffer`（旁听暂存）
  - 非对话指令（掷骰 `.r`、规则查询 `.q` 等）完全不落库，LLM 无法感知用户的指令行为
  - 消息发送结果（成功/失败）仅在 `_persist_assistant_message` 写入后隐式成功，发送失败时无记录
  - LLM 的 `search_chat_history` 工具只能检索 `messages` 表，不能跨群聊/私聊或查看掷骰历史
  - 数据模型不统一（`Message` vs `GroupConversation` vs `Observation`），每加一个数据源需新增表 + 独立 API
- 工作计划:

  **1. 数据层 — 统一消息表**
  - 新建 `unified_messages` 表：
    ```
    id, user_id, group_id, role, type, content, display_name,
    sent_ok, tool_call_id, created_at
    ```
  - `role`: `user` / `assistant` / `system` / `tool`
  - `type`: `chat`（对话消息）、`dice_command`（`.r 3d6` 之类）、`system_notice`（系统指令注入，如 [[系统指令]]）、`send_result`（发送状态回执）
  - `sent_ok`: `True` / `False` / `NULL`（非 assistant 消息无意义时为 NULL）
  - `group_id` 为空表示私聊；有值为群聊
  - 该表替代现有的 `messages`、`group_conversations`、`observation_buffer` 三张表

  **2. 采集层 — 全量消息拦截**
  - 在 message pipeline / command 层统一 hook：所有用户消息（含掷骰指令、规则查询等）在进入具体 handler 之前先写入 `unified_messages`
  - 消息写入时机：前置 hook（handler 执行前），`sent_ok` 初始为 NULL，发送结果后续回填
  - 现有采集点迁移：
    - `chat()` 中的 `store.add_message()` / `store.add_group_conversation()` → 改为写 `unified_messages`
    - `_persist_assistant_message()` → 改为写 `unified_messages` 并回填 `sent_ok`
    - `command.py` 中的 `_group_chat_recorder()` → 改为写 `unified_messages`

  **3. 保留策略 — 滚动 + 保底**
  - 总容量上限：每 `(user_id, group_id)` 组合保留最近 N 条（默认 1000）
  - 保底：每个群/私聊至少保留最近 M 条（默认 50），即使超出总上限
  - 清理触发：写入后检查，超出上限时按 `created_at` 删最旧的
  - 独立于 LLM 上下文截断（`max_history_turns` / `max_history_tokens`），后者继续在获取时做窗口截断

  **4. LLM 数据消费**
  - 对话上下文构建（`_fetch_short_term_history` → `format_history` → `truncate_by_turns`）：
    - 从 `unified_messages` 读取，筛选 `type=chat`（或包含 `type=chat` + `type=dice_command`，待讨论）
    - LLM 能看到同一会话中的掷骰结果等非对话内容，但通过 `role`/`type` 区分不是 bot 发出的
  - 工具检索（`search_chat_history`）：
    - 扩展为跨会话查询，支持按 `type` / `user_id` / `group_id` / 时间范围筛选
    - 返回精简摘要而非原文全文（字符上限控制）

  **5. observe_group 存废**
  - 如果保留：将现有的 `observation_buffer` 采集 path 改为从 `unified_messages` 表筛选：
    ```sql
    WHERE group_id = ? AND type = 'chat' AND NOT (role = 'assistant')
          AND created_at > ? ORDER BY created_at DESC
    ```
    阈值逻辑（`observe_min_length` / `observe_initial_threshold` 等）不变
  - 如果取消：直接删除 `observation_buffer` 表、`observe_group_enabled` 配置项、`command.py` 中的 `_group_chat_recorder` 旁听分支

  **6. 迁移策略**
  - 旧表数据（`messages` / `group_conversations` / `observation_buffer`）做一次性迁移到 `unified_messages`
  - `observation_buffer` 在迁移后直接删除（无论 observe_group 是否保留，其数据均归入统一表）
  - `messages` / `group_conversations` 保留旧表作为回滚备份，后续版本删除

  **7. 配置变更**
  - PersonaConfig 新增：
    - `unified_message_max_total: int = 1000` — 每 `(user_id, group_id)` 总条数上限
    - `unified_message_min_retain: int = 50` — 每 `(user_id, group_id)` 保底保留条数
  - `observe_group_enabled` 暂保留，取决于 observe_group 存废讨论结论

