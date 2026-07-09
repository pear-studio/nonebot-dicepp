# DicePP 3.0 数据状态架构

本文记录 3.0.0 正式发布前需要稳定下来的数据状态、迁移、Manager 与存档恢复设计。目标是让自托管用户在 3.0.0 后拥有清晰的升级路径、状态边界和恢复手段，避免正式发布后再背负不必要的兼容债。

## 目标

- 明确 DicePP 自托管实例的持久状态边界。
- 建立统一但不过度设计的 SQLite schema lifecycle。
- 区分应用自管状态、bot 状态、Dashboard 自身状态和用户自管内容。
- 让 Dashboard 支持类似“游戏存档”的创建与恢复能力。
- 在 3.0.0 前落地本地 Manager，使 Dashboard 不直接操作 Bot 进程、容器或持久化文件。

## 不纳入 3.0.0

- `content/` 的格式重构和自动迁移。`queries`、`excel`、`decks`、`random` 后续另行设计。
- DiceHub 远程 WSS 控制通道、云端远程更新、远程回滚、device-code 授权闭环。
- Dashboard 自身数据库 `dashboard/data/dashboard.db` 纳入 DicePP 存档。
- 完整定时备份、云端备份、加密备份、自动恢复演练。

## 状态边界

`config/` 是 DicePP 管理的配置快照。允许程序规范化写回。

`data/` 是 DicePP 实例运行状态根目录，可能包含敏感数据，不应公开或提交。

推荐形态：

```text
data/
  dicepp.db
  backups/
  runtime/
  local_images/
  bots/
    <bot_id>/
      bot_data.db
      log.db
      personas_data_<character>.db
```

`data/dicepp.db` 是实例级 SQLite，承载不属于单个 bot 的本地状态，例如本地控制凭据、未来 Manager 状态、版本与操作状态。

`data/bots/<bot_id>/bot_data.db` 是 bot 核心状态。

`data/bots/<bot_id>/log.db` 是 bot 日志数据库。它需要 schema 管理，但备份/保留策略可以与核心状态区分。

`data/bots/<bot_id>/personas_data_*.db` 是 Persona 数据库。短期保留每角色一个 DB。

`dashboard/data/dashboard.db` 属于 Dashboard Web 应用自身状态，包括登录、会话、审计等，不纳入 DicePP 存档。

`content/` 暂按用户自管源资产处理，不纳入自动 schema migration，也不在启动时 canonical rewrite。

## 配置规范化

`config/*.json` 是 DicePP 管理的配置快照，不按用户内容资产处理。

规则：

- 启动时允许 canonical rewrite。
- 新增普通配置直接使用默认值并写回。
- 过时普通字段可以丢弃。
- 未知普通字段可以丢弃。
- 敏感、身份、路径、外部入口、危险行为开关字段不能静默丢弃，必须迁移、告警或禁用相关功能。
- 不保留 `.bak`。

关键字段判定：

- 身份与权限：master、Dashboard/Manager 控制凭据、远程控制授权。
- 数据定位：data dir、bot id、content path 等。
- 外部服务入口：监听地址、WebSocket 地址、Hub endpoint。
- 密钥与付费资源：API key、provider credentials、加密密钥。
- 危险行为开关：自动更新、远程控制、删除、恢复、外部命令执行。

推荐落地为轻量 normalization layer：

```text
load raw JSON
  -> pre-migration hooks for valuable old fields
  -> drop unknown ordinary fields
  -> validate with Pydantic
  -> drop/default invalid ordinary fields
  -> apply critical field policy
  -> write canonical JSON
```

当前实现状态：

- `ConfigLoader` 在加载 `config/global.json`、存在的 `config/user.json`、以及当前 bot 的 `config/bots/<account>.json` 后执行 canonical rewrite。
- rewrite 先规范化每个 JSON layer，再合并与应用环境变量；只有最终 `BotConfig` 校验成功才写回文件。
- `global.json` 作为默认快照补齐普通字段默认值；`user.json` 和 account config 保持 override layer 语义，不写入完整 merged config。
- 普通未知字段会被丢弃；普通字段类型错误在字段有 Pydantic 默认值时默认化。
- 身份/权限、路径、外部入口、密钥/凭据、危险行为相关字段使用保守策略：缺失时不合成默认值写回；已存在但无效，或看起来像关键字段的未知字段，不会被静默丢弃或默认化，当前直接保留验证失败行为。
- 环境变量覆盖仍为最高优先级，但不会写回 JSON。
- rewrite 使用 `.tmp` + atomic replace，不生成 `.bak`。

## DB Migration Model

迁移是 forward-only。生产 downgrade 不作为标准能力；数据回退依赖存档/备份。没有备份时属于 emergency repair，不进入常规 migration contract。

3.0.0 Data Foundation 第一批采用破坏性切换：当前没有真实用户数据需要兼容，旧 `schema_version`、旧 core migration 链与旧 persona runtime migration 链不再兼容，不提供 legacy bridge。旧 runtime migration 文件已退役，避免后台继续运行未验证的兼容逻辑。

每个 SQLite 文件自持版本与迁移历史。共享框架，不共享一个全局 schema version。

目标：

```text
instance  -> data/dicepp.db
bot_core  -> data/bots/<bot_id>/bot_data.db
bot_log   -> data/bots/<bot_id>/log.db
persona   -> data/bots/<bot_id>/personas_data_*.db
```

每个 target 由数据所有权模块维护。模块不是 Python 包粒度，而是数据 owner：

- `core/data` 维护 `instance`、`bot_core`、`bot_log`。
- `module/persona/data` 维护 `persona`。

`module/persona/data/schema_sql.py` 只保存 latest schema SQL fragments，供 `SchemaTarget.create_latest_schema` 创建新 Persona DB 使用；它不是旧 runtime migration 链。后续真实版本升级必须挂到对应 target 的 `migrations` / `async_migrations`，并配套 forward migration 测试。

新 DB 不从 v1 跑完整迁移链。新 DB 直接创建 latest schema，并记录当前版本。已有 DB 的处理规则：

```text
no schema_metadata and no user tables:
  create_latest_schema()
  set current_version = latest
  record schema_migrations(version=latest, name='create_latest_schema')

no schema_metadata and any user table:
  reject as unmanaged existing DB

schema_metadata exists and current_version == latest:
  noop

schema_metadata exists and current_version < latest:
  run target migrations current+1 ... latest

schema_metadata exists and current_version > latest:
  reject as unsupported future DB
```

最小元数据：

```sql
CREATE TABLE IF NOT EXISTS schema_metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_migrations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  version INTEGER NOT NULL UNIQUE,
  name TEXT NOT NULL,
  applied_at TEXT NOT NULL
);
```

先不做 checksum、dependency graph、down migration、复杂 baseline generation。

第一批 target migration 可以为空；后续新增版本时，缺失或不连续 migration 必须报错。所有 migration 必须 retry-safe。简单 migration 推荐 idempotent，但不强制所有 migration 完全幂等。

migration 不应依赖当前业务模型或 Repository。允许依赖标准库、schema lifecycle helper，以及 migration 文件内定义的旧格式快照。第一批不再维护旧 core migration import whitelist，也不保留 persona 旧表 runtime rename/drop 后台路径。

测试规则：

- fresh DB -> latest schema。
- already latest -> noop。
- unmanaged existing DB 拒绝启动，错误需能定位到目标和表名。
- current > latest 拒绝。
- current < latest 且缺失或不连续 migration 拒绝，且不得产生副作用。
- forward migration 成功时记录 `schema_migrations` 并更新 `schema_metadata.current_version` / `updated_at`。
- forward migration 失败时整批回滚，不能留下部分版本记录或半成品表。
- fresh latest schema 与 v1 -> v2 migrated schema 应能用 fixture helper 比较用户表、列和索引等价。
- target schema 落在正确物理 DB。

baseline/squash 第一版只写规则，不实现复杂 squash。未来需要压缩时，baseline 只服务新环境；是否重新引入旧版本支持窗口由发布策略另行决定。

## DicePPDatabase 与本地控制凭据

新增 `DicePPDatabase`，文件为 `data/dicepp.db`。

第一批真实用途是 local control token。当前策略是破坏性切换：新 token 只存在 `data/dicepp.db` 的 `local_control_token` 表；不读取、不迁移旧 `data/runtime/local-control.token`。旧文件可忽略，不作为兼容来源。Bot 和 Dashboard 必须同时切到新来源。

token 建专表，不使用通用 KV：

```sql
CREATE TABLE local_control_token (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  token TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

Dashboard 不直接获得通用 `DicePPDatabase` 访问能力。短期由 narrow shared accessor 暴露 token 读写能力：

- Bot 调用 ensure。
- Dashboard 调用 read。
- Dashboard 不访问其他 instance tables。

Manager 落地后应以受控配对/授权流程替代 Dashboard 共享读取本地 token store。该 TODO 写入代码注释或设计文档，不为 RC 阶段临时兼容单独开 backlog。

## Manager 3.0 范围

3.0.0 需要本地 Manager 完整，DiceHub 远程控制保留 TODO。

纳入：

- Manager 常驻层。
- Dashboard 通过 Manager 管理 Bot/Dashboard 生命周期。
- Manager Core：本地生命周期状态机、鉴权、审计、健康检查、并发控制、手动升级边界所需的状态可见性。
- Runtime Backend：Linux `DockerRuntime`、Windows `ProcessRuntime`，共享 contract。
- 本地启停、重启、Linux Docker 运行状态管理、Windows 手动升级边界和存档恢复所需能力。

不纳入：

- DiceHub 远程 WSS 控制通道。
- 云端触发更新、重启、诊断、回滚。
- DiceHub device-code 授权闭环。

Dashboard 不直接访问 Docker socket，不直接替换 Bot 进程/容器，不直接覆盖运行中的 DB 文件。

## 存档与恢复

存档是 Dashboard 的独立能力，升级流程只是复用它。用户可以随时创建、查看、删除、恢复存档。

默认存放：

```text
data/backups/
```

格式：单个 zip 包 + manifest。

示例：

```text
data/backups/2026-06-24T153000Z-v3.0.0rc3-before-upgrade.zip
```

包含：

- `config/user.json`
- `config/bots/<bot_id>.json`
- `data/dicepp.db`
- `data/bots/**/bot_data.db`
- `data/bots/**/log.db`
- `data/bots/**/personas_data_*.db`
- `data/local_images/`

不包含：

- `config/global.json`
- `config/bots/_template.json`
- `dashboard/data/dashboard.db`
- `content/`
- `data/backups/`
- `data/runtime/`
- `data/bots/*/logs/`
- LLOneBot 数据

manifest 第一版记录：

```json
{
  "format_version": 1,
  "created_at": "2026-06-24T15:30:00+08:00",
  "dicepp_version": "v3.0.0rc3",
  "description": "before upgrade",
  "scope": {
    "included": ["config/user.json", "config/bots/*.json", "data/dicepp.db", "data/bots/*/bot_data.db", "data/bots/*/log.db", "data/bots/*/personas_data_*.db", "data/local_images"],
    "excluded": ["config/global.json", "config/bots/_template.json", "dashboard/data/dashboard.db", "content", "data/backups", "data/runtime", "data/bots/*/logs"]
  },
  "databases": [
    {
      "path": "data/dicepp.db",
      "target": "instance",
      "schema_version": 1
    }
  ],
  "checksum": {
    "algorithm": "sha256",
    "files": {}
  }
}
```

做 sha256 checksum，不做签名和加密。

创建存档时允许短暂停写或进入维护状态。第一版不追求零停机在线快照。

恢复必须由 Manager 编排：

```text
select archive
  -> stop/quiesce Bot
  -> create pre-restore archive
  -> verify target archive checksum
  -> restore config + data scope
  -> start Bot
  -> target version runs forward migration if needed
```

恢复前自动创建 pre-restore 存档。pre-restore 创建失败时默认不继续恢复。

恢复失败时：

- 显示失败原因。
- 保留 pre-restore 存档。
- 提供基于恢复前备份存档继续预览和恢复的入口。
- 不做复杂自动多次回滚。

跨版本规则：

- 允许从旧版本存档恢复到新版本程序，恢复后自动 forward migration。
- 不承诺 Dashboard 3.0.0 完整编排“程序回退到旧版本 + 恢复旧存档”。
- 存档 manifest 记录 DicePP 版本和 schema 信息，为未来回退路径保留证据。

release metadata 标记 `数据变更: yes` 或 `配置变更: yes` 时，升级流程必须强提示，并要求选择已验证的既有存档或显式跳过存档。存档功能本身仍可独立使用。

## 实施阶段

### 阶段 A：Data Foundation

目标：3.0.0 前稳定数据状态边界和迁移基础。

范围：

已完成第一批：

- `data/dicepp.db` 与 `DicePPDatabase`。
- local control token 入库；不读取、不迁移旧 token 文件。
- `SchemaTarget` lifecycle。
- fresh DB create latest schema。
- unmanaged existing DB 拒绝启动。
- `schema_metadata` + `schema_migrations`。
- `bot_core`、`bot_log`、`instance` targets。
- persona bot 级 schema fragment 由 `bot_core` target 创建。
- persona 角色 DB target 化。
- config canonical rewrite。
- forward migration 护栏测试已建立：缺失/不连续版本拒绝、成功记录 metadata/history、失败回滚、fresh latest 与 migrated schema 等价比较。

### 阶段 B：Manager Foundation

目标：3.0.0 前建立本地 Manager 作为 Dashboard 与运行时之间的最小权限管理层。

范围：

- Manager Core。
- 本地 API 与鉴权。
- 操作状态机、审计、健康检查、并发控制。
- Linux DockerRuntime 与 Windows ProcessRuntime contract。
- Bot/Dashboard 接入 Manager。
- DiceHub 远程控制仅保留 TODO。

已完成第一批本地 Dashboard Manager：

- Dashboard 内嵌 Manager Core，提供 `/api/manager/status`、`/api/manager/operations`、`/api/manager/bots/{bot_id}/{action}` 与全局运行日志 `/api/manager/logs`。
- Manager API 复用 Dashboard session 鉴权；生命周期操作写入审计日志。
- 操作状态机支持 queued/running/succeeded/failed/rejected，并按 bot 限制同一时刻只允许一个 in-flight 操作。
- `manager_operations` 写入 `dashboard.db`，Dashboard/Manager 重启后仍可查看历史；重启遗留的 queued/running 操作恢复为 failed。
- Manager health 暴露 runtime backend、Manager API version、operation schema version 和 DicePP package version。
- 默认 `UnavailableRuntimeBackend` 不伪造启停或日志；`ProcessRuntimeBackend` 与 `DockerComposeRuntimeBackend` 需显式 opt-in 环境变量配置后才启用。
- Linux Docker Compose 与 Windows/local Process runtime 共用 contract tests，不访问真实项目 data，不默认调用真实 Docker。
- Dashboard 运行监控页合并展示连接状态、Manager/runtime 状态、最近操作，支持启停/重启；运行日志入口读取全局 runtime log，不挂在单个 bot 行上。

仍留给后续阶段：

- 阶段 C 负责本地生命周期、Windows 单入口和手动升级边界；自动版本切换、自动回滚与失败恢复编排另行设计，不纳入 3.0。
- 阶段 D 负责存档创建、恢复、pre-restore 和恢复失败入口。
- DiceHub 远程控制、云端触发和 device-code 授权仍不纳入 3.0.0 本地 Manager Foundation。

### 阶段 C：Local Lifecycle And Manual Upgrade Boundary

目标：3.0.0 前让 Dashboard 通过 Manager 完成本地生命周期、Windows 单入口和手动升级边界；版本切换统一走人工流程。

范围：

- 启停、重启、状态、日志。
- Windows 单入口托盘与 `ProcessRuntime` 启停/日志能力。
- Linux Docker Compose 本地生命周期管理。
- Dashboard 不直接访问 Docker socket 或替换进程/文件。
- Windows 和 Linux 3.0.0 均不提供 Dashboard 自动 update/rollback；手动升级通过阅读 Release metadata、创建存档、替换镜像 tag / zip / 发行目录、必要时恢复存档完成。

已完成 Dashboard Manager 本地生命周期管理，并移除自动版本操作链路：

- `DockerComposeRuntimeBackend` 继续保持 opt-in：需要 `DICEPP_MANAGER_RUNTIME=docker-compose`、`DICEPP_MANAGER_DOCKER_COMMAND` 和 `DICEPP_MANAGER_DOCKER_SERVICE`，可选 `DICEPP_MANAGER_DOCKER_CWD` / `DICEPP_MANAGER_DOCKER_TIMEOUT`。
- Docker Compose runtime 只支持 `status`、`start`、`stop`、`restart` 和 `logs`，不会执行 `pull`、版本 tag 注入或 `up -d` 版本切换。
- Manager action 白名单只包含 `start`、`stop`、`restart`；`update` / `rollback` 在 API 边界作为非法 action 拒绝，不创建 operation、不调用 runtime、不写 manager audit。
- 已删除 `DICEPP_MANAGER_DOCKER_VERSION_ENV`、`DICEPP_MANAGER_RELEASE_METADATA_ROOT`、Manager release metadata preview endpoint、compatibility gate、deployment gate、archive gate、post-action health 与 failure guidance 等自动版本操作后端链路。
- Release metadata 文档继续保留为 GitHub Release body / asset 和人工升级风险阅读材料，不进入 Docker 镜像，也不由 Manager 自动消费。
- GitHub Release 发布侧 metadata 输出已接入：`docs/releases/vX.Y.Z.md` 作为 Release body 和 release asset 提供；`docs/linux.md` 随 Linux offline zip 提供；release metadata 仍不进入 Docker 镜像。

已完成 Windows 单入口与手动升级边界：

- Windows 发布包提供 `DicePP.exe` 作为用户主入口，负责启动 Dashboard/托盘体验。
- `DicePP-Runtime.exe` 作为 runtime 进程入口，由 Manager `ProcessRuntime` 编排，不作为普通用户直接入口。
- `ProcessRuntime` 已支持 start/stop/restart、状态查询和 console log 捕获；Dashboard 运行监控页通过 Manager 展示与操作这些能力。
- Windows update/rollback 在 3.0.0 不由 Dashboard Manager 提供，不做自动 staging 替换、失败恢复或自动版本切换。
- Windows 手动升级路径依赖存档能力：升级前创建存档，退出旧版本，解压/复制新版发行包，启动 `DicePP.exe`，必要时通过 Dashboard 恢复既有存档。

3.0.0 不纳入，后续另行设计：

- Windows 自动 staging update/rollback、版本切换和失败恢复。
- Linux Docker Compose 自动 update/rollback、镜像 tag 注入、compatibility gate、archive gate、post-action health 和失败恢复。
- Manager runtime 自动联网抓取或消费 GitHub Release body / release asset。
- 自动同步真实目标 Release compose / deployment 文件。
- 更完整的跨版本健康检查、自动恢复与失败后交互式用户指引。

### 阶段 D：Dashboard Save Archives

目标：3.0.0 前提供 Dashboard 存档/恢复能力，支持人工升级前的存档验证与恢复准备。

范围：

- 创建、查看、删除、恢复存档。
- zip + manifest + sha256 checksum。
- `data/backups/` 默认存储。
- pre-restore 存档。
- 恢复失败处理。
- 升级/恢复前存档能力。
- 明确不包含 Dashboard DB、content、LLOneBot 数据。

已完成 Dashboard Save Archives 基础：

- 新增 Dashboard 本地存档创建与列表 API：`GET /api/archives`、`POST /api/archives`，复用 Dashboard session 鉴权。
- 存档默认写入 `data/backups/`，格式为 zip + `manifest.json`，manifest `format_version=1` 并记录创建时间、DicePP 版本、描述、scope 和每个 payload 文件的 sha256。
- 当前包含真实用户配置 `config/user.json`、真实 bot 配置 `config/bots/<bot_id>.json`、`data/dicepp.db`、`data/bots/**/bot_data.db`、`log.db`、`personas_data_*.db` 与 `data/local_images/` 普通文件；跳过 symlink、不存在路径和目录。
- 当前排除版本随附配置 `config/global.json`、`config/bots/_template.json`，以及 Dashboard DB、`content/`、`data/backups/`、`data/runtime/`、`data/bots/*/logs/` 及未在白名单中的 LLOneBot 数据。
- 新增存档详情与删除 API：`GET /api/archives/{filename}`、`DELETE /api/archives/{filename}`；只允许管理 `data/backups/` 下的普通 `.zip` 文件名，拒绝路径穿越、子目录、非 zip 与 symlink。
- 新增恢复前只读 verify 基础：`POST /api/archives/{filename}/verify` 校验 manifest 格式、payload sha256、缺失文件、危险 archive path 与额外未声明 payload。
- 新增只读恢复预览 API：`POST /api/archives/{filename}/restore-plan` 复用 verify 结果，将白名单 payload 映射为逻辑目标路径并报告 create/overwrite/blocked；API 名称保留 `restore-plan`，用户界面显示为“恢复预览”。
- 实际恢复 API 第一版已完成：`POST /api/archives/{filename}/restore` 要求显式确认，恢复前自动创建 pre-restore 存档，然后仅将已验证、计划允许的白名单 payload 写回目标路径。
- Manager 停写/启动编排 API 已完成：`POST /api/archives/{filename}/restore` 可在 `quiesce_runtime: true` 时先通过 Manager stop 已发现 bot，恢复后再 start 已 stop 成功的 bot，并在响应中返回编排诊断信息。
- Dashboard UI 恢复联动已完成：用户可查看“存档信息”、执行“检查存档”、打开“恢复预览”；详情、检查、恢复预览面板互斥。UI 默认只展示用户向汇总、问题和提醒，不默认展示 raw `arcname`、`target_path`、checksum、scope、pre-restore、`restored_entries` 或 `failed_entries` 等工程字段。
- Dashboard UI 恢复失败入口已完成：恢复失败时，可直接基于恢复前自动创建的备份存档继续查看恢复预览，无需手动回列表查找。
- Manager quiesce 的 Dashboard UI 开关接入已完成：恢复确认区默认开启“恢复前暂停 Bot（推荐）”，默认发送 `quiesce_runtime: true`；用户取消勾选时才走 direct restore。
- Dashboard 自动 update/rollback 存档门禁、版本操作面板、post-action health 与失败指引已移除；升级前存档改为人工流程，由用户在更新或回滚前主动创建并验证。

3.0.0 不纳入，后续另行设计：

- 在线一致性快照和更完整的一致性策略。当前 3.0.0 只承诺普通存档、恢复前 pre-restore、默认 Manager 停写、人工升级前存档提醒/验证流程和恢复失败入口。

## 参考

- SQLite `user_version` / `application_id`: https://sqlite.org/pragma.html
- SQLite Online Backup API: https://sqlite.org/backup.html
- Django squashed migrations: https://docs.djangoproject.com/en/6.0/topics/migrations/
- Flyway baseline migrations: https://documentation.red-gate.com/fd/baseline-migrations-273973336.html
- Alembic SQLite batch mode: https://alembic.sqlalchemy.org/en/latest/batch.html
- Gitea backup and restore: https://docs.gitea.com/administration/backup-and-restore
- Immich backup and restore: https://docs.immich.app/administration/backup-and-restore/
- Home Assistant backups: https://www.home-assistant.io/common-tasks/general/
