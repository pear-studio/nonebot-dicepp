# 延后项 Backlog

记录所有需要后续 PR 处理的延后项。
对应实现 commit 自行删除条目；脚本只负责追加与排序。

每条包含：
- **问题表现**：症状、错误日志、量化指标、复现路径
- **工作计划**：可能的修复方向、需先验证的假设、影响面、风险点

---

## persona

### [B-260507-7127d2] add_group_conversation 在 post_send_hook 回调中事务嵌套报错
- 创建: 2026-05-07
- 问题表现:
  - 日志原文: "旁听群消息写入失败: cannot start a transaction within a transaction"
  - 触发路径: `_group_chat_recorder` (command.py:115) → `add_group_conversation` (store.py:271) → `BEGIN` 失败
  - `store.py:284` 显式 `BEGIN`/`commit` 用于保证 INSERT + 裁剪 DELETE 在同一事务
  - aiosqlite 异步共享连接，若外层已有事务，再次 `BEGIN` 失败
  - 后果: 该次群聊消息漏写，无法回放
- 工作计划:
  - 加 stack trace 日志定位调用栈，在 dev 复现一次（需制造并发写入）
  - 方案 A: `add_group_conversation` 改用 SAVEPOINT 兼容嵌套
  - 方案 B: 拆掉显式事务，依赖 aiosqlite 隐式 autocommit（需评估裁剪与 INSERT 不在同事务的一致性影响）
  - 方案 C: 裁剪改为异步任务，与 INSERT 解耦
  - 影响面: `data/store.py:add_group_conversation`、所有显式调用路径（command.py、chat/session.py 多处）

### [B-260507-9b9094] 评分失败持久化记录与触发条件改造
- 创建: 2026-05-07
- 问题表现:
  - `persona_score_history` 最后一条停留在 2026-04-19，之后用户共 12 条消息、6 轮对话均未触发评分
  - 触发条件 `len(messages) >= scoring_interval * 2 = 10`，多用户场景下刚好卡边缘
  - 失败路径: `_process_batch_scoring` 异常仅 `logger.warning`，无持久化，旧容器日志已丢失，根因无从回溯
- 工作计划:
  - 新增 `persona_scoring_failures` 表（user_id/group_id/error/raw_response/created_at）持久化失败记录
  - 评估触发条件改造: 时间窗口（24h 内有对话即触发）或下调 `scoring_interval`
  - 需先在 dev 加日志复现一次评分失败路径，确认是 LLM 返回异常 / 解析失败 / 超时
  - 影响面: `chat/session.py:_process_batch_scoring`、`data/store.py` 表结构、配置项

### [B-260507-a78174] 主动消息阈值默认值评估与 proactive_greeting_schedule 落地或下线
- 创建: 2026-05-07
- 问题表现:
  - `share_desire` 阈值: 4月27日后所有事件 share_desire ≤ 0.45，低于默认 0.5，事件分享完全停止；目前 1276920536 已临时覆盖到 0.3
  - 想念阈值: 用户好感度 39.9 vs `proactive_miss_min_score` 默认 40.0，刚好卡阈值下永不触发想念
  - 死配置: `proactive_greeting_schedule` 在 `core/config/pydantic_models.py` 有 default 定义（5 个时段），但全代码仓库未被消费（grep 0 结果）
- 工作计划:
  - 收集 1~2 周内 share_desire 分布与好感度变化数据，决定 `proactive_event_share_threshold` / `proactive_miss_min_score` 是否下调到 0.3-0.4 / 35-38
  - `proactive_greeting_schedule` 二选一：实现 scheduler 消费这份配置（character_life 已有 wake_up/good_night 槽位但与配置脱节），或从 pydantic_models 删除该字段
  - 影响面: scheduler、character_life、PersonaConfig；需确认覆盖文件 `config/bots/*.json` 不会破坏

### [B-260507-d3cc8b] 事件生成 LLM 调用超时策略与 fallback 事件 delta 兜底
- 创建: 2026-05-07
- 问题表现:
  - 4月30日 19:38 一次 MiniMax-M2.7 调用 134 秒后返回 error；推测为服务端排队而非生成
  - 上层 fallback 事件: "我正在房间里休息。"，无 delta（energy/mood/health 全 None），无内容质量
  - 后果: 后续事件链无状态变化，角色状态长期停滞
  - 现状: `client.py` 已有 `max_retries=3` 指数退避（默认 timeout=30），事件生成上层调用未消费 / 未配置长 timeout
- 工作计划:
  - 评估 auxiliary 模型 timeout 默认值是否上调到 60-90 秒
  - 上层是否引入轻量重试（独立于客户端层）：1 次重试，避免单点 fallback 事件
  - fallback 事件携带默认 delta（如 energy=-1, mood=0）兜底，避免状态断层
  - 影响面: `life/character_life.py` 事件生成路径、PersonaConfig 超时项、fallback 构造代码

### [B-260507-f9ea98] 厂商适配层根据模型类型选择 prompt 注入角色（system/user/developer）
- 创建: 2026-05-07
- 问题表现:
  - `_on_segment_round_complete` 全局硬编码 `user` 角色注入纠正消息
  - 短期 fix 已将前缀改为 `[内部指令: ...]`，但角色仍是 user
  - 兼容约束: MiniMax 等厂商不支持 mid-conversation system，必须用 user 兜底
  - 风险: LLM 可能将 `[内部指令: ...]` 文本误识为用户输入，影响对话连贯性
- 工作计划:
  - 在 ContextBuilder 或厂商适配层加模型类型分支：支持 system/developer 角色的厂商使用对应角色，MiniMax 等保留 user 注入路径
  - 需先观察现有 LLM 是否真的频繁误识 `[内部指令]`，决定是否提前优先级
  - 影响面: ContextBuilder、厂商 adapter、segment dispatcher

