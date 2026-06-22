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

### [B-260618-56a0a3] 数据库迁移架构优化调研
- 创建: 2026-06-18
- 优先级: P2
- 类型: refactor
- 改动量: L
- 问题表现:
    - 当前数据库迁移采用线性堆叠脚本（v1→v2→v3），缺少历史脚本清理策略
    - 不知何时可删旧脚本、如何确认所有部署已越过某版本
    - V3 迁移已出现职责混杂（同时做 DROP TABLE variable/favor 和 ALTER TABLE hub_config RENAME COLUMN）
    - 长期线性堆叠会导致迁移链越来越长、维护成本递增
- 开发备忘:
    - 调研方向：Alembic 式 delta 脚本 vs 声明式 schema + 自动 diff vs 其他轻量方案
    - 历史脚本的清理判定策略（如何确认所有存量部署已跑过某版本）
    - 产出调研文档后讨论方案，本次不做代码改动

## deploy

### [B-260615-19b0fa] 生产备份与恢复策略
- 创建: 2026-06-15
- 优先级: P1
- 类型: feature
- 改动量: M
- 问题表现:
    - 当前版本发布/回退流程即将切到镜像 tag 部署，但缺少对应的生产备份与恢复机制。
    - 镜像回退无法恢复已经变更的数据库、config、data、content 或运行时状态，容易让“可回退”产生误导。
    - 生产更新前、定时备份、恢复验证、保留策略和敏感数据处理规则尚未固化。
- 开发备忘:
    - 梳理需要备份的范围：config/、data/、content/、数据库文件、LLOneBot 相关持久化数据，以及生产环境中额外存在的本地环境变量文件。
    - 设计升级前备份、定时备份、恢复演练、保留周期和失败告警。
    - 后续可与 version-deploy / deploy-docker 联动：当 release metadata 标记 数据变更/配置变更 为 yes 时，生产更新前必须确认备份。
    - 注意恢复流程不能只写文档，至少需要可验证的恢复步骤或脚本入口。

## deployment

### [B-260618-8fce87] 引入 DicePP Manager 统一管理 Bot 与 Dashboard 生命周期
- 创建: 2026-06-18
- 优先级: P1
- 类型: feature
- 改动量: XL
- 问题表现:
  - 用户完成首次部署后，仍缺少通过 Dashboard 完成 Bot 启停、重启、更新和回滚的能力。
  - Dashboard 未来还需要更新自身；由 Dashboard 直接替换自身容器或持有 Bot 子进程，难以保证操作完成、失败恢复和职责边界。
  - 直接向 Dashboard 挂载 Docker Socket 会把宿主机高权限暴露给复杂 Web 应用。
  - Linux 以 Docker 容器运行，Windows 以打包进程运行；如果分别实现生命周期逻辑，容易形成两套状态机、错误语义和更新流程。
- 开发备忘:
  - 引入常驻的 DicePP Manager，作为 Dashboard 与运行时之间的最小权限管理层；Dashboard 不直接访问 Docker Socket，也不直接持有 Bot 生命周期。
  - Manager Core 统一实现操作状态机、鉴权、审计、健康检查、并发控制、版本兼容、失败回滚和对 Dashboard 的 API。
  - 平台差异收敛到 Runtime Backend：Linux 使用 DockerRuntime 管理容器，Windows 使用 ProcessRuntime 管理进程；禁止在业务代码中散落平台判断。
  - Manager 仅提供受限的启停、重启、更新、状态、日志和回滚接口，禁止任意 Docker API、镜像名称和命令执行。
  - Windows 最终发布一个用户入口 `DicePP.exe`；同一可执行文件以内部 manager/dashboard/bot/update 模式运行多个独立进程。采用 onedir 发行包，不强求单一物理文件。
  - Windows 自更新采用 staging 新版本接管：校验下载、停止旧进程、替换程序、健康检查、失败恢复备份；运行状态放在 `data/manager/`，不能只保存在进程内存。
  - Linux 保持 Manager、Dashboard、Bot 独立镜像；Windows 单一入口只是分发形式差异，两端共享 Manager API 和生命周期语义。
  - 永久控制通道由受管理组件主动建立：Manager 通过出站 WSS 连接 DiceHub，DiceHub 在既有双向通道上下发生命周期命令；用户无需暴露 Manager/Bot 入站端口。
  - DiceHub 的远程更新、重启、诊断和回滚必须发给 Manager，不允许 Bot 直接承担替换自身进程或容器的操作；Bot 现有 DiceHub 业务连接与运维控制通道保持职责分离。
  - Manager 落地后接管安装级本地控制凭据的生成、轮换和恢复；凭据属于整个 DicePP 安装，不属于 Dashboard 私有数据。DiceHub 等远程控制端使用独立的 device-code/网页授权凭据，不复用本地安装凭据。
  - 当前 Dashboard PR 不实现 Manager，只补齐独立 Dashboard 镜像发布、Windows 双 EXE 打包和测试门禁。
  - 后续还需细化部署形态、Manager 自身升级边界、鉴权协议、发布清单、镜像/文件签名以及 Runtime Backend contract tests。

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

### [B-260622-7d8610] structured_collect 工具层通用输出校验与自动纠正
- 创建: 2026-06-22
- 优先级: P2
- 类型: feature
- 改动量: M
- 问题表现:
  - `run_structured_collect` 当前只校验 LLM 是否调用了目标工具、参数是否符合 JSON schema，不校验参数内容是否满足业务约束（如字数范围、必填语义有效性）
  - 日记 `record_diary_entry.diary` 要求 100-200 字但仅凭 prompt 引导，LLM 可能超出范围，无代码层兜底
  - 其他工具字段可能存在同类问题（`context_summary` 30-60 字等），缺少通用校验入口
- 开发备忘:
  方向：不是为单个字段加 ad-hoc 校验，而是实现工具层面的通用校验/报错/重试机制。可参考社区开源 agent 框架的 validation feedback loop 实现。

  大致形态：工具 executor 返回结果时，增加一层 validate hook——可配置的校验规则（如 `Field.min_length`/`max_length`、自定义 validator、正则等）检查 LLM 输出 → 不符合时以 correction 形式返回错误信息给 LLM → LLM 修正后重新调用工具 → 复用现有 `max_corrections` 限制重试次数。

  字符数校验作为首个应用：从 Pydantic Field 的 `min_length`/`max_length` 自动生成校验规则，无需手写。

  影响面：`tool_bridge.py`（`run_structured_collect` / `build_collecting_registry`）、`loop.py`（correction 计数需兼容内容校验触发的重试）、`collecting.py`（各工具的 Pydantic Field 定义）。

### [B-260622-5ee332] chat_time 从 user_stat.data 迁移到独立 user_config 表
- 创建: 2026-06-22
- 优先级: P2
- 类型: refactor
- 改动量: S
- 问题表现: chat_command 在私聊场景将 chat_time 存储在 user_stat 的 JSON blob 中，与统计数据混在同一行。UserStatInfo.serialize() 不保留未知键，导致 chat_time 被 process_message 静默丢弃，私聊冷却功能形同虚设。即使 B-260602-4263c4 加了锁，序列化不兼容问题依然存在。
- 开发备忘: 新增 user_config 表（类似 group_config 但 key 为 user_id），将 chat_time 从 user_stat 迁移过去。移除 chat_command 对 user_stat 的依赖，改用独立表。移除 StatManager.update_user_stat_data()（不再需要）。

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

