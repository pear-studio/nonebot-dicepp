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

### [B-260623-76a47a] 页面闪烁 — 全局 loading overlay 被 monitor 轮询触发
- 创建: 2026-06-23
- 优先级: P1
- 类型: bug
- 改动量: S
- 问题表现:
    - 切换页面后（尤其在数据浏览界面）每隔几秒整个页面闪烁一次
    - 根因：loadMonitor() 中 setInterval 每 10s 调用 loadMonitorData()
    - loadMonitorData() 走 api() helper，每次调用设置 apiLoading = true
    - apiLoading 绑定全局 loading overlay（全屏半透明 spinner），导致所有 tab 都闪
    - monitorTimer 在切换 tab 时未清除，且重复进入 monitor tab 会创建重复 timer
    - 代码位置：dashboard.html:552 (apiLoading=true)、:957 (setInterval)
- 开发备忘:
    - 方案A：loadMonitorData() 绕过全局 apiLoading，直接用 fetch 或传 skipLoading 参数
    - 方案B：setInterval 在切换 tab 时清除（在 loadTabData 中对非 monitor tab 执行 clearInterval）
    - 建议两个方案同时做：静默轮询 + tab 离开时停 timer
    - 影响面：dashboard.html 前端单文件，api() helper + loadMonitor/loadMonitorData + loadTabData
    - 风险：极低，纯前端改动

### [B-260623-aeaea8] Bot配置 tab 默认可编辑，缺少编辑/查看模式切换
- 创建: 2026-06-23
- 优先级: P1
- 类型: bug
- 改动量: S
- 问题表现:
    - Bot配置 tab 中 master/admin/friend_token/persona/nickname 的 input 字段直接可编辑
    - "保存"按钮始终可见，误触即覆盖线上 bot 配置
    - 而配置编辑 tab 有明确的 "编辑→编辑框→保存/取消" 状态机，两者设计不一致
    - 代码位置：dashboard.html:248-291（Bot配置），对比 :186-226（配置编辑字段视图）
- 开发备忘:
    - 给 Bot配置 tab 增加编辑模式开关：默认只读展示，点击"编辑"按钮后才可修改
    - 与配置编辑 tab 的交互模式保持一致
    - 影响面：dashboard.html botcfg tab 模板 + botConfigFields 状态管理
    - 风险：极低，纯前端改动

### [B-260623-b15e43] 登录后默认不选择已开启的 bot
- 创建: 2026-06-23
- 优先级: P1
- 类型: bug
- 改动量: S
- 问题表现:
    - 登录后 left sidebar 的 bot selector 显示 "-- 选择 Bot --"，未自动选中任何 bot
    - 数据浏览、配置编辑等 tab 都需要先手动选 bot 才能看到内容，多了一步无意义操作
    - 代码位置：loadBots() 只填充 this.bots 数组，selectedBotId 保持 ''
    - 无任何 auto-select 逻辑
- 开发备忘:
    - loadBots() 完成后，若 bots 非空且 selectedBotId 为空，自动选 bots[0]
    - 或 checkAuth() → loadBots() 成功后自动调用 onBotChange 选中第一个
    - 影响面：dashboard.html init/checkAuth/loadBots/onBotChange
    - 风险：极低，注意处理 bots 为空的情况（当前已处理 selectedBotId 为空时的提示）

### [B-260623-f6b322] 配置编辑中 _comment / _llm_comment 键不应出现
- 创建: 2026-06-23
- 优先级: P1
- 类型: bug
- 改动量: S
- 问题表现:
    - global.json 包含多个 comment 键被展示在配置编辑字段列表中：
      _comment、persona_ai._comment_character、persona_ai._comment_persona、
      persona_ai._comment_tables、persona_ai._comment_timezone
    - 这些键是给开发者看的注释，不应暴露在 Web UI
    - 根因：config_merged API 的 _annotate_deep 递归遍历所有键，无过滤逻辑
    - 代码位置：app.py:623-643 (_annotate_deep)、dashboard.html:743 (buildConfigFields)
- 开发备忘:
    - 方案A（推荐）：后端 config_merged 遍历时跳过 key 以 '_comment' 或 '_llm_comment' 结尾的路径
    - 方案B：前端 buildConfigFields 过滤
    - 建议后端过滤（源头解决），同时前端加防御性过滤
    - 影响面：app.py _annotate_deep、dashboard.html buildConfigFields
    - 风险：极低，注意用 endswith 或正则精确匹配，避免误杀正常配置键

### [B-260623-7f3f82] 内容管理查询 tab 存在多个问题（手动加载按钮、中文 DB 名报错、表格无数据/表头错误）
- 创建: 2026-06-23
- 优先级: P1
- 类型: bug
- 改动量: M
- 问题表现:
    - 9a: 切到 queries tab 时不自动加载数据库列表，需手动点"加载数据库列表"按钮；其他 tab（如 decks）切过去自动加载
    - 9b: 选择 DND5E混合 报错 "db_name 格式无效" — _validate_identifier() 使用 ^[a-zA-Z0-9_-]{1,64}$ 正则，中文不通过
      实际文件 content/queries/DND5E混合.db 是合法存在的
    - 9c: 选择数据库后表格只有表头没有数据 — query DB 实际有 data 表（2000行）和 redirect 表（736行），
      列名是 名称/英文/来源/分类/标签/内容，但前端硬编码表头为 ID/内容/操作，与真实列不匹配导致显示空
    - 代码位置：app.py:178 _validate_identifier、:867 content_queries_entries（默认 table='data'）、
      dashboard.html:305-354（queries UI）、:323-328（硬编码表头）
- 开发备忘:
    - 9a: loadTabData 中 content tab 时若 contentSubdir === 'queries' 自动调用 loadQueryDbs()
    - 9b: _validate_identifier 对 db_name 放宽限制，或 query DB 走独立校验（允许中文文件名）
      注意 path traversal 检查已独立做了 _is_path_traversal，放宽 _validate_identifier 不影响安全
    - 9c: 查询结果使用动态表头（从 records 解析 columns，类似数据浏览的做法），去掉硬编码 ID/内容/操作
      默认 table 参数也可能是 'redirect' 而非 'data'，需要支持表选择或自动检测
    - 影响面：app.py _validate_identifier/content_queries_entries、dashboard.html queries 渲染
    - 风险：低-中，放宽校验需确保 path traversal 保护仍然有效（已有独立检查）

### [B-260623-7d7d93] 配置编辑与数据浏览缺少中文标签和解释
- 创建: 2026-06-23
- 优先级: P1
- 类型: feature
- 改动量: M
- 问题表现:
    - 配置编辑：字段以 dotted key 展示（如 persona_ai.segment_max_chars），schema.json 的中文描述仅作为 :title tooltip（鼠标悬停才可见），无可读的中文名
    - 数据浏览：表名来自 SQLite sqlite_master（如 user_stat, group_config），纯英文无中文映射
    - 小白用户无法理解这些技术字段名
    - 当前 schema.json 有 137 条描述，但描述本身就是 "默认值: xxx" 格式，不够友好
- 开发备忘:
    - 方案：在 schema.json 中扩展描述格式或新增 label 字段，支持中文短标签 + 详细描述
    - 数据表名映射：维护一个 table_name → 中文名 的映射（前端或后端均可）
    - 配置编辑字段视图展示中文标签为主，dotted key 为辅（灰色小字）
    - 影响面：schema.json 格式（需向后兼容）、dashboard.html 配置渲染、app.py config_merged 返回格式
    - 风险：低-中，schema.json 格式变更需谨慎，确保不破坏已有描述

### [B-260623-6754e2] 配置编辑缺少分组功能和隐藏低优先级配置
- 创建: 2026-06-23
- 优先级: P1
- 类型: feature
- 改动量: L
- 问题表现:
    - 137 个配置键 flat 展开为长列表，无分组无折叠
    - persona_ai.* 有 60+ 个键混在核心配置中，严重干扰普通用户
    - command_split、log.*、health_monitor.* 等低优先级配置与核心配置同级展示
    - 用户需要反复滚动才能找到需要的配置项
    - 代码位置：dashboard.html:189-226（配置字段视图）、schema.json（无分组元数据）
- 开发备忘:
    - 需要梳理所有配置项，划分分组（如：基础设置、Bot行为、Persona AI、Log/监控、高级/杂项）
    - 方案A：在 schema.json 中增加 group/priority 元数据
    - 方案B：在 global.json 的 key 前缀隐式分组（但已有 flat key 如 command_split 不好办）
    - 建议方案A：schema.json 扩展为结构化描述，每条包含 label/group/priority/description
    - 前端按 group 分组渲染，默认折叠低优先级组，提供"展开全部"开关
    - 影响面：schema.json 格式、app.py config_merged、dashboard.html 配置渲染
    - 风险：中，schema.json 格式变更影响面较大，需要向后兼容设计
    - 建议先独立完成分组梳理文档，确认分组方案后再实现

### [B-260623-638226] 内容管理中 .gitkeep 文件应被过滤
- 创建: 2026-06-23
- 优先级: P2
- 类型: bug
- 改动量: S
- 问题表现:
    - content 目录下有 4 个 .gitkeep：decks/.gitkeep, queries/.gitkeep, random/.gitkeep, excel/.gitkeep
    - 内容管理的文件列表会展示这些 0 字节的 .gitkeep 文件
    - 这些是 git 占位文件，对用户无意义
    - 代码位置：app.py:844 content_list 的 iterdir() 无过滤
- 开发备忘:
    - content_list 中在 iterdir 后过滤掉 name == '.gitkeep' 的文件
    - 或使用更通用的规则：过滤 '.' 开头的隐藏文件
    - 影响面：app.py content_list 一行过滤条件
    - 风险：极低，.gitkeep 是标准 git 占位约定，过滤安全

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

