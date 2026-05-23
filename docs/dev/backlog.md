# 延后项 Backlog

记录所有需要后续 PR 处理的延后项。
对应实现 commit 自行删除条目；脚本只负责追加与排序。

每条包含：
- **优先级**：P0(阻塞)/P1(应该修)/P2(可修可不修)
- **类型**：bug / feature / refactor
- **改动量**：S(<30行单文件) / M(<300行单模块) / L(300~999行单模块) / XL(≥1000行或跨模块)，不含测试和文档行数
- **问题表现**：症状、错误日志、量化指标、复现路径
- **工作计划**：可能的修复方向、需先验证的假设、影响面、风险点

---

## persona

### [B-260522-6a8ed6] generate_image tool description 引导过弱，LLM 不使用 SELF_APPEARANCE 导致角色外貌丢失
- 创建: 2026-05-22
- 优先级: P1
- 类型: bug
- 改动量: S
- 问题表现: tool description 中引导语气过弱(可使用)，LLM 不引用角色外貌占位符，最终图片 prompt 缺少角色特征，生成图片主角不对
- 工作计划: 强化 tool description：展示外貌描述原文 + 明确引导 + 说明原因 + 纯风景例外。改 generate_image.py 的 make_generate_image_tool_def

### [B-260522-8859d5] 事件/日记生成 LLM prompt 缺少真实日期，导致日记月份错误（11月 vs 5月）
- 创建: 2026-05-22
- 优先级: P1
- 类型: bug
- 改动量: S
- 问题表现:
    - 日记内容日期为"11月21日~25日"对应真实日期 5月18~22日，LLM 自行推断起始月份后每天递增
    - 事件描述中也出现"11月23日。下午。"等错误日期
    - 根因：event_agent.py 的 generate_event_result（L256）和 generate_diary（L531）的 user_prompt 均未注入真实日期
    - 前一天日记作为上下文传给 LLM，错误日期自我强化
- 工作计划:
    - 在 generate_event_result 的 user_prompt 中加上"当前日期: 2026年5月23日"
    - 在 generate_diary 的 user_prompt 中加上日期
    - 影响面：life/event_agent.py:256~257 user_prompt、life/event_agent.py:531~537 user_prompt
    - 风险：低，纯 prompt 修改，不影响数据流

### [B-260522-8dcb27] 日报"主动消息覆盖"统计口径错误——统计全部 bot 消息而非主动消息
- 创建: 2026-05-22
- 优先级: P1
- 类型: bug
- 改动量: S
- 问题表现:
    - get_daily_message_stats（data/store.py:268）统计 role='assistant' AND type!='system_log' 的全部消息
    - 5月22日日报显示 67 条，实际构成：64 条命令响应 + 3 条被动聊天回复，真正主动消息为 0
    - 日报第三段 _collect_proactive_coverage（daily_report.py:318）标签为"主动消息覆盖"，严重误导
    - 该统计实际反映 bot 全局回复量，与"主动消息"无关
- 工作计划:
    - 方案A（推荐）：将日报标签从"主动消息覆盖"改为"Bot 消息覆盖"，SQL 不变
    - 方案B：新增专门统计主动消息的字段，需在 message_stream 中区分主动/被动消息
    - 影响面：data/store.py:268 get_daily_message_stats、report/daily_report.py:318 _collect_proactive_coverage
    - 风险：低，只改标签文案

### [B-260522-97227f] minimax_image 错误码 2013 误判为不可重试，参数错误应允许重试
- 创建: 2026-05-22
- 优先级: P1
- 类型: bug
- 改动量: S
- 问题表现: MiniMax image-01 对参数错误（prompt length must be less than 1500）也返回 code=2013，被 classify_error 笼统判为 NON_RETRYABLE，导致 provider 被永久标记 dead，后续所有图片请求失败
- 工作计划: minimax_image.py 的 classify_error 对 2013 做细分：status_msg 含 content/moderation/审核 → NON_RETRYABLE，含 params/invalid/length → RETRYABLE

### [B-260515-dd50eb] 用户自带 API Key 功能（.ai key config）
- 创建: 2026-05-15
- 优先级: P2
- 类型: feature
- 改动量: L
- 问题表现: 用户可通过 .ai key config 命令提供自己的 LLM API key，覆盖全局配置。涉及计费体系、配额管理、滥用防护等一整套体系，当前设计对安全边界覆盖不足。该功能与 provider 路由重构的候选池调度、熔断器、探针等核心机制耦合过深，增加了不必要的复杂度。当前从本分支 scope 中移出，需求保留待后续独立实施。
- 工作计划: 独立设计用户 Key 管理子系统：安全存储（加密）、配额追踪、滥用检测。实现 UserLLMConfig 数据模型（含 API key 加密存储）。实现 .ai key config 命令交互流程。Router 层集成用户 Key 覆盖逻辑（优先级高于全局配置）。影响面: module/persona/data/models.py、module/persona/command.py、module/persona/llm/router.py。

## roll

### [B-260520-be2315] 掷骰引擎统一 — 删除 Legacy 引擎，全量迁移到 AST 引擎
- 创建: 2026-05-20
- 优先级: P1
- 类型: refactor
- 改动量: L
- 问题表现:
    - 两套完整掷骰引擎共存: expression.py (870行，正则) 与 ast_engine/ (10文件，Lark AST)
    - ast_engine/adapter.py (327行) 作为运行时 demux 桥接层
    - legacy/ 目录为空，legacy_adapter.py 中 _LEGACY_ENABLED=False (正常路径死代码)
    - 5个 module 子目录通过 module.roll 重导出使用，无法确定实际走哪个引擎
- 工作计划:
    - 将所有调用方迁移到 from module.roll.ast_engine.adapter import exec_roll_exp_unified
    - 删除 module/roll/expression.py
    - 删除 module/roll/legacy/ 和 ast_engine/legacy_adapter.py
    - 影响面: module/deck, module/character/base, module/character/dnd5e, module/initiative, module/persona/tools, module/roll/roll_dice_command.py
    - 风险: 旧引擎某些边界表达式行为可能与 AST 引擎有差异，迁移前需全量对比测试

