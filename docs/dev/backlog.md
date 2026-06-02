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

### [B-260602-e78f1d] LLM 回复带时间戳前缀 [HH:MM 刚刚]
- 创建: 2026-06-02
- 优先级: P1
- 类型: bug
- 改动量: S
- 问题表现: bot 回复自动带 [HH:MM 刚刚] 前缀，如 [15:42 刚刚] ……下午好。根因: context.py _format_private_history 和 _format_group_history 给 assistant 历史消息注入了 format_timestamp 时间戳前缀 (context.py:280-287, 265-273, session.py:441-445)。LLM 看到自己的历史回复都以此格式开头，学会模仿。这是 prompt 格式设计问题，assistant 历史消息不应带时间戳。
- 工作计划: 在 _format_private_history / _format_group_history 中，仅给 user 消息注入时间戳前缀，assistant 消息不加。影响面: context.py 两个 format 方法

### [B-260602-4263c4] user_stat/group_stat 的 read-modify-write 写竞争
- 创建: 2026-06-02
- 优先级: P1
- 类型: bug
- 改动量: M
- 问题表现: 多路径对 user_stat/group_stat 做全量覆写，tick_daily 的 daily_update() 可能被 stale 数据覆盖，update_group_info_all 修改 meta 字段同理。涉及路径：process_message（dicebot.py:577-585,735）、tick_daily（line 282-310）、update_group_info_all（line 856-866）、record_roll_stat（roll_dice_command.py:527-556）。meta_stat 同类竞争已修复（单一写者），这些路径仍有窗口。
- 工作计划: 修复方向同 meta_stat 单写者模式或 Repository 原子更新：将 user_stat/group_stat 的读-改-写收敛到单一路径，或用原子 upsert 替代全量覆写。需先梳理各写路径的字段修改交集。

### [B-260602-58712e] 图片对话链路修复：capability 缺失 + 纯图片不触发
- 创建: 2026-06-02
- 优先级: P1
- 类型: bug
- 改动量: M
- 问题表现: 用户发图后机器人无法看图回复，断裂在两个环节: (1) MiniMax M3 支持多模态但 global.json 的 capabilities 只有 ['text','tool_calls'] 缺 image_input，CHAT_WITH_IMAGE policy 要求 (text, tool_calls, vision) 做 capability 交集时匹配不上 → 无候选模型 → ServiceUnavailableError (2) 纯图片消息: nonebot_adapter 用 extract_plain_text() 提取 plain_msg 为空字符串，can_process_msg 中 cmd=='' 直接 return False 拒绝处理 (command.py:345)，图片消息被静默丢弃，虽然 _inbound_message_recorder 下载了图片但 process_msg 未被调用
- 工作计划: 修复(1): global.json 给 MiniMax-M3/M3-t 加 image_input capability，并确认 selection.py CHAT_WITH_IMAGE 的 capability 名与 config 一致。修复(2): can_process_msg 中 cmd 为空但 raw_msg 含 CQ image 段时，不应 return False，应返回 True 触发后续处理。两个子任务可独立实施，但需一起验收闭环。影响面: config/global.json, command.py can_process_msg

### [B-260602-186086] LLMRouter 启动日志计数未过滤 disabled 的 provider/model
- 创建: 2026-06-02
- 优先级: P2
- 类型: bug
- 改动量: S
- 问题表现: _build_providers 注册时未检查 pconfig.enabled / mconfig.enabled，关闭 deepseek/mimo 后启动日志仍汇报 9 LLM 模型和 4 providers，与实际情况不符，用户无法从启动日志判断哪些 provider 真正生效
- 工作计划: 在 _build_providers 外层跳过 pconfig.enabled == False 的 provider，内层跳过 mconfig.enabled == False 的 model。影响面: router.py 的 _build_providers 一个方法

### [B-260601-ef9e5a] 用户自带 API Key 功能（.ai key config）
- 创建: 2026-06-01
- 优先级: P2
- 类型: feature
- 改动量: M
- 问题表现:
  当前 .ai key config 命令返回"升级中，暂不可用"，用户无法配置自己的 API Key。
  - command.py:436 硬编码了占位回复
  - errors.py:163 已提示用户使用 .ai key config 配置 API Key 可解除限制，但功能未实现
  - data/models.py 已有 primary_api_key / auxiliary_api_key 字段，但缺少命令入口和路由集成
  - 所有对话只能使用全局 provider 配置，用户无法配置自有 key 来解除限流或使用自己的额度
- 工作计划:
  实现 .ai key config 命令，允许用户配置自己的 API Key：
  - 实现 command.py 中的 key config 子命令（设置/查看/删除）
  - 加密存储用户 API Key 到数据库（复用 data/models.py 已有字段）
  - LLM 路由中优先使用用户自有 key（若已配置），回退到全局 provider
  - 影响面：command.py、data/store.py、llm/router.py
  - 风险点：用户 key 的安全存储与传输，key 校验机制

