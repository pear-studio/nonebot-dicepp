# 延后项 Backlog

记录所有需要后续 PR 处理的延后项。
对应实现 commit 自行删除条目；脚本只负责追加与排序。

每条包含：
- **问题表现**：症状、错误日志、量化指标、复现路径
- **工作计划**：可能的修复方向、需先验证的假设、影响面、风险点

---

## persona

### [B-260507-3a7763] proactive_greeting_schedule 死配置删除
- 创建: 2026-05-07
- 问题表现:
    - ProactiveGreetingEntry 类与 _default_proactive_greeting_schedule (5 个时段) 仅在 src/plugins/DicePP/core/config/pydantic_models.py:14-46 定义
    - PersonaConfig.proactive_greeting_schedule 字段 (pydantic_models.py:167) 全仓库零消费者
    - 代码注释 pydantic_models.py:166 已自承 "定时事件配置已迁移到角色卡 extensions.scheduled_events"
    - 文档 docs/dicepp/persona/config-example.md:71 已标 DEPRECATED 且备注 "被调度器 redesign 忽略"
    - config/global.json:133 仍写有 proactive_greeting_schedule 默认值条目
- 工作计划:
    - 删除 src/plugins/DicePP/core/config/pydantic_models.py 中 ProactiveGreetingEntry 类、_default_proactive_greeting_schedule、PersonaConfig.proactive_greeting_schedule 字段
    - 删除 config/global.json 中 proactive_greeting_schedule 条目
    - 移除 docs/dicepp/persona/config-example.md 中 DEPRECATED 行
    - 影响面: 仅死代码删除, character_life 已有 wake_up/good_night 槽位机制不受影响
    - 风险: 低 (零消费者), 但需确认 bot 配置文件 config/bots/*.json 不会因严格模式 pydantic 校验报错

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

### [B-260507-d4b350] 消息发送路径统一（合并 send_segmented 与 send_now）
- 创建: 2026-05-07
- 问题表现:
  - 症状: MessagePort 同时存在 send_segmented 与新增的 send_now，行为重复
  - 现场: 三处 send_segmented 调用方（ChatSession._coordinator_on_result、PersonaApp.send_message、LifeSimulator._send_msg）均为 [make_segment(content, group_id)] 单段形态，与 send_now 行为几乎一致
  - 影响: 分段路径走 dispatcher → send_now，非分段路径走 send_segmented，形成两套并存的发送路径；语义割裂，可观测性不一致，新调用方需在两个 API 间二选一
- 工作计划:
  - 方向: 统一走 SegmentDispatcher 模型，让分段与非分段共享同一调度/失败回调/可观测路径
  - 步骤:
    1. 评估 send_segmented 三处调用方能否迁移到 dispatcher（含 life 主动消息的 delay 与失败回调语义）
    2. 设计统一发送接口（扩展 dispatcher 支持非分段单条消息或并入 send_now）
    3. 删除 send_segmented，迁移所有调用方，更新测试
  - 影响面: persona 模块所有出口消息（chat 非分段、PersonaApp.send_message、life 主动消息）；测试 test_message_port、test_life_simulator
  - 风险: life 主动消息原本是 fire-and-forget 后台 task，迁移 dispatcher 同步等待语义需保留或显式重设计

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

### [B-260508-1f1b9e] 消息发送路径统一（合并 send_segmented / send_now / dispatcher）
- 创建: 2026-05-08
- 问题表现:
    - `send_segmented` 三处调用方（`ChatSession._coordinator_on_result`、`PersonaApp.send_message`、`LifeSimulator._send_msg`）当前都是 `[make_segment(content, group_id)]` 单段形态，行为与 `send_now` 重叠
    - 分段路径走 `dispatcher → send_now`，非分段路径走 `send_segmented`，形成两套并存的发送路径，调度/失败回调/可观测点分裂
    - 同一"发送"语义有两套实现，未来扩展失败重试、限流、监控时需要双写
- 工作计划:
    - 修复方向：评估三处调用方是否都能迁移到 dispatcher（含 life 主动消息的 delay 与失败回调语义）；设计统一发送接口（扩展 dispatcher 支持非分段单条 / 合并到 send_now / 让 send_segmented 内部走 dispatcher）；删除 `send_segmented`，迁移所有调用方，更新测试
    - 影响面：persona 模块所有出口消息（chat 非分段、`PersonaApp.send_message`、life 主动消息）
    - 风险点：需保证 life 主动消息行为不退化（delay/失败回调语义一致性）；何时拉起：分段回复主线合入并稳定运行后单独立项推进

### [B-260508-7f130e] proactive_miss_min_score 默认值卡边界(40.0→38.0)
- 创建: 2026-05-08
- 问题表现:
    - 用户好感度 39.9 vs `proactive_miss_min_score` 默认 40.0,卡阈值下永不触发想念消息(pydantic_models.py:127、config/global.json:131 仍为 40.0)
    - a78174 PR 完成 share_desire 阈值与 greeting_schedule 死配置删除后,miss_min_score 仍未动,边界好感度用户(39-40 区间)体验损失持续累积
    - 风险与 share_threshold 调整同档(数据驱动数值微调,无 schema 变更),无工程理由继续延后
- 工作计划:
    - 调整方向:`proactive_miss_min_score` 默认 40.0 → 38.0,与 share_threshold 同方法线上数据校准
    - 影响面:pydantic_models.py:127、config/global.json:131、docs/dicepp/persona/config-example.md 默认值表
    - 风险点:与 `proactive_miss_enabled` / `proactive_miss_min_hours` 联动需复核;何时拉起取决于 1-2 周线上 39-40 区间用户分布与触发率数据

### [B-260508-eb32c9] 好感度阶段-想念-衰减联动重构
- 创建: 2026-05-08
- 问题表现:
    - 当前好感度数值含义模糊：初始值30、想念阈值40、衰减下限50（=初始+20）三者之间无关联，各说各的
    - 情感节奏不合理：高好感度用户长时间不互动，系统直接扣好感度；应先主动表达想念，被忽视后再伤心减分
    - 想念消息触发概率使用连续公式 `0.40 + 0.40 * (score/100)`，不够直观，且亲密关系（80+）也无法100%触发
- 工作计划:
    - 重构好感度阶段定义，统一数值与行为含义：冷淡(0-20)/疏远(20-40)/友好(40-60)/默契(60-80)/亲密(80-100)
    - 新增 `last_miss_sent_at` 字段到 `RelationshipState`，实现"开关型"衰减：想念消息发出前不衰减，发出后用户未回应才开始正常衰减
    - 将想念概率改为阶段固定值：疏远50%/友好70%/默契90%/亲密100%
    - 调整 `miss_min_score` 配置从40改为20
    - 更新衰减计算逻辑，衰减下限改为当前阶段下限（取代 `initial_score + floor_offset`）
    - 亲密用户（80+）长时间不互动最多掉到80，可永久保持亲密阶段
    - 补充/更新相关测试

