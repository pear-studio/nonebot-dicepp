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

## bot

### [B-260520-b6f605] 从 Bot 中提取 TaskScheduler 模块，消除 todo_tasks 耦合
- 创建: 2026-05-20
- 优先级: P1
- 类型: refactor
- 改动量: M
- 问题表现:
  Bot 持有 todo_tasks: Dict 并直接操作其内部结构，命令层可随意篡改（master_command 直接 todo_tasks={} 清空）
  process_async_task 原地修改可变 bot_commands 列表，接口副作用不透明
  register_task / process_async_task 无法独立测试，必须构造完整 Bot 实例
  shell/bot_runner 直接调用 process_async_task，绕过 tick_loop 的正常路径
- 工作计划:
  新建 core/bot/task_scheduler.py: TaskScheduler 类
    - schedule(task, is_async, timeout, timeout_callback) 注册任务
    - async process(free_time) -> List[BotCommandBase] 纯返回值，消除副作用
    - clear_all() 替代直接清空 dict
    - error_handler 通过构造函数注入，dice_log 直接 import
  修改 core/bot/dicebot.py: 移除 todo_tasks/register_task/process_async_task，Bot 持有 self._scheduler
  迁移 4 个命令调用方: register_task -> bot._scheduler.schedule
  迁移 shell/bot_runner.py: process_async_task -> bot._scheduler.process
  影响面: core/bot/dicebot.py, master_command.py, persona/command.py, roll_dice_command.py, shell/bot_runner.py
  风险: process() 返回值合并语义需与 tick_loop 的 bot_commands 累积逻辑对齐

## persona

### [B-260521-a751ba] persona_llm_traces 表缺少 selected_provider 列
- 创建: 2026-05-21
- 优先级: P0
- 类型: bug
- 改动量: S
- 问题表现: 每次写入 LLM trace 时触发 OperationalError: table persona_llm_traces has no column named selected_provider
- 工作计划: 在 data/migrations.py 新增迁移，为 persona_llm_traces 表添加 selected_provider TEXT 列

### [B-260522-6a8ed6] generate_image tool description 引导过弱，LLM 不使用 SELF_APPEARANCE 导致角色外貌丢失
- 创建: 2026-05-22
- 优先级: P1
- 类型: bug
- 改动量: S
- 问题表现: tool description 中引导语气过弱(可使用)，LLM 不引用角色外貌占位符，最终图片 prompt 缺少角色特征，生成图片主角不对
- 工作计划: 强化 tool description：展示外貌描述原文 + 明确引导 + 说明原因 + 纯风景例外。改 generate_image.py 的 make_generate_image_tool_def

### [B-260522-97227f] minimax_image 错误码 2013 误判为不可重试，参数错误应允许重试
- 创建: 2026-05-22
- 优先级: P1
- 类型: bug
- 改动量: S
- 问题表现: MiniMax image-01 对参数错误（prompt length must be less than 1500）也返回 code=2013，被 classify_error 笼统判为 NON_RETRYABLE，导致 provider 被永久标记 dead，后续所有图片请求失败
- 工作计划: minimax_image.py 的 classify_error 对 2013 做细分：status_msg 含 content/moderation/审核 → NON_RETRYABLE，含 params/invalid/length → RETRYABLE

### [B-260522-a8953d] 图片生成 prompt 超 1500 字符限制，需配置化 + 截断 + LLM 重试
- 创建: 2026-05-22
- 优先级: P1
- 类型: bug
- 改动量: S
- 问题表现: 七七 image_gen_appearance ~1050 字符 + image_gen_style ~150 + LLM prompt，超过 MiniMax image-01 的 1500 字符上限，导致生成失败并把 provider 标记为 dead
- 工作计划:
  1. global.json persona_ai 新增 image_gen_prompt_max_chars 配置项（默认 1500）
  2. executor 超限时返回明确错误信息给 LLM（含当前长度、上限），让 LLM 缩短 prompt 重试
  3. 缩短角色卡 image_gen_appearance 到 ~450 字符，只保留核心视觉锚点（体型、紫发、粉瞳、紫色衣裙、符咒、绷带袜、珠串、笔记）

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

