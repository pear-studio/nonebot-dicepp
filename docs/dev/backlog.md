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

## dice_hub

### [B-260731-4c8f69] 暂时禁用过时的 DiceHub 命令
- 创建: 2026-07-31
- 优先级: P1
- 类型: bug
- 改动量: S
- 问题表现:
    - `.hub` 命令已经过时，当前不应继续向用户开放
    - `.hub online` 调用了不存在的 `HubManager.heartbeat()`
    - 命令在已有异步调用链中使用 `run_async` 创建额外线程和事件循环，并同步等待结果
- 开发备忘:
    - 停止导入和注册 `HubCommand`，使 `.hub` 不再被命令分发处理
    - 暂时保留仍可能被其他调用者使用的 `HubManager`、配置数据和远端访问代码，不删除已有用户数据
    - 补充命令注册与实际消息行为测试，确认 `.hub` 已禁用且不影响其他命令
    - 不调用真实 DiceHub 或其他外部网络

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

