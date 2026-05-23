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

### [B-260523-4e0a8b] 5月22日 CharacterLife 全天仅触发 1 个事件（22:31），正常应为 8-12 个
- 创建: 2026-05-23
- 优先级: P1
- 类型: bug
- 改动量: S
- 问题表现:
  - 5月22日仅 22:31 触发 1 个 system 事件，无 wake_up，无 good_night，正常日期（5/15-21）稳定产出 8-12 个
  - 同日 bot 聊天功能正常（10:17~16:46 有对话），LLM router 可用（minimax 正常），说明不是全局故障
  - 事件缺失导致当日主动消息为 0，用户收不到角色主动消息
  - 根因推断：PersonaCommand.tick() at-most-once 异步任务模式——同一时间只允许一个 tick 在跑，若 LLM 调用长时间阻塞则后续槽位全部跳过
  - 同日报显示 deepseek: 4/4 错误，若事件生成路由到 deepseek 可能触发超时链
  - 无运行时日志，无法确认精确阻塞时段
- 工作计划:
  - 已在 command.py/simulator.py/character_life.py 三个文件补充诊断日志：tick 耗时（>10s 告警）、character_life 耗时（>60s 告警）、槽位触发详情（slot 编号/计划时间/剩余数）、事件链生成耗时
  - 等下次复现时通过日志定位根因，再决定结构性修复方向
  - 候选修复：给 _run_tick 加总体超时（600s）、将 character_life 和 scheduler 拆成独立任务、或引入 watchdog 心跳

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

