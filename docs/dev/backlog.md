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

## config

### [B-260731-90cfb8] 取消通用配置热重载，改为保存后明确重启
- 创建: 2026-07-31
- 优先级: P1
- 类型: refactor
- 改动量: XL
- 问题表现:
  - `Bot.reload_config` 在日志、Persona、本地化和 HealthMonitor 全部更新成功前就替换 `self.config`；后续失败可能留下部分新配置、部分旧状态
  - `.reload` 失败回复声称“已保留旧配置”，但在配置对象已经替换后该承诺不一定成立
  - Bot 直接修改 HealthMonitor 私有字段，绕过其自身配置校验和状态管理
  - Dashboard 保存配置后自动请求 Manager 通知 Bot reload；Manager 只能报告成功或失败，无法识别部分生效
- 开发备忘:
  - 取消通用配置热重载；完整配置只在 Bot Runtime 启动时加载并应用
  - Dashboard 保存配置时继续执行模型校验和原子写入，但不再自动请求 Bot reload
  - 保存成功后返回并展示“需要重启”，提供调用 Manager 重启整个 RuntimeUnit 的明确操作
  - Dashboard 必须提示一个 RuntimeUnit 可能承载多个 QQ 账号，重启会使这些账号一起短暂离线
  - 删除 `.reload` 命令，或在兼容阶段明确回复“通用热重载已停用，请在 Dashboard 重启 Bot”，不得继续执行部分更新
  - Manager control reload 路径暂时保留兼容响应，但退出 Dashboard 正常流程；后续协议升级时再决定删除
  - 删除生产路径中对 `Bot.reload_config` 的依赖以及对 HealthMonitor 私有字段的直接修改
  - Persona 角色卡等内容第一版允许保存后要求重启；以后有明确需求时，为具体 Module 单独实现专用重载
  - 不建立通用“部分字段可热重载”名单；专用重载需求另行新增 backlog
  - 验证配置保存不会改变运行中 Bot、重启后完整生效、Dashboard 待重启状态准确、`.reload` 不再造成部分更新，并覆盖多账号 RuntimeUnit 提示

## dashboard

### [B-260731-cf6a1d] 设计 Dashboard 查询库与群私设管理
- 创建: 2026-07-31
- 优先级: P2
- 类型: feature
- 改动量: XL
- 问题表现:
    - Dashboard 当前只能只读浏览查询数据库，使用 SQLite `mode=ro`，不能创建、编辑、删除或导入资料
    - 查询库管理与群私设管理仍依赖 Bot 命令、手工文件或本机工具
    - 当前架构文档只允许 Dashboard 对 Persona 角色卡进行类型受限写入，尚未定义查询库写入、安全事务和 Bot 在线刷新规则
- 开发备忘:
    - 将普通查询库管理和群私设管理作为同一个 Dashboard 产品功能重新设计
    - 设计查询条目、重定向、数据库创建和 XLSX 导入的页面流程，批量导入需预览和确认
    - 设计群私设库与群号的关联、启用/停用、按来源导入和清理流程
    - 只提供类型受限的查询库操作，不提供任意 SQL、任意表写入或通用 `content/` 写入
    - 所有写操作需要登录鉴权、输入校验、事务保护和审计记录
    - 明确 Dashboard 提交后 Bot 如何看到变更，以及新建、替换、删除数据库时的连接刷新规则
    - 明确完整归档与 Dashboard 并发写入时的一致性策略
    - Dashboard 功能达到替代条件后，再决定是否禁用 `HomebrewCommand`
    - 按最终写入归属更新 Manager 架构文档

## deployment

### [B-260802-6fdfcc] 建立发布前最终制品候选与不可变晋升门禁
- 创建: 2026-08-02
- 优先级: P0
- 类型: refactor
- 改动量: XL
- 问题表现:
    - 普通 CI 当前主要验证 PyInstaller assembled payload；最终 `vpk pack`、Portable stable stub、Setup 安装和安装后启动主要在 tag 触发的 Release workflow 才首次执行。
    - rc17 至 rc19 多次出现普通 CI 成功但 Release workflow 失败；同一 tag 在失败后移动到新 SHA 重跑，tag 同时承担“候选构建按钮”和“公开发布身份”，无法保持不可变发布语义。
    - `.github/workflows/test-suite.yml` 与 `.github/workflows/release.yml` 各自包含 Windows executable 启动、标准流重定向和制品检查逻辑，已经发生 Windows 建链权限、进程树清理及 onefile payload 等待窗口导致的门禁失败。
    - 影响后果是打包与启动缺陷直到推 tag 后才暴露，发版需要撤回或移动 tag，失败定位还要区分产品缺陷与测试 harness 缺陷。
- 开发备忘:
    - 建立按 commit SHA 或 workflow_dispatch 运行的 Windows 最终候选流水线，在创建 tag 前生成 Portable、Setup、Velopack bundle 和 release manifest。
    - 对候选资产执行 stable stub 无控制台启动、Velopack hooks、silent Setup 安装、安装后启动、资产布局、版本、digest 和 provenance 校验；全部通过后才允许创建 tag。
    - Release workflow 只按 digest 晋升并发布已经验证的同一字节资产，不重新打包；禁止 force-move 已用于候选或发布的 tag。
    - 将两份 workflow 中重复的 Windows 构建、启动、进程清理和诊断逻辑提取为仓库内可本地与 CI 共用的版本化 PowerShell/Python runner。
    - 将候选资格检查设为 master/tag required check，并保留失败时的进程树、端口、启动耗时、日志和安装目录清单。
    - 先验证 GitHub artifact retention、跨 workflow provenance、GHCR/Release 权限和候选缓存成本；不得重写已公开 release assets。

## dice_hub

### [B-260731-93a733] 重新设计并实现 DiceHub 命令
- 创建: 2026-07-31
- 优先级: P2
- 类型: feature
- 改动量: XL
- 问题表现:
    - 旧 DiceHub 命令被禁用后，用户将暂时无法通过机器人完成注册、查看节点、心跳或连接配置
    - 旧实现把命令解析、远端调用、配置持久化和回复生成混在一起，且现有测试没有覆盖完整命令调用链
    - 当前尚未确定重新实现时需要保留的 DiceHub 使用场景和远端契约
- 开发备忘:
    - 实现前重新确认 DiceHub 的实际用途、远端协议、认证方式、隐私要求和失败语义，不默认兼容旧命令行为
    - 使用现有异步调用链完成远端操作，不恢复 `run_async`
    - 集中命令执行、错误转换和用户回复，避免命令 Adapter 了解远端调用细节
    - 明确旧 DiceHub 配置和数据的保留、迁移或废弃策略
    - 使用本地 Fake Adapter 覆盖完整命令行为；真实外部 DiceHub 验收需要另行确认

## manager

### [B-260802-eb74ca] 修正功能回退成功被控制心跳误判为 rollback_failed
- 创建: 2026-08-02
- 优先级: P0
- 类型: bug
- 改动量: M
- 问题表现:
    - Linux fresh rc17→公开 rc19 故障注入验收中，程序镜像、配置、数据、schema 和 Runtime 均已恢复，事务 marker/request 也已清理，但 operation journal 仍记录 `rollback_status=failed`。
    - 故障注入在 confirm 前 24 秒关闭控制通道；回退捕获 baseline 时末次心跳约 70 秒旧，仍小于 `heartbeat_timeout=120s`。`ControlChannelService.probe()` 只按历史心跳年龄返回 ok，没有表达 authenticated websocket 已断开，导致 rollback control gate 被错误设为 enforced。
    - post-rollback control heartbeat 不可能前进后，`upgrade.py` 将整个回退持久化为 `rollback_failed`；后续恢复可能据此报告 `manual_recovery_required`、保留事务资产并误导运维，尽管程序与数据实际已经安全恢复。
    - 现有回归只覆盖断开后 heartbeat 已过期约 3600 秒的场景，没有覆盖 fresh-but-disconnected 的 120 秒竞态，也没有区分 restoration success 与 post-rollback control health。
- 开发备忘:
    - 为 ControlChannel 维护线程可读的不可变 active-session snapshot，至少记录当前 authenticated session 数与当前 session 的最新 heartbeat；connect、replace、disconnect、heartbeat 均必须更新，多 Bot 下不得由已断开 Bot 的历史心跳遮蔽真实状态。
    - `_capture_control_baseline()` 仅在当前确有 authenticated session 且其心跳新鲜时选择 enforced，不得只依据历史 heartbeat age；不要通过单纯扩大超时或按 rollback 阶段无条件豁免规避问题。
    - 分离 restoration 与 post-rollback control health：程序、数据、schema 和 Runtime 本地恢复成功时记录 `rollback_status=succeeded`、`rolled_back=true`；控制通道未恢复单独记录 degraded/failed warning。只有程序、数据、schema 或 Runtime 本地恢复失败才进入 `rollback_failed/manual_recovery_required`。
    - 保持首次目标升级后的 control gate fail-closed；切换前存在 active session 而目标未重连时，升级仍必须失败并触发回退。
    - 补充 fresh heartbeat 后断开、多 Bot session 切换、回退后重连并产生新心跳、永久断开但本地恢复成功、本地恢复真实失败等回归测试。
    - 关联 `B-260802-3e3e23` 的跨版本矩阵与 `B-260731-b6f811` 的长期事务重构，但本 bug 应独立优先修复并在 v3.0 正式版前完成 Linux 定向复验。

### [B-260802-3e3e23] 建立持久化升级协议契约与跨版本升级回退矩阵
- 创建: 2026-08-02
- 优先级: P0
- 类型: refactor
- 改动量: XL
- 问题表现:
    - rc17 `WindowsVelopackUpgradeAdapter.stage()` 将 rollback nupkg 写入 `<transaction>/rollback-payload/`，同版本 resume validator 却只接受事务根目录的直接子项，真实 rc17→rc18 自动升级因此报 `ValueError: UpdateGuard rollback package escapes transaction`。
    - producer/stage 与 consumer/resume 由不同测试组分别验证；consumer fixture 手工构造事务根布局，没有消费真实 stage/switch 输出，所以互相矛盾的生产实现仍能全绿。
    - UpdateGuard request `format_version=2` 只约束 JSON 字段、绝对路径和 digest 格式，没有表达 rollback 目录拓扑等跨进程、跨重启、跨版本语义。
    - Windows 真实升级与故障回退目前依赖 `.temp` 下的一次性验收 harness；已验证的 pre-bump 候选与公开 rc19 二进制、同一实例回退后再次升级仍需明确区分，尚未成为固定 Release 门禁。
    - 影响后果是 `automatic_upgrade: yes` 的版本可能在公开发布后才发现旧 Manager/Guard 无法消费新请求，严重时需要人工恢复。
- 开发备忘:
    - 建立真实 `adapter.stage() → switch() → scan/load/resume` producer-consumer 契约测试；正常请求必须由生产 producer 生成，只有畸形和攻击样本允许手工 mutation。
    - 在 `tests/fixtures/update_guard/` 保存 `v2-direct`、`rc17-staged` 等不可变 golden protocol fixtures，使用路径占位符并记录期望事务树；当前 consumer 必须读取全部仍受支持的历史变体。
    - 盘点 request、guard/started/health/rollback marker、journal、release manifest、bundle manifest 和 deployment schema，逐项记录 producer、consumer、format version、支持周期及门禁测试；目录语义变化必须新增协议版本或显式布局字段。
    - 建立上一受支持版本→当前候选的 Windows/Linux E2E：正常健康提交、目标启动后健康失败自动回退、同一实例回退后再次升级、Velopack apply 失败且目标代码从未执行。
    - 普通 PR 跑低成本 producer-consumer 与 golden tests；涉及 Manager/UpdateGuard/Velopack/发布协议的 PR、nightly 和 Release 跑真实旧二进制矩阵，并固定下载资产 SHA-256。
    - `automatic_upgrade: yes` 必须绑定当前 SHA 的跨版本验证证据；证据缺失或受支持旧制品不可获得时只能发布为 `automatic_upgrade: no`。
    - 本条应先于 `B-260731-b6f811` Manager 维护事务 Module 的大规模重构完成，以先建立旧 journal、回退状态和平台行为兼容护栏。

### [B-260731-b6f811] 深化 Manager 维护事务 Module
- 创建: 2026-07-31
- 优先级: P1
- 类型: refactor
- 改动量: XL
- 问题表现:
  - `UpgradeCoordinator` 当前有 19 处对 `ArchiveCoordinator` 私有 Implementation 的直接调用，包括 `_quiesce`、`_restart`、`_hard_health`、`_migrate_and_validate_schema`、`_best_effort_restart`、`_capture_control_baseline`、`_cleanup_inprogress` 和 `_apply_retention_if_safe`
  - 升级与归档恢复分别实现维护锁上下文、事务 journal、重启恢复和 `rollback_failed` 终态判定；共同的数据安全政策没有集中归属
  - `docs/dev/manager-architecture.md` 已明确 terminal rollback adjudication rule 由升级与归档恢复共用，但两条代码路径仍分别解释 journal 状态；后续新增或调整状态时存在只更新一侧的风险
  - 当前尚未确认已有生产数据损坏；本条处理的是高风险事务规则的 Locality、可验证性和长期分歧风险
- 开发备忘:
  - 目标:
    - 建立升级与归档恢复共用的 Manager 维护事务 Module
    - 消除 `UpgradeCoordinator` 对 `ArchiveCoordinator` 私有 Implementation 的调用
    - 集中维护锁、RuntimeUnit 停写与恢复、迁移、健康检查、共同 journal 生命周期和回退终态政策
  - 必须保持的不变量:
    - 现有归档格式和 manifest 格式不变
    - 现有 Manager HTTP Interface 和 operation 响应不变
    - 已持久化的旧 journal 仍可读取和恢复
    - 破坏性回退已失败的终态 journal 不会在 Manager 重启后重复执行
    - UpdateGuard 已提供可靠回退证据的例外路径保持可恢复
    - 成功、失败和 Manager 重启后均恢复原先运行的 RuntimeUnit 集合
    - system 安全归档的保护与保留策略不变
    - Linux Docker 与 Windows Velopack/UpdateGuard 的平台行为不变
  - 不在本条范围:
    - 不修改归档 payload、Catalog 或 schema migration 契约
    - 不改变自动升级支持范围或部署拓扑
    - 不重写 Docker、Velopack 或 UpdateGuard Adapter
    - 不新增用户可见能力
  - 建议提交顺序:
    1. 补齐共同事务状态、故障阶段和旧 journal 的行为护栏
    2. 提取 RuntimeUnit 停写、重启、迁移和健康检查等共同行为，替换 Upgrade → Archive 私有调用
    3. 集中可重试回退与 terminal rollback 判定
    4. 集中共同 journal 字段、阶段和 commit point 解释
    5. 删除失去调用者的私有方法和重复规则，更新架构文档
  - 提交要求:
    - 在同一个 PR 中完成
    - 每个提交均应可独立运行并通过其相关测试
    - 不允许依赖后续提交才能恢复可运行状态
    - 不得先完成整体重构，再事后机械拆分不可运行的提交
  - 重点验证:
    - 正常归档创建与恢复
    - 正常 Linux/Windows 升级
    - 停写、程序切换、数据切换、迁移、重启和健康检查各阶段失败
    - 回退成功、回退失败及 terminal rollback
    - Manager 在各 commit point 重启后的恢复
    - Windows UpdateGuard handoff 与可靠 marker 例外
    - 改动前生成的 journal 兼容读取
    - Manager 相关测试与完整离线回归

## persona

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

## query

### [B-260731-731553] 将 Bot 资料查询命令收缩为只读
- 创建: 2026-07-31
- 优先级: P1
- 类型: refactor
- 改动量: L
- 问题表现:
    - 玩家仍需要 `.查询`、`.搜索`、结果选择和翻页，但 `QueryCommand` 同时承担骰主创建、编辑、删除、重定向写入和数据库管理
    - 编辑状态跨 `can_process_msg` 与 `process_msg` 传递，依赖 `pending_db_action` 等隐藏状态
    - 英文 `create` 分支判断英文命令后按中文“创建”切分，已存在明确错误
    - 当前产品方向不再需要骰主通过群指令管理资料库
- 开发备忘:
    - 保留玩家查询、全文搜索、结果选择、翻页和只读重定向解析
    - 删除条目创建、编辑、删除及 `DELETE` 密文流程
    - 删除重定向创建和删除，只保留查询时的重定向读取
    - 删除数据库创建、加载、卸载、导入和列表等管理指令
    - 清理 `editing`、`edit_index`、`edit_new`、`edit_flag`、`pending_db_action` 等写入状态
    - `HomebrewCommand` 和 `.私设` / `.hb` 本条不改
    - 不在本条增加 Dashboard 写入能力；接受过渡期内没有内建单条资料 CRUD
    - 更新帮助文本，并验证现有玩家查询行为不变、旧管理命令不再执行写入

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

