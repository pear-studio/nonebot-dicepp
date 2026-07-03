# 延后项 Backlog

记录所有需要后续 PR 处理的延后项。
对应实现 commit 自行删除条目；脚本只负责追加与排序。

每条包含：
- **优先级**：P0(阻塞)/P1(应该修)/P2(可修可不修)
- **类型**：bug / feature / refactor
- **改动量**：S(<30行单文件) / M(<300行单模块) / L(300~999行单模块) / XL(≥1000行或跨模块)，不含测试和文档行数
- **问题表现**：症状、错误日志、量化指标、复现路径
- **开发备忘**：历史背景、相关线索、可能方向（仅供参考，agent 应独立诊断，允许推翻）

---

## dashboard

### [B-260623-6f9e85] 缺少总览/概览 tab 聚合核心数据指标
- 创建: 2026-06-23
- 优先级: P2
- 类型: feature
- 改动量: M
- 问题表现:
    - 当前 6 个 tab 各自独立，没有一个 overview/dashboard 页面
    - 无法一眼看到核心指标（在线 bot 数、最近错误、配额使用、配置变更等）
    - 需要逐个 tab 点开查看，体验分散
- 开发备忘:
    - 新增 overview tab（放在 tabs 数组第一位）
    - 聚合展示：bot 在线状态卡片、最近审计日志摘要、配置覆盖统计、最近错误计数等
    - 后端可能需要新增 /api/overview 聚合 endpoint，或前端组合现有 API 调用
    - 影响面：dashboard.html（新 tab UI + 数据获取逻辑）、可能新增 app.py endpoint
    - 风险：低，纯增量功能；注意 API 调用数量和性能

### [B-260623-84b827] SSE 端点缺少流式集成测试
- 创建: 2026-06-23
- 优先级: P2
- 类型: refactor
- 改动量: S
- 问题表现: tests/dashboard/test_sse.py 缺少对 /api/events 端点的端到端流式测试（连接→接收初始状态→断开清理），当前仅测试 auth 和 broadcast 机制
- 开发备忘: 使用 httpx.AsyncClient + ASGITransport 编写异步流式测试。低优先级，broadcast 机制已通过 TestBroadcast 测试验证

## persona

### [B-260622-0ed4e3] DM 层接管事件生成：裁决权、隐藏设定与叙事线索管理
- 创建: 2026-06-22
- 优先级: P1
- 类型: refactor
- 改动量: XL
- 问题表现:
  - 事件生成（`EventGenerationAgent.generate_event_result`）当前 prompt 自称"世界观设定专家"，但实际没有 DM 的裁决权和隐藏信息
  - `Character.scenario` 字段默认为空字符串，事件生成 prompt 中场景 fallback 为硬编码 "日常生活"，所有事件共享同一场景上下文，缺乏叙事方向
  - 角色反应中的 `follow_up_action` 直接注入下一环事件的 scenario，角色企图直接兑现为事件走向。中途没有不确定性——角色想去采药就一定能采到，不会遇到意外
  - 缺少长线叙事记忆：DM 不知道当前有哪些线索在推进、进展到哪了。每次事件生成是独立的，产出趋于流水账和随机事件
  - 没有从状态数值到叙事意义的转换——体力低是一个数字，DM 不据此调整事件走向
  - `_slot_type_hint` 中 "wake_up 恢复规则：体力自然恢复，energy_delta 保底 +20" 导致 LLM 习惯性填满 delta 上限，数值区分度不足
- 开发备忘:
  DM 定位：世界观层，拥有裁决权，知道角色不知道的隐藏设定。角色只能产生"企图"（follow_up_action / pending_plan），DM 裁决企图的执行结果。

  流程设计：tick → DM 读取角色状态 + DM 备忘（permanent_state DM 区）+ 角色企图 → DM 裁决：产出事件（可能产出一连串叙事链规划）→ 角色生成 reaction + 新的企图 → 循环。DM 产出的叙事链是柔性参考，非时间表——下次 tick 重新裁决。

  DM 备忘：自由文本，存在 permanent_state 中。LLM 自行管理——创建线索、推进、合并重复线索、完结、归档。线索整体数量不限，但 focus 上限约 3 条，其余闲置。不结构化，让 LLM 自行把握。

  scenario 清理：删除 "日常生活" fallback 和 `Character.scenario` 空字符串依赖。事件生成的场景上下文由 DM 动态产出。

  与现有机制的衔接：`pending_plan`/`follow_up_action` 保留——角色仍产生企图，DM 裁决取代直接兑现。`slot_type` 的 wake_up/good_night 保留——起床和入睡是客观时间节点。`recovery_energy` floor 在 DM 架构下重新评估——DM 可依据睡眠质量叙事产出匹配的 delta。

  影响面：`character_life.py`（DM 裁决循环）、`event_agent.py`（prompt 重写为 DM 视角，删除 scenario 相关 prompt）、`character/models.py`（`Character.scenario` 字段评估是否移除）、`collecting.py`（事件参数可能调整）、`permanent_state` 读写路径。

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
- 开发备忘:
  实现 .ai key config 命令，允许用户配置自己的 API Key：
  - 实现 command.py 中的 key config 子命令（设置/查看/删除）
  - 加密存储用户 API Key 到数据库（复用 data/models.py 已有字段）
  - LLM 路由中优先使用用户自有 key（若已配置），回退到全局 provider
  - 影响面：command.py、data/store.py、llm/router.py
  - 风险点：用户 key 的安全存储与传输，key 校验机制

### [B-260623-7412ec] MiniMaxImageProvider.probe() 适配错误分类，避免配额/鉴权类无意义重试
- 创建: 2026-06-23
- 优先级: P2
- 类型: refactor
- 改动量: S
- 问题表现:
    - MiniMaxImageProvider.probe() 仍吞掉所有异常返回 False
    - 若遭遇配额/鉴权类永久错误会进入无意义重试
    - OpenAIProvider（及子类 MiniMaxProvider）已更新为 raise 模式，该 provider 使用 httpx 直连未同步
- 开发备忘:
    - 方向：将 MiniMaxImageProvider.probe() 改为对预期内异常（httpx.TimeoutException）返回 False，其余 raise
    - 影响面：仅限 MiniMax 图生模型的探针路径
    - 风险点：httpx 异常类型与 OpenAI SDK 不同，需确认分类兼容性后再动手

### [B-260702-7b8fc3] AgentRuntime 纠正注入改为元数据标记（非内容匹配），解除 ToolLoop 字符串耦合
- 创建: 2026-07-02
- 优先级: P2
- 类型: refactor
- 改动量: S
- 问题表现: _filter_corrections 依赖硬编码 [系统指令] 字符串前缀识别纠正消息，与 AgentRuntime 注入侧字符串耦合。若注入格式变化过滤静默失效。
- 开发备忘: 在 AgentRuntime 层为纠正消息添加元数据标记（如特殊 role 或 _internal flag），使 ToolLoop._filter_corrections 不依赖内容匹配。影响面: agent/loop.py（注入侧）+ life/tool_loop.py（过滤侧）。

### [B-260630-1f9286] 工具定义与传入规则梳理 — 统一 required_tool 语义和传递路径
- 创建: 2026-06-30
- 优先级: P2
- 类型: refactor
- 改动量: M
- 问题表现: 当前 required_tools 从 tools[0] 隐式推导；SAY_TOOL_DM 与 SAY_TOOL_CHARACTER 共享 name=say 但通过不同路径传递（factory 只注册 DM 版，Character 版仅用 to_openai_format）；工具传递三路并行（_run_life_collect_loop / AgentRuntime.run() / run_structured_collect）；opening() 走独立 AgentRuntime 路径。整体缺乏统一的工具注册、传递与 required 语义。
- 开发备忘: 1. 梳理所有工具定义和调用路径 2. 统一 required_tool 语义（显式传入优于从 tools[0] 推导）3. 统一工具传递路径（收敛 _run_life_collect_loop 和 AgentRuntime 两条线或明确分工）4. 考虑 SAY_TOOL 的 name 冲突解决方案（如 namespace 前缀）

### [B-260630-46af37] compact_conversation 改为 LLM 摘要压缩
- 创建: 2026-06-30
- 优先级: P2
- 类型: refactor
- 改动量: M
- 问题表现:
    - 当前 compact_conversation 仅做 clear() 清空
    - 日终前的所有对话记忆被丢弃而非提炼为叙事线索
    - 跨事件累积的叙事上下文（人物/地点/事件/线索）完全丢失
- 开发备忘:
    - 将 compact 从 clear 改为 LLM summary 压缩
    - 调用一次轻量 LLM 将 _messages 压缩为叙事摘要，保留关键信息
    - 需评估压缩 LLM 的 token 消耗和延时
    - 影响面: life/agent.py compact_conversation()

### [B-260630-38ec7f] share_desire 概念重新设计及 share_threshold 死配置清理
- 创建: 2026-06-30
- 优先级: P2
- 类型: refactor
- 改动量: L
- 问题表现:
    - Phase 2+3 已将 share_desire 从数据模型/CRUD/协议/DB 列全量清理
    - schedule_share 已移除 (Phase 4 R3)，proactive_event_share_threshold 配置（proactive_config.py / simulator.py / pydantic_models.py）变为死配置
    - "角色对事件有分享欲望→触发主动分享"概念本身有价值，当前无替代机制
    - 主动分享仅靠 tick 时间窗口触发，缺少事件级筛选
- 开发备忘:
    - share_desire 重新设计暂不启动，等 SA 条目化 (B-260629-5a1f2c) 落地后系统性设计
    - share_threshold 配置清理: 移除 proactive_config.py 属性及 from_persona_config 映射，移除 simulator.py 传递，清理或标记 DEPRECATED pydantic_models.py 字段
    - 新方案应与 SA 叙事产出和角色状态结合，避免回到独立数值字段旧模式
    - 影响面: proactive_config.py、simulator.py、pydantic_models.py

### [B-260623-4a2c1d] probe 路径感知错误分类，避免配额/鉴权类错误无意义重试
- 创建: 2026-06-23
- 优先级: P2
- 类型: refactor
- 改动量: M
- 问题表现:
    - 当前 `probe()` 返回 `bool`，调用方 `_probe_loop` 只看 True/False，不区分"网络瞬断"和"永久配额耗尽"
    - minimax 429 配额耗尽时 probe 会重试 10 次才进入 exhausted，期间每次无意义重试约 25 分钟
    - `classify_error_kind` 已能正确识别 2056 / rate_limit_error，但 probe 路径不调用它
- 开发备忘:
    - 方向：probe 返回类型从 `bool` 扩展为携带 ErrorKind，或 probe 内部直接调用 `circuit_breaker.mark_dead()` 跳过重试
    - 也可给 probe 使用 `max_retries=0` 的独立 client，让 SDK 不重试直接抛出原始异常（带 body）
    - 波及所有 provider 的 probe 实现和 router 探针循环，需要统一设计
    - 已加 WARNING 级日志辅助判断异常类型，等下次生产环境再现后确认具体异常链再动手

## release

### [B-260615-90ee20] GitHub Release 与多产物发布流程
- 创建: 2026-06-15
- 优先级: P2
- 类型: feature
- 改动量: L
- 问题表现:
    - 已决定 docs/releases/vX.Y.Z.md 作为 release metadata 源头，但未来 GitHub Release body、发布附件、镜像、可能的 Windows exe 产物如何统一发布尚未设计。
    - 当前第一阶段只计划 GHCR Docker 镜像，尚未覆盖桌面/Windows exe、checksums、构建矩阵、手动/自动发布边界等常见发布产物问题。
- 开发备忘:
    - 调研并设计后续 release 流程：以 docs/releases/vX.Y.Z.md 生成或同步 GitHub Release body。
    - 评估是否在 GitHub Release 附加构建产物，如 Windows exe、压缩包、checksums、SBOM 或签名文件。
    - 保持第一版实现克制：先不承诺具体 exe 技术路线，未来可比较 PyInstaller、zipapp、独立 Python runtime、Docker-only 等方案。
    - 需要决定哪些产物由 CI 自动生成，哪些必须人工确认后发布；避免 GitHub Actions 自动部署生产。

### [B-260617-1cc4a4] 改进 PyInstaller 打包结构以减少 hiddenimports 补丁
- 创建: 2026-06-17
- 优先级: P2
- 类型: refactor
- 改动量: M
- 问题表现: Windows rc1 包可生成并通过 --smoke-check，但普通启动时 NoneBot 加载 DicePP 插件失败：ModuleNotFoundError: No module named 'cryptography.fernet'。现场包内只有 cryptography/hazmat/bindings/_rust.pyd 和 dist-info，缺少 cryptography/fernet.py；原因是插件源码主要作为 datas 复制，PyInstaller 没有完整分析 DicePP 插件 import 链。短期可用 collect_submodules('cryptography') 修复，但类似动态依赖仍可能再次漏包。
- 开发备忘: 长期方向：重新梳理 Windows 打包结构，让 DicePP 插件代码尽量作为 PyInstaller 可静态分析的 Python 模块进入 Analysis，而不是主要依赖 datas 复制源码和手写 hiddenimports。需先验证 adapter/module/utils 等当前顶层导入路径是否能迁移或兼容；影响面包括 scripts/build/dicepp.spec、bot.py 的 frozen 路径、插件导入方式、release smoke test。风险点是改动可能影响开发环境插件加载和现有 NoneBot load_plugin 行为，适合在 RC 后续单独处理。

## runtime

### [B-260616-5f74ec] 重新设计 Standalone 无 QQ 服务入口
- 创建: 2026-06-16
- 优先级: P1
- 类型: refactor
- 改动量: L
- 问题表现: 当前 standalone_bot.py 是历史实验入口，混合了无 QQ 服务入口、DiceHub 注册、WebChat 启动和 /dpp runtime 绑定等职责。该模式目前不作为发布路径或新手路径使用，但未来网站可能需要单独部署 DicePP，绕过 QQ / NoneBot 直接提供服务。继续保留根目录入口和旧测试会让它看起来像现役能力，也会把未来重写约束在旧实现上。
- 开发备忘: 近期先将旧 standalone 入口归档为 docs/dev/archive/standalone_bot_legacy.py，删除围绕旧行为的测试，避免维护半成品运行模式。后续实现时重新设计无 QQ / 无 NoneBot 服务入口：明确 CLI/配置入口、HTTP/WebChat 边界、与 DiceHub 的显式启用策略、与 bot.py/NoneBot 入口的职责分离，并考虑是否提供 dicepp-standalone console script 或包内 runtime 模块。

## statistics

### [B-260622-d85176] StatManager 规模化运维
- 创建: 2026-06-22
- 优先级: P2
- 类型: refactor
- 改动量: M
- 问题表现:
    - (a) tick_daily 逐行 get+upsert 替代 batch upsert_many，O(1)→O(N) commit，万级用户时 daily tick DB 写入次数显著增加
    - (b) StatManager._user_locks / _group_locks 字典无上限无清理，每个新 key 创建一个 asyncio.Lock 永不删除，百万级历史 ID 时内存持续增长
- 开发备忘:
    - 正确性优先于性能，R2 per-row 异常保护已减轻逐行失败影响
    - 优化方向：(1) StatManager 增加批量更新方法（update_user_stat_batch / update_group_stat_batch），单次事务内顺序获取 per-key 锁但合并 commit，需注意多锁获取顺序避免死锁
    - (2) 锁池增加 LRU 清理或 weakref 防护，需记录最后使用时间戳
    - 触发条件：实测 daily tick 耗时超阈值，或锁池 dict 大小超过 100K keys

