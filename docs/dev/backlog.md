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

## data

### [B-260625-cdbbd9] DND5eDatabase — 基于 5etools-cn 结构化 JSON 重建 D&D 5e 查询数据库
- 创建: 2026-06-25
- 优先级: P1
- 类型: feature
- 改动量: XL
- 问题表现: 当前 content/queries/DND5E*.db 为纯文本 6 字段模型：data(名称,英文,来源,分类,标签,内容)，所有结构化信息混在内容字段中，无法按环位/CR/伤害类型等字段精确筛选。5etools-cn 提供 502 个 JSON 文件、13000+ 条目的深度结构化数据（法术 936 条、怪物 4528 条、物品 2428 条），翻译率法术 99%/专长 100%/技能 100%，数据覆盖远超现有 DB。现有 REGEXP 搜索逐行调 Python re 性能差，随机生成依赖 xlsx 手工维护。
- 开发备忘: 设计详见 docs/dev/dnd5e-database-design.md。分 5 Phase：(1) 法术表 + 通用 entries 渲染器 + 搜索索引；(2) 怪物表 + Python port of _copy 解析器；(3) 物品/专长表 + 通用 dnd5e_entries 表 + 配置驱动渲染器；(4) 其余 30+ 类别 + 随机生成接入；(5) edition 版本列 + v2024 查询参数。单文件 DND5E.db，LIKE 替代 REGEXP，查询时解析 _copy 继承链，实时 entries 渲染。

### [B-260618-56a0a3] 3.0.0 Data Foundation：数据状态架构与迁移基础
- 创建: 2026-06-18
- 优先级: P1
- 类型: refactor
- 改动量: XL
- 问题表现:
    - 3.0.0 将面向更多自托管用户，当前 config/data/dashboard/content 的状态边界尚未固化。
    - 现有 `BotDatabase` 同时管理 bot_data/log 两个 DB，迁移 registry 仍是单一线性链，新 DB 也会经过历史迁移路径。
    - Persona 仍使用 `ensure_tables()` + SQL 列表 + `ALTER TABLE` try/except，未纳入统一 schema lifecycle。
    - 本地控制 token 仍以 `data/runtime/local-control.token` 文件形式存在，跨 Bot/Dashboard 共享状态缺少实例级数据库承载。
    - 配置 JSON 过时字段、新增字段和字段级错误缺少统一 canonical rewrite 策略。
- 开发备忘:
    - 设计详见 `docs/dev/data-architecture-3.0.md` 的 Data Foundation 部分。
    - 新增 `data/dicepp.db` 与 `DicePPDatabase`，local control token 入库；旧 token 文件只作为一次性迁移输入，导入成功后删除。
    - 引入 SchemaTarget：fresh DB 直接 create latest schema，existing DB forward-only migration；每个 DB 自持 `schema_metadata` / `schema_migrations`。
    - targets 覆盖 `instance`、`bot_core`、`bot_log`、`persona`，由数据 owner 维护 target 定义。
    - 配置 JSON 引入 canonical rewrite：普通未知/过时/错误字段可丢弃或默认，关键字段必须迁移、告警或禁用相关功能。
    - 测试治理：schema equivalence、migration import whitelist、legacy fixture 生命周期注释、retry-safe migration。

## deploy

### [B-260615-19b0fa] 3.0.0 Dashboard Save Archives：存档与恢复
- 创建: 2026-06-15
- 优先级: P1
- 类型: feature
- 改动量: XL
- 问题表现:
    - 3.0.0 自托管用户需要像游戏存档一样，在 Dashboard 中创建和恢复 DicePP 状态存档。
    - 镜像/程序版本回退无法恢复已经变更的 config、data、SQLite DB 或运行时状态，容易让“可回退”产生误导。
    - 升级前存档、恢复前存档、manifest/checksum、恢复失败处理和 release 风险门禁尚未落地。
    - Dashboard 自身 DB、content、LLOneBot 数据等边界尚未在存档 UI 中明确告知。
- 开发备忘:
    - 设计详见 `docs/dev/data-architecture-3.0.md` 的存档与恢复、阶段 D 部分。
    - Dashboard 提供独立存档能力：创建、查看、删除、恢复；升级流程仅复用该能力。
    - 存档为 `data/backups/*.zip`，包含 manifest 和 sha256 checksum。
    - 包含 `config/`、`data/dicepp.db`、`data/bots/**/{bot_data.db,log.db,personas_data_*.db}`、`data/local_images/`。
    - 不包含 `dashboard/data/dashboard.db`、`content/`、`data/backups/`、`data/runtime/`、`data/bots/*/logs/`、LLOneBot 数据。
    - 创建存档时允许短暂停写；恢复由 Manager 编排，恢复前自动创建 pre-restore 存档，失败时保留 pre-restore 并提供恢复入口。
    - release metadata 标记数据/配置风险时，升级前必须强提示或门禁创建存档。

### [B-260626-b6bb08] DicePP 分布式 QQ 协议端 APK
- 创建: 2026-06-26
- 优先级: P2
- 类型: feature
- 改动量: XL
- 问题表现: 集中部署方式下 QQ 协议端存在单点风险，需将协议端分布到用户设备上
- 开发备忘: 开发 Android APK，内嵌 OneBot 协议端，用户安装后扫码登录即可接入 DicePP 服务器。Kotlin 壳 + Go 二进制，前台服务保活。预估 7-9 天。

## deployment

### [B-260618-8fce87] 3.0.0 Manager Foundation：本地 Manager 与 Runtime Backend
- 创建: 2026-06-18
- 优先级: P1
- 类型: feature
- 改动量: XL
- 问题表现:
  - 用户完成首次部署后，仍缺少通过 Dashboard 完成 Bot 启停、重启、更新和回滚的能力。
  - Dashboard 未来还需要更新自身；由 Dashboard 直接替换自身容器或持有 Bot 子进程，难以保证操作完成、失败恢复和职责边界。
  - 直接向 Dashboard 挂载 Docker Socket 会把宿主机高权限暴露给复杂 Web 应用。
  - Linux 以 Docker 容器运行，Windows 以打包进程运行；如果分别实现生命周期逻辑，容易形成两套状态机、错误语义和更新流程。
  - 3.0.0 Dashboard 存档恢复需要可靠停止 Bot、替换 config/data、重启 Bot，不能由 Dashboard 直接覆盖运行中的 DB 文件。
- 开发备忘:
  - 设计详见 `docs/dev/data-architecture-3.0.md` 的 Manager 3.0 范围、阶段 B 部分。
  - 引入本地 Manager 常驻层，作为 Dashboard 与运行时之间的最小权限管理层；Dashboard 不直接访问 Docker Socket，不直接持有 Bot 生命周期，不直接替换运行中的文件。
  - Manager Core 统一实现操作状态机、鉴权、审计、健康检查、并发控制、版本兼容、失败恢复和对 Dashboard 的 API。
  - 平台差异收敛到 Runtime Backend：Linux 使用 DockerRuntime，Windows 使用 ProcessRuntime；两端共享 contract tests。
  - 3.0.0 纳入本地 Manager 完整能力；DiceHub 远程 WSS 控制、云端更新/回滚/诊断、device-code 授权闭环保留 TODO。
  - Manager 需为阶段 C 更新/回滚和阶段 D 存档恢复提供停写、停启、状态、日志等基础能力。

### [B-260624-a91b7e] 3.0.0 Local Update And Rollback：本地更新、版本切换与失败恢复
- 创建: 2026-06-24
- 优先级: P1
- 类型: feature
- 改动量: XL
- 问题表现:
  - 3.0.0 前需要 Dashboard 通过 Manager 完成本地启停、重启、更新、版本切换和失败恢复，否则存档恢复和升级门禁无法闭环。
  - Windows 打包进程与 Linux Docker 部署形态不同，如果更新/回滚逻辑分散实现，会形成两套状态机和错误语义。
  - 镜像 tag 或程序文件切换必须和数据迁移、存档、健康检查协作，否则失败后用户无法判断当前处于哪个版本/状态。
- 开发备忘:
  - 设计详见 `docs/dev/data-architecture-3.0.md` 的阶段 C 部分。
  - Dashboard 只调用 Manager API；Manager 负责执行启停、重启、日志、状态、版本切换和失败恢复。
  - Windows 采用 staging 新版本接管：校验下载、停止旧进程、替换程序、健康检查、失败恢复；采用 onedir 发行包，不强求单一物理文件。
  - Linux 通过受限 DockerRuntime 管理 DicePP 相关容器/compose 服务，禁止 Dashboard 直接暴露 Docker Socket。
  - 更新前复用阶段 D 存档能力；恢复/回滚后由目标版本按 forward-only migration contract 处理数据。
  - 不纳入 DiceHub 远程控制闭环；只要求本地 Dashboard + Manager 路径可用。

## persona

### [B-260630-44f47a] 生活事件时间加速测试功能
- 创建: 2026-06-30
- 优先级: P1
- 类型: feature
- 改动量: M
- 问题表现: 生活事件（life events）的评测和调试只能依赖真实时间流逝; 无法快速模拟一周或一个月的 tick 推进效果; 导致 life event 相关功能的调试、回归测试和效果验证周期极长
- 开发备忘: 引入时间加速/模拟时钟机制，允许在测试/调试模式下按需推进模拟时间（如 +7d、+30d）; 可能方向：(a) Simulator/tick 循环增加可配置时间倍速因子；(b) 提供 debug 命令或 API 手动触发指定天数的 tick 批量执行；(c) 引入 clock abstraction，测试时注入 fake clock; 影响面: life/simulator.py、event/time 相关模块、可能的 debug 命令入口

### [B-260630-e9fcc8] Conversation 消息线程 DB 持久化
- 创建: 2026-06-30
- 优先级: P1
- 类型: feature
- 改动量: M
- 问题表现:
    - Conversation 仅内存中存活，bot 重启或进程退出后天内上下文全部丢失
    - DM/Character 失去对今日已发生对话的全部记忆，无法恢复叙事连续性
- 开发备忘:
    - 在 persona_session_message 基础上增加 Conversation 序列化/反序列化
    - 可考虑 JSON/msgpack 序列化 _messages 列表
    - 跨天 compact 时自动清理旧消息
    - 影响面: life/conversation.py、data/store.py、data/models.py

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

### [B-260630-26d6a7] 通知消息 name/content 前缀一致性扫描与 role 验证
- 创建: 2026-06-30
- 优先级: P2
- 类型: refactor
- 改动量: S
- 问题表现: 当前 notification/transient 消息混用 name 字段和 content 前缀来区分通知与真用户输入，缺乏一致性
- 开发备忘: 1. 扫描全项目所有类似注入点，确认 name 和 content 前缀是否都有使用；2. 判断是否应将 role 从 user 统一改为 system；3. 跑真实 LLM 验证 system role 通知消息的行为差异。结论以 LLM 验证结果为准。

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

## release

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

