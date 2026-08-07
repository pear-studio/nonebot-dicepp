# Manager、归档恢复与升级架构

> 状态：当前实现契约。本页描述标准部署已经提供的行为；代码、发布元数据和测试是更细的事实来源。

本页面向开发者说明 Manager、RuntimeUnit、归档恢复和升级事务的系统边界。用户操作步骤见 [版本更新](../updates.md) 以及对应平台部署文档；发版流程见 [DicePP 发版系统](../releases/README.md)。

## 职责边界

Manager 是 DicePP 标准 Windows 与 Linux 部署的一部分，负责实例运行单元、维护事务、归档和兼容更新。Dashboard 是经过用户鉴权的界面与 Manager API 代理；它不直接控制 Docker 或子进程，不直接读写归档文件或执行数据恢复，但仍负责用户驱动的配置编辑、自身本地状态，以及受限的 Persona 角色卡编辑。Bot 只负责 NoneBot 与一个或多个 QQ 账号的业务运行。

没有 Manager 的旧部署不再是受支持拓扑。Dashboard 无法连接 Manager 时会报告不可用，不会退回到直接操作 Docker、子进程或 ZIP 文件。

| 组件 | 负责 | 不负责 |
|---|---|---|
| Manager | RuntimeUnit 生命周期、维护锁、持久 operation、归档/恢复事务、兼容更新 | Dashboard 用户界面、机器人业务逻辑 |
| Dashboard | 用户鉴权、状态展示、提交和查询 Manager operation、受限的 Persona 角色卡编辑 | Docker/子进程控制、归档文件写入、通用内容写入 |
| Bot Runtime | QQ Bot 业务与到 Manager 的本地控制心跳 | 自身部署生命周期 |

Linux 标准 Compose 包含 `bot`、`dashboard`、`manager` 三个服务。Manager 的 API 在 Compose 网络可用，并只映射到宿主机 `127.0.0.1:4091`，不向公网暴露；只有 Manager 挂载 Docker Socket。Windows 的 `DicePP.exe` 是托盘 Manager：它启动并监控 Bot Runtime 和 Dashboard，可为当前用户设置登录后启动，但不是 Windows Service。

## 实例与持久化数据

Bot、Dashboard 与 Manager 通过同一 `InstanceLayout` 解析实例根目录。兼容门面和 `DICEPP_DATA_DIR` 仍可供旧部署使用；新代码不得另建路径规则。

```text
<instance>/
├─ config/                 # DicePP 配置
├─ data/                   # Catalog 管理的运行数据
├─ content/                # 同一用户工作区（Bot、认证 Dashboard Persona 路由和本机 Agent）
├─ dashboard/data/         # Dashboard 本地账号与会话状态
└─ manager/
   ├─ state/               # API token、operation、维护 journal
   ├─ control/             # 仅 Bot 与 Manager 的控制凭据
   ├─ packages/            # 已下载的发布包缓存
   └─ backups/             # 用户归档与事务安全归档
```

`manager/` 不属于用户数据归档，`dashboard/data/` 也不进入 DicePP 归档。归档、下载缓存、Manager 事务状态和业务数据必须保持边界清晰。

### DataAsset Catalog 与 schema

`DATA_CATALOG` 是受管理持久化资产的唯一事实来源。每个 `DataAsset` 定义稳定 ID、逻辑区域和安全路径模板、类型、归档 profile、恢复授权范围，以及 SQLite 所需的 schema 引用。业务代码通过资产的 `resolve()` 或 `iter_matches()` 定位动态文件，不得重新拼接 glob 或把整个 `config/`、`data/` 当作默认写入授权根。

当前 Catalog 覆盖用户与 Bot 配置、实例数据库、Bot 核心/日志数据库、Persona 数据库、本地图片，以及完整归档中的用户 `content/`。发布随附的全局配置和模板、Dashboard 数据、Manager 数据，以及其他 NoneBot 插件和 NapCat/LLOneBot 数据不属于这个 Catalog。

每个 SQLite 文件独立保存 schema 元数据与迁移历史。`SchemaTarget` 仍是建表、版本读取和 forward migration 的执行事实来源；Catalog 只序列化 schema 身份。新数据库直接创建最新 schema，旧版本只允许按连续迁移向前升级，版本高于当前程序的数据库会被拒绝。生产降级不属于 schema migration 的常规路径，需依赖归档恢复或人工应急处理。

新增 DicePP 管理的持久化资产时，必须同时：

1. 在 `dicepp_data` 中定义或扩展 `DataAsset`；
2. 让业务代码经由该资产解析路径；
3. 为 SQLite 资产维护对应 `SchemaTarget`、schema 引用和 forward migration；
4. 覆盖 profile 枚举、路径安全、Catalog 摘要稳定性和迁移兼容性的行为测试。

### 用户内容

`content/` 是同一实例用户的工作区，而非 Manager 的独占写入区。Bot 读取实例内容；认证后的 Dashboard 仅可通过类型受限的 Persona 角色卡路由直接编辑 `content/characters/`，同机 Agent 也可直接编辑内容文件。Dashboard 不提供通用内容写入 API，也不经 Manager 代理这些写入。启动、升级和恢复不会从程序 `templates/` 静默复制、合并或覆盖内容。随发布提供的 `templates/characters/default/` 是只读模板，只有用户通过显式新建或导入操作后才会进入 `content/`。配置的实例外绝对内容路径可以继续使用，但不会被归档；恢复预览会给出警告。

## RuntimeUnit 与 Manager API

`RuntimeUnit` 是可独立启停、检查健康和读取日志的进程或容器，不等于逻辑 `bot_id`。标准 Linux 部署中一个 Bot 容器是 `dicepp-runtime`；标准 Windows 部署中一个 Bot 子进程是 `dicepp-runtime`。一个单元可承载多个 QQ 账号，因此启动、停止、重启、归档和恢复均作用于整个单元，不支持按账号独立维护。

Linux Docker Adapter 只对同时带有下列标签的容器执行固定操作：

```text
io.dicepp.managed=true
io.dicepp.runtime-unit=dicepp-runtime
io.dicepp.deployment-schema=2
```

容器名、Compose 服务名或用户输入都不能单独授权控制。Windows Process Adapter 只管理由托盘 Manager 创建的子进程，退出 Manager 时只会有序关闭它所持有的 Bot 和 Dashboard，不扫描或终止同名的其他进程。

Manager 默认监听本机 `127.0.0.1:4091`；Compose 中监听 `0.0.0.0:4091`，并仅映射宿主机 `127.0.0.1:4091`。首次启动会在 `<instance>/manager/state/api-token` 创建随机 HTTP API token，Dashboard 只读该文件并以 Bearer token 调用 API。Bot WebSocket 使用另一枚 `<instance>/manager/control/control-token`；只有 Bot 与 Manager 挂载该目录，Dashboard 不挂载、也不兼容读取旧 `data/dicepp.db` token。两枚 token 都不得写入 Compose 环境变量、业务配置或日志。

每项操作都会持久化到 `<instance>/manager/state/manager.db`。Dashboard 以 operation ID 提交和查询操作，因此刷新、重启或短暂断线不会丢失已提交的结果。实例级维护锁排他保护会停写的数据：冲突维护操作必须等待或被拒绝，不能并发修改同一批资产。

## 归档与恢复

归档仓库只属于 Manager，位于 `<instance>/manager/backups/`。Dashboard 保留 `/api/archives` 用户 API，但只代理给 Manager。

| Profile | 范围 | 恢复语义 |
|---|---|---|
| `regular`（默认） | 用户配置与 Catalog 管理的 `data/` | 不读取、修改或删除 `content/` |
| `full` | `regular` 加整个用户 `content/` | 将归档中声明的内容精确同步；当前程序后来新增且旧归档不知道的资产保留 |

创建归档时，Manager 获取维护锁，记录并停止原本运行的 RuntimeUnit，将数据流式写入 `*.zip.inprogress` 并在同一遍读取中计算摘要，验证成功后原子发布正式 ZIP，最后只恢复原先运行的单元。不会创建第二份原始文件快照。完整归档可能因大型内容延长停机；Dashboard 应先展示大小、文件数和可用空间估算。

当前归档使用 `manifest.json` format v2，记录 profile、DicePP 版本、来源平台、Catalog 描述/摘要、每个资产的 schema 和敏感标记，以及 payload 的逻辑路径、大小和 SHA-256。`config/user.json` 等可能含密钥的资产会被标记为敏感。读取和导入会拒绝路径穿越、重复成员、加密或未知压缩方式、symlink/special member、异常压缩比、超限成员以及未声明或摘要不一致的 payload。来源平台只用于展示和诊断：Windows 与 Linux 可在 Catalog 和 schema 兼容时互相恢复。

format v1 仍以保守兼容方式读取：它被视为 `regular`，并按旧 manifest 的声明范围生成恢复预览；SQLite schema 会在预览时只读校验，旧程序不能恢复由新程序写出的 schema。

### 恢复事务

恢复预览返回 `create`、`overwrite`、`remove` 或 `blocked`。删除只限于归档已声明且当前程序认识的资产；普通归档不会触碰 `content/`。Catalog 摘要用于诊断而非简单的全等门槛：当前程序不认识的资产、schema 身份变化或归档 schema 较新时会阻止恢复；当前程序新增但旧归档没有声明的资产会保留。

在用户显式确认后，Manager 会：

1. 获取维护锁并暂停原本运行的 RuntimeUnit；
2. 以相同 profile 创建并验证 `system` pre-restore 归档；
3. 写入持久 journal，以 `data_switch_started` 记录数据切换边界；
4. 通过临时文件、`fsync` 和原子替换写入，再执行精确删除；
5. 运行目标程序支持的 `SchemaTarget` forward migration；
6. 重启原先运行的单元，并检查 Manager store、配置、schema、RuntimeUnit 存活和新于启动基线的 Bot 控制心跳；
7. 健康通过后写入 `health_passed` 并完成提交。

任一步失败都会自动应用 pre-restore、再次检查 schema 和本地健康，并恢复原运行状态。Manager 重启或断电恢复时，未开始切换的事务清理临时状态；已经开始切换但尚未提交的事务自动回退；已写入健康标记的事务完成收尾。回退失败会保留 journal 和安全归档，等待后续恢复重试。NapCat、QQ、GitHub、LLM 等外部依赖只形成 warning，不触发这一本地数据回退。

归档恢复与升级保持各自独立的持久状态机，但通过 coordinator-neutral 的维护运行时边界共用控制心跳基线、原运行单元采集与暂停、重启、schema migration/validation 和本地硬健康检查。临时归档清理与 system 归档保留策略则经公开的 archive housekeeping 边界调用；升级流程不得调用归档 coordinator 的私有实现。维护 reservation 仍由发起事务持有直到 operation 与 journal 到达持久终态，不因共享原语的内部作用域提前释放。

回退在破坏阶段开始后被判定失败的事务是终态的（terminal rollback adjudication rule，升级与归档恢复两侧共用同一规则）：Manager 重启不会重放破坏性回退，只重复上报需要人工恢复，journal 保留在可恢复集合中以持续保护目标版本包与安全归档。终态 journal 在任一恢复性操作成功时自动退役——成功恢复归档（典型为 pre-upgrade 归档）或成功完成一次升级 commit——状态移出可恢复集合并保留 operation 历史作证据，目标版本包与安全归档的保护随之解除（归档转为普通归档走正常保留策略，不立即删除），Manager 重启不再重复上报。退役之前仍需人工介入：按目标平台的部署指南完成手工恢复，再通过一次归档恢复或升级让系统回到受管健康状态。

Bot 控制通道契约：Manager 是唯一的 `/v1/control/ws` 服务端，使用 `<instance>/manager/control/control-token` 认证；这枚 token 与 Manager HTTP API 的 `api-token` 完全独立，Dashboard 没有该路径的挂载权限。连接、单 bot 会话替换、ping/pong、状态心跳和 reload 请求/结果都保持 `dicepp-control-v1` 包络；已被替换的会话不能上报状态或完成请求。Manager 内存中持有 Bot 的版本和最新心跳，并通过 `/v1/control/bots` 提供认证 HTTP 调用。`/v1/control/reload` 暂时保留协议兼容，但 Bot 会明确回复通用配置热重载已停用，Dashboard 保存配置后不会调用它。完整配置由 RuntimeUnit 启动时加载；Dashboard 通过 Manager 原子保存，并使用现有 RuntimeUnit restart operation 使其生效。

控制迁移的混版本语义是明确失败而非回退：新 Dashboard 调用不具备 `control` capability 的旧 Manager 会报告“需先升级 Manager”；新 Manager 不再提供 Dashboard `/ws/control` 兼容端点，因此旧 Bot 会保持离线并在日志中重连，直到它被升级为 Manager URL。标准 Compose 以 Manager 为启动前提，Manager 本身不依赖 Dashboard。该拓扑变更和 Manager 自身发布均属于自动升级拒绝范围，必须在已有归档可恢复的前提下完成手工部署迁移；不要尝试让旧 Dashboard 直接接管控制通道。

手动归档不会被自动删除。`system` 安全归档默认只保留最近 5 份，活跃或失败事务引用的归档受保护。浏览器可从 Manager 导出已验证的 ZIP，也可向 Manager 导入 ZIP；导入会先流式校验并原子加入列表，绝不会自动恢复，仍需要预览和确认。

## 发布发现、自动升级与人工兜底

GitHub Release 是版本事实来源。Manager 使用机器可读的 `dicepp-release.json` 发现版本，而不猜测 artifact 文件名。发布元数据声明版本/频道、平台和架构、摘要、Catalog 与数据配置风险、`deployment_schema_version`、`minimum_manager_version`、`change_scope` 及是否允许自动升级。Linux 外层发布包还带有 `dicepp-package.json`，用于校验部署、Compose 和镜像 archive 元数据。

更新配置默认发现 stable 频道；预发布频道需要用户主动选择，自动下载默认关闭。即使包已经下载，安装也始终要求用户确认。

Manager 是**兼容的最新版本升级**的首选入口：它负责发现、下载、校验、pre-upgrade 归档、安装和本地硬性健康检查。Linux 失败时自动回退程序与数据；Windows 只预备一次性人工恢复入口。Manager 不重写用户的 Compose 或部署拓扑。普通 Manager 代码变化可以随自动升级切换（Linux 通过 handoff 协议、Windows 通过简化交接），破坏性交接变化（handoff 协议、Manager state、deployment schema、Compose 运行契约或安装布局不兼容）仍要求手工迁移。

### Linux 自动路径

Linux 自动安装处理当前部署结构兼容的发布。Manager 验证已确认的发布和空间，创建并验证普通 pre-upgrade 归档，从发布包本地 `docker load` 目标镜像，保留旧 immutable Image ID，再执行下述 Manager handoff 与容器切换。自动事务不会在安装时 `docker pull`，也不会把包内 Compose 复制到实例目录。它会深度比较当前与发布包的 Compose；除了 Bot/Dashboard 的 `image`、`build` 和顶层 Compose `version` 外，挂载、依赖、网络和其他拓扑差异均转为人工迁移。镜像默认值或未知非默认 Docker 配置无法安全判定时也会拒绝自动安装。

#### Linux Manager handoff 模型

首个带协议基线、Updater 入口、Dashboard DB 稳定挂载和受管本地 `current`
镜像别名的公开版本（rc20）必须手工完整三服务迁移并声明
`automatic_upgrade: no`；之后变更 Manager 的兼容候选声明
`linux_manager_handoff_protocol: 1`（release manifest 与 bundle 内层
manifest 一致），Linux 运行时确认协议、deployment schema、Compose 语义契约
和 immutable Image ID 后才允许自动升级；字段缺失或不受支持一律 fail closed
为手工迁移。

事务位于稳定实例根 `manager/recovery/<transaction-id>/`，与 Windows 简化方案
共享“旧 Manager 准备、目标 Manager 接管、健康后提交”的事务骨架，但交接用
三份事务文件表达：

- `request.json`：来源 Manager 唯一写入、之后不可变，绑定协议版本、事务/
  operation ID、来源/目标版本与 Image ID、Compose project、正式与备份容器名、
  原 restart policy、pre-upgrade 归档、Dashboard DB 快照及两个 `current`
  alias 的来源 Image ID。
- `decision.json`：目标 Manager 一次性写入，只允许事务绑定的 `commit` 或
  `rollback`；使用 first-write-wins 原子发布，`commit` 是唯一不可逆提交点，
  其后任何流程都不得回退来源。
- `result.json`：只有来源镜像中的 Updater helper 角色可写，记录
  `target-committed`、`source-restored` 或 `restore-failed`。

Updater 是来源镜像中独立 CLI 入口启动的一次性容器：不开放端口、不启动网页
服务、临时挂载 Docker Socket 与事务恢复目录。它验证 request 与正式 Manager
精确身份后，把来源 Manager 容器直接改名保留（不重建），用目标 immutable
Image ID 与事务标签创建新 Manager；任一失败或启动期限耗尽都恢复来源 Manager
并写 `source-restored`。来源 Manager、Bot、Dashboard 在交接窗口内 `restart=no`，
commit/rollback 后只恢复最终一侧的原策略；宿主机或 Docker daemon 在窗口内
重启不自动续作，用户或其 agent 先读取 request/decision/result 与 Docker
实际身份，再按 commit 是否存在选择 `restore-source` 或 `finalize-committed`
人工恢复。目标健康至少证明新 Manager 实际运行目标 Image ID、Dashboard 通过
ManagerClient compatibility handshake、Bot 控制心跳推进，且两个受管
`current` alias 精确指向目标 immutable Image ID；`commit` 写入前失败完整
恢复来源，写入后只能完成目标侧清理。

### Windows 自动路径

Windows 由当前 Manager 发起检查、下载和安装，Velopack 只负责切换版本化
`current/` 程序目录。Manager 在切换前创建并验证 pre-upgrade 归档，再将完整
`current/` 原子准备到稳定实例根下的单一 `manager/recovery/<transaction-id>/`
恢复事务。最小 `recover.json` 只引用现有 upgrade journal 的 transaction ID、
source/target version、pre-upgrade 归档和 RuntimeUnit 原状态，不成为第二个事实源。

同一恢复事务在实例根生成一次性 `DicePP-Recover.cmd`。脚本只使用 Windows
`cmd.exe` 的目录操作，不调用 `current/` 中的 Python、Manager helper 或独立恢复 EXE；
它不终止进程。用户关闭 DicePP 并运行脚本后，脚本将故障 `current/` 整体隔离、
将备份程序树整体换回，写入固定 `manual-restore.requested` 标志后才启动稳定根
`DicePP.exe`。旧 Manager 据此跳过平台程序回退，直接使用已绑定的归档恢复协调器，
再按原状态恢复 RuntimeUnit。任一目录被占用、换位或数据恢复失败都停止且保留
journal、标志、归档和程序备份；不逐文件合并、不搜索其他事务、不自动重试。

新 Manager 完成 migration、本地硬性健康检查和 upgrade commit 后，立即最佳努力删除
旧程序备份、恢复描述和根恢复入口。清理失败只产生 warning，不会反转已提交的成功结果。
简化协议不包含 UpdateGuard、started/health/rollback marker、进程身份监督或无人值守
程序降级。首个简化版本不扫描、迁移、恢复或清理任何旧 Guard 状态；遗留物不得阻止启动。

自动安装只接受与当前架构和频道匹配、具备 `velopack.win-x64.zip` 的发布，
并要求当前安装布局受 Velopack 支持、可读取完整 `current/`、可以在同卷稳定根准备
恢复事务。Manager 按外层 Release contract 校验 bundle，再按内层 manifest 校验并提取
唯一 full nupkg；缺少任何条件时会在程序和数据切换前拒绝。

### 必须人工处理的情形

以下情形明确走手动部署/恢复流程，而不是要求 Manager 设法继续：

- 第一次安装、旧式或不受支持的部署迁入标准拓扑；
- 指定安装较旧版本、人工回退或灾难恢复；
- 发布包含破坏性 Manager 交接变化（handoff 协议、Manager state、
  deployment schema、Compose 运行契约或安装布局不兼容）；
- Linux Compose、RuntimeUnit、挂载、网络或 deployment schema 迁移；
- 发布元数据标记为不兼容，或自动校验/空间/健康门槛未通过。

普通 Manager 代码变化不属于上述手工情形：Linux 上声明受支持 handoff 协议
的兼容候选可随自动升级切换 Manager；未声明协议或协议不受支持的 Manager
变更仍在 Linux 上 fail closed 为手工迁移。

人工操作前先创建并验证归档，按目标平台的部署指南替换程序或镜像，必要时从已验证归档恢复。Windows 只允许事务预先生成的根恢复脚本整体换回 `current/`，不得手工拼接文件；不要未经对比直接用发布包内 Compose 覆盖实例目录，也不要把一次自动升级拒绝当作可安全忽略的警告。

## 明确非目标

- 按 QQ 账号单独启停共享 RuntimeUnit；
- Updater 在宿主机或 Docker daemon 重启后自动续作（交接窗口中断按文档人工恢复）；
- 自动改写 Linux Compose 或部署 schema；
- 零停机归档、归档加密或自动云备份；
- NapCat、LLOneBot 和其他 NoneBot 插件的数据迁移；
- 用户内容库的增量归档、去重或远程内容包管理。
