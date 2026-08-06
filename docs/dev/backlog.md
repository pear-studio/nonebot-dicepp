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
    - 当前状态（2026-08-03）：本地 `master` 已实现并拆分提交 Windows 有界进程 runner、仓库污染守卫、Final Candidate/Receipt v2、Promotion 原字节晋升、Gitee 停用及升级证据 fail-closed 框架；Manager 维护边界、配置保存后重启和 Query 只读化也已完成。全量测试为 `4191 passed, 67 skipped`，但本轮提交尚未 push，新流程也尚未在 GitHub/GHCR 上运行。后续 agent 不应重新设计或重复实现本地代码，除非真实验收暴露缺陷。
    - 当前设计：Final Candidate 绑定当前 `master` 的精确 SHA，封存 Windows、Linux、release manifest、条件性 upgrade evidence、容器身份和 `dicepp-candidate.json`；Promotion 必须显式选择 run ID 与 artifact ID，复核同一字节和镜像 digest 后按 draft-first 流程发布，不重新构建、压缩或打包。
    - 当前有意不做：Gitee 同步、额外发布 token、release environment/第二位 reviewer、每次 Promotion 读取管理员配置、自动 GHCR candidate 清理。不得在没有新决策的情况下重新加入这些复杂度。
    - 验收时机：`B-260731-b6f811` 及本轮其他代码修改已完成；先补完 `B-260802-3e3e23` 剩余的真实 journal 与双平台 harness，再冻结最终 `master` SHA。任何后续代码提交都会使已生成 Candidate 失效，必须从冻结和验收重新开始。
    - 冻结后一次性确认远端最小配置：启用 Immutable Releases、建立受保护的 `refs/tags/v*` ruleset、授予仓库 Actions 对两个 GHCR package 的 Write access；发布运行时只使用 `GITHUB_TOKEN`。
    - 验收顺序：push 冻结 SHA 并等待普通 CI → 先完成 `B-260802-eb74ca` 的 Linux 故障回退验收及 `B-260802-3e3e23` 的跨版本矩阵/evidence → 对同一 SHA 触发 Final Candidate → 核对 30 天 artifact、Receipt、所有资产摘要和 GHCR candidate digest → 显式触发 Promotion。
    - Promotion 会真实创建 tag、GitHub Release、正式 GHCR version tag 并最后更新 `latest`，不得作为无副作用的试运行；应使用计划公开的 RC 或正式版本。
    - 关闭条件：记录成功的 Candidate run ID、run attempt、artifact ID/digest 和 Promotion run；确认公开 tag、Release metadata/assets、版本镜像及 `latest` 均与 Receipt 一致，且冲突状态 fail closed、精确同身份中断状态可幂等恢复。全部满足后才删除本条。

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
    - 当前状态（2026-08-03）：active-session snapshot、断开后撤销健康、回退本地恢复与 post-rollback control health 分离、Manager 凭据就绪后重连及对应回归测试均已实现；本条代码主体完成，当前只保留真实跨版本故障注入验收。后续 agent 不应重新实现已有修复，除非验收失败。
    - 验收时机：`B-260731-b6f811` 代码已完成；补完 `B-260802-3e3e23` 的真实 journal 与双平台 harness 后，在最终冻结的 `master` SHA 上独立执行；它是 `B-260802-6fdfcc` Final Candidate/Promotion 之前的第一个系统验收。
    - Linux 定向场景：从受支持旧版本 fresh 部署升级到冻结 SHA，在 confirm 前保持 fresh heartbeat 后主动断开控制通道并触发目标失败；确认程序镜像、配置、数据、schema、Runtime、marker/request 均恢复，operation journal 记录 `rollback_status=succeeded` 与 `rolled_back=true`，控制通道未恢复只产生 degraded warning。
    - 同次验收还要确认切换前存在 active session 而目标未重连时仍会 fail closed，并覆盖多 Bot session 切换、回退后重连产生新心跳、永久断开但本地恢复成功、本地恢复真实失败。
    - 若验收触发任何代码修复，必须重新冻结 SHA，并重跑本条、跨版本 evidence 和后续 Final Candidate/Promotion；不得沿用旧 Candidate。
    - 关闭条件：保存 Linux fresh 跨版本故障注入的版本、冻结 SHA、日志/journal 和恢复结果证据；上述恢复状态与控制健康语义全部符合预期后删除本条。

### [B-260802-3e3e23] 建立简化 Windows 恢复契约与跨版本升级矩阵
- 创建: 2026-08-02
- 优先级: P0
- 类型: refactor
- 改动量: XL
- 问题表现:
    - Windows 现有 UpdateGuard 将 Velopack、独立进程、身份监督、多组 marker 和程序/数据自动回退绑在一起；跨进程、跨重启和跨版本契约已多次在真实 RC 升级中暴露布局与兼容问题。
    - 对“新版本完全无法启动”这个低频故障长期维护第二套无人值守协调器，复杂度和发布风险已高于所得收益。
    - Windows 需收缩为可审计的简单保险：升级前备份整个 `current/` 并保留 pre-upgrade 数据归档；新版失败时不自动回退，由用户运行实例根目录 `DicePP-Recover.cmd` 换回旧程序，再由旧 Manager 恢复数据和 RuntimeUnit 原状态。
    - 升级矩阵仍需消费真实候选产物而不是手工构造 fixture，避免 `automatic_upgrade: yes` 的版本在公开后才发现源 Manager 无法完成新协议。
- 开发备忘:
    - Windows 删除 `DicePP-UpdateGuard.exe`、`%LOCALAPPDATA%\DicePP-UpdateGuard`、Guard request/started/health/rollback marker、进程身份监督和无人值守程序降级；不以其他名称重建后台 watchdog。Linux 的 immutable Image ID、Compose 切换与自动回退协议保持不变。
    - 调用 Velopack 前必须将完整 `current/` 备份到稳定实例根下的单一恢复事务，并写入仅绑定现有 upgrade journal 的最小 `recover.json` 和根目录一次性恢复入口；任一准备步骤失败都在程序切换前停止。
    - 恢复脚本不终止进程；`current/` 被占用、目录换位或数据恢复失败时原样保留所有材料并停止。恢复只能整目录移动，不逐文件合并。
    - 新 Manager 完成 migration、本地硬健康检查并提交升级后立即最佳努力删除程序备份、恢复描述与根恢复入口；清理失败只告警，不把已成功的新版重新判失败。
    - rc20 作为 `automatic_upgrade: no` 的手工迁移起点：不扫描、迁移、清理或恢复 rc19 及更旧 Guard 状态，旧遗留物不得阻止 rc20 启动。rc20 Candidate 验证无 Guard 的最终包结构、首装/手工迁移与 Linux 现有矩阵；不得用 rc19→rc20 宣称新 Windows 协议已通过。
    - 第一次真实 Windows 新协议验收是 rc20→rc21：同一最终候选上验证健康提交后恢复材料清理，以及目标 `current/` 缺失/损坏时根 `DicePP-Recover.cmd` 仍能换回旧程序、恢复 pre-upgrade 数据和 RuntimeUnit 原状态。矩阵必须消费冻结 Candidate 字节；`automatic_upgrade: no` 的 validation-only 证据不进入 Receipt 或 Release assets。
    - 未来正式版只维护“上一正式版 → 当前正式版”。关闭条件：rc20 的简化基线验收通过，rc20→rc21 Windows 真实升级/人工恢复与当次 Linux 矩阵全部绑定冻结 SHA 通过外部验收。完成前本条保持 open。

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

