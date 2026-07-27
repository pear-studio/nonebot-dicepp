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

## manager

### [B-260727-33bf96] Windows UpdateGuard 交接无法形成可恢复终态，真实自动升级必败
- 创建: 2026-07-27
- 优先级: P0
- 类型: bug
- 改动量: XL
- 问题表现:
    - 实证（2026-07-27 rc14 Windows 验收，报告 .temp/dicepp-v3.0.0rc14-windows-upgrade-acceptance-evidence.md）：发现/下载/校验/preview/回退包准备全部通过后，首次真实 confirm 失败
    - 三段式故障链：(1) Manager 创建事务进入 awaiting_update_guard 并记录 Guard PID 后正常退出，但该 Guard 未写出 guard.json/started.json 即消失，实例离线；(2) 人工启动稳定根 DicePP-UpdateGuard.exe，Guard 写出 guard.json(running)、Velopack 实际将 current 切到 RC14，随后 Guard 再次消失，无 started/health/rollback marker；(3) RC14 Manager 启动能识别中断事务（interrupted）并进入维护恢复，但 Dashboard/Manager 停止监听后 launcher/current 两进程不退出，交接不闭合
    - 终态：程序已是 RC14 但 journal interrupted/awaiting_update_guard，guard marker running 绑定死 PID，packages/ 空，升级未提交
    - 可排除项：非 UAC（无弹窗无拦截事件）；非心跳门（未走到健康确认）；GitHub 限流与防火墙均已合规绕过并留证
    - 影响：Windows 自动升级在真实环境从未完成过一次；rc12 卡 preview 409（已修），本问题是其后下一层。阻塞 v3.0.0
- 开发备忘:
    - 初步嫌疑（待代码验证）：首次 Guard 连 guard.json 都未写出，疑似 Guard 作为 Manager/launcher 子进程在 launcher 退出时被进程组/Job 对象连带终止（脱离标志不对）；人工启动活到 apply 后再消失，指向 Velopack apply(Update.exe apply --norestart) 边界的进程生命周期问题；维护恢复不退出是第三个独立问题
    - 验收方 4 条建议即修复验收标准：Guard stdout/stderr 与退出码持久化到事务目录（不得 DEVNULL）；apply 期间 Guard 进程存活观测；维护恢复时 tray/launcher 必须退出（Runtime stop 409 也要完成退出）；补真实 Windows 回归（正常切换、目标 Manager 强杀后自动回退、apply 边界中断幂等恢复）
    - 推进方式（用户已定方案 B）：授权 Windows agent 配 repo+工具链本地迭代，dev 侧出诊断指引（交接时序、仪表化点位、实验矩阵）；修复需覆盖 Guard 启动脱离方式、apply 边界存活、维护恢复退出三处
    - 关键代码：src/dicepp_manager/update_guard.py、upgrade.py WindowsVelopackUpgradeAdapter（handoff/awaiting_update_guard）、factory.py Windows 装配、UpdateGuard exe 打包入口（scripts/build）
    - 现场保留在 Windows 机 D:\Workplace\nonebot-dicepp\.temp\rc14-windows-upgrade-20260727\（事务目录、manager.db、包缓存、evidence/*.json），诊断时优先复用

### [B-260727-bd8bb8] Release 下载在干净 EOF 截断时删除部分文件且无自动重试，劣质网络下大 bundle 几乎无法下载
- 创建: 2026-07-27
- 优先级: P1
- 类型: bug
- 改动量: M
- 问题表现:
    - 实测（2026-07-27 rc14 Linux 验收）：99.6MB linux bundle 经 Manager 下载，在 41,943,040 字节处被对端干净 EOF，报 Downloaded artifact size differs from manifest；.part 与 .part.json 被删除，重新触发 POST /v1/releases/download 从 0 开始
    - 本机到 GitHub 链路约 17KB/s 且单连接约 40MB 必断，100MB 级 bundle 在该路径下几乎永远无法完成；国内用户经 Manager 自动升级下载 GitHub Release 资产时命中同一场景
    - 根因：release.py:1107-1124 把截断（size 不符）与 SHA 不符合并为 ReleaseDownloadError 并删除 .part；Range/If-Range 续传机制（release.py:1036-1067，有测试覆盖）只在连接异常中断（非 ReleaseDownloadError）时保得住部分文件；下载路径无任何自动重试
- 开发备忘:
    - 修复方案：干净 EOF 截断改判为可续传失败（保留 .part + validator），在 _download_artifact 外层加有界重试循环，复用已有 Range/If-Range 续传与 416 stale-range 处理；带退避、最大尝试次数与既有 _check_cancelled 取消纪律；SHA 校验不符仍删除重下（数据损坏语义不同）
    - 预计核心改动 40-80 行，限 src/dicepp_manager/release.py 单模块；测试在 tests/integration/manager/test_release_storage.py 用 fake transport 模拟 截断-续传-成功 / 超预算失败 / 取消
    - 需先验证：UrlTransport 对 IncompleteRead 与干净 EOF 的区分；重试预算与 416 内部重试的叠加关系
    - 风险：低-中，只触下载路径，升级事务语义不变
    - 排期（用户已定）：RC14 两平台验收收官后实施，随后发 RC15（含 manager 变更，自动升级: no），两平台手工迁移实证后再发 v3.0.0

### [B-260727-bf469b] 无绑定 bot 的实例无法通过升级/回退的控制心跳健康门
- 创建: 2026-07-27
- 优先级: P1
- 类型: bug
- 改动量: M
- 问题表现:
    - 实证（2026-07-27 rc14 Linux 验收）：无任何 OneBot 连接的实例（新装未配置 QQ、或升级窗口内 NapCat/QQ 离线）自动升级 100% 失败，报 Bot control heartbeat did not advance after restart
    - 回退卡同一道门：容器实际已恢复旧镜像，回退仍被判 rollback_failed（连续两次失败尝试均为此模式），留下终态保护，UX 迷惑
    - 机制：控制心跳由每个 QQ 账号的 DiceBot 经控制 WS 上报（dicebot.py:133-157），DiceBot 只在 OneBot 客户端连接后创建；无绑定 bot 时 Dashboard latest_heartbeat 恒 null；_hard_health 要求心跳较基线前进（archive_coordinator.py:730-739），基线为 null 时永不可能通过
    - 反证：挂上 mock OneBot 客户端（绑定 bot 10001）后同一实例升级一次通过
- 开发备忘:
    - 已定方案 B（用户 2026-07-27 拍板）：基线捕获时若 manager status bots 为空（无绑定 bot），控制探针降级为 not_applicable，runtime + Dashboard 健康即为充分证据；有绑定 bot 但心跳断流时维持失败（真故障）
    - 涉及：upgrade.py:1010-1011（基线捕获需带 bots 列表）、upgrade.py:1074-1077 与 :1554 附近（升级与回退健康门调用）、archive_coordinator.py:292/343/592/720-741（归档恢复路径同款门）
    - 测试：manager 升级/回退健康门集成测试补两类用例——无绑定 bot 时升级/回退均跳过控制探针并成功；有绑定 bot 但无新鲜心跳时维持失败
    - 验收备忘：未来验收标记字段必须用真实配置字段（如 dicehub.name），自定义键会被 bot ConfigLoader 按设计丢弃（loader.py:291-295）
    - 排期：RC15，与 B-260727-bd8bb8（下载续传）同车

### [B-260726-7d886d] 统一 _discover_latest 与 _find_release_by_version 的分页逻辑（抽 _iter_release_entries 生成器）
- 创建: 2026-07-26
- 优先级: P2
- 类型: refactor
- 改动量: M
- 问题表现:
    - release.py:767-797（_find_release_by_version，manager-upgrade-path-fixes 分支新增）与 :694-703（_discover_latest）的 10 页 × per_page=100 分页循环、list 校验、错误文案几乎逐字重复
    - 将来调整分页上限或 GitHub API 行为时极易只改一处，两处行为静默分叉
    - 来源: review-260726-2209-manager-upgrade-path-fixes R14（已共识·延后）
- 开发备忘:
    - 修复方向: 抽 _iter_release_entries() 私有生成器统一分页与 list 校验，两处各自只做过滤/匹配
    - 生成器需接受可选 operation 参数，保留 _discover_latest 每页 _check_cancelled(operation) 的取消纪律，抽取时一并统一两侧取消纪律
    - 影响面: src/dicepp_manager/release.py 单模块；_discover_latest 是下载热路径，需跑 tests/integration/manager/test_release_storage.py 等既有回归
    - 何时拉起: 下次需要调整分页上限/GitHub API 行为，或第三次出现同款分页循环时

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

## runtime

### [B-260727-de3b6b] OneBot Runtime 硬编码绑定 0.0.0.0，HOST 环境变量无效
- 创建: 2026-07-27
- 优先级: P2
- 类型: bug
- 改动量: S
- 问题表现:
    - 打包版 bot.py 显式以 host="0.0.0.0" 初始化 NoneBot，HOST 环境变量不生效，OneBot Runtime 实际监听 0.0.0.0:8080
    - Dashboard(4090)/Manager(4091) 均绑 127.0.0.1，仅 OneBot 8080 暴露全部网卡
    - Windows 首启时系统自动为该 exe 创建 TCP/UDP 入站阻止规则（事件 ID 2097）；真实用户环境可能弹防火墙/UAC 询问，干扰首次使用
    - 复现：Windows 启动 RC13 Portable，netstat/Get-NetTCPConnection 可见 0.0.0.0:8080
    - 证据：.temp/dicepp-v3.0.0rc13-windows-migration-acceptance-evidence.md「Windows UAC / 防火墙自动化」节、rc13-post-windows-network.json
- 开发备忘:
    - 修复方向 A：bot.py 读取配置项（如 global 配置或环境变量 DICEPP_ONEBOT_HOST）决定 bind host，默认 0.0.0.0 保持兼容
    - 修复方向 B：评估默认改为 127.0.0.1（LLOneBot/NapCat 通常同机部署），需确认远程 OneBot 客户端场景是否存在
    - 需先验证：打包后环境变量/配置在 PyInstaller onefile 运行时的读取路径；linux docker 部署是否依赖 0.0.0.0
    - 影响面：bot.py 及打包入口、docs/windows.md、docs/linux.md 部署文档
    - 风险点：改默认值会让远程连接 OneBot 的既有用户在升级后断连，需 release note 明示

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

