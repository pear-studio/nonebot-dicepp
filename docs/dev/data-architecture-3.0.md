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

## DB Migration Model

迁移是 forward-only。生产 downgrade 不作为标准能力；数据回退依赖存档/备份。没有备份时属于 emergency repair，不进入常规 migration contract。

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

新 DB 不从 v1 跑完整迁移链。新 DB 直接创建 latest schema，并记录当前版本。已有 DB 走 forward migration：

```text
current_version == 0:
  create_latest_schema()
  set current_version = latest

current_version > 0:
  require current_version >= min_supported_schema_version
  run migrations current+1 ... latest
```

最小元数据：

```sql
CREATE TABLE IF NOT EXISTS schema_metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TEXT NOT NULL
);
```

先不做 checksum、dependency graph、down migration、复杂 baseline generation。

所有 migration 必须 retry-safe。简单 migration 推荐 idempotent，但不强制所有 migration 完全幂等。

migration 不应依赖当前业务模型或 Repository。允许依赖 aiosqlite、标准库、migration helper，以及 migration 文件内定义的旧格式快照。用 import whitelist 测试防止历史迁移引用当前业务层。

测试规则：

- fresh DB -> latest schema。
- supported legacy fixture -> latest。
- already latest -> noop。
- fresh latest schema 与 migrated schema 等价。
- 数据搬迁 migration 必须有 legacy fixture。
- legacy fixture 生命周期绑定 `min_supported_schema_version`；低于支持窗口后可删除对应测试。

baseline/squash 第一版只写规则，不实现复杂 squash。未来需要压缩时，baseline 只服务新环境，旧环境仍按支持窗口内的 migration chain 升级。

## DicePPDatabase 与本地控制凭据

新增 `DicePPDatabase`，文件为 `data/dicepp.db`。

第一批真实用途是 local control token。旧 `data/runtime/local-control.token` 只作为一次性迁移输入：

```text
if old token file exists:
  read token
  insert into data/dicepp.db
  delete old token file
```

不保留长期双来源兼容。Bot 和 Dashboard 必须同时切到新来源。

token 建专表，不使用通用 KV：

```sql
CREATE TABLE local_control_token (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  token TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

Dashboard 不直接获得通用 `DicePPDatabase` 访问能力。短期提供 narrow shared accessor，例如 `core/security/local_control_token.py`：

- Bot 调用 ensure。
- Dashboard 调用 read。
- Dashboard 不访问其他 instance tables。

Manager 落地后应以受控配对/授权流程替代 Dashboard 共享读取本地 token store。该 TODO 写入代码注释或设计文档，不为 RC 阶段临时兼容单独开 backlog。

## Manager 3.0 范围

3.0.0 需要本地 Manager 完整，DiceHub 远程控制保留 TODO。

纳入：

- Manager 常驻层。
- Dashboard 通过 Manager 管理 Bot/Dashboard 生命周期。
- Manager Core：操作状态机、鉴权、审计、健康检查、并发控制、版本兼容、失败恢复。
- Runtime Backend：Linux `DockerRuntime`、Windows `ProcessRuntime`，共享 contract。
- 本地启停、重启、更新、版本切换、回滚和存档恢复所需能力。

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

- `config/`
- `data/dicepp.db`
- `data/bots/**/bot_data.db`
- `data/bots/**/log.db`
- `data/bots/**/personas_data_*.db`
- `data/local_images/`

不包含：

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
    "included": ["config", "data/dicepp.db", "data/bots", "data/local_images"],
    "excluded": ["dashboard/data/dashboard.db", "content", "data/backups", "data/runtime", "data/bots/*/logs"]
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
- 提供恢复到 pre-restore 存档的入口。
- 不做复杂自动多次回滚。

跨版本规则：

- 允许从旧版本存档恢复到新版本程序，恢复后自动 forward migration。
- 不承诺 Dashboard 3.0.0 完整编排“程序回退到旧版本 + 恢复旧存档”。
- 存档 manifest 记录 DicePP 版本和 schema 信息，为未来回退路径保留证据。

release metadata 标记 `数据变更: yes` 或 `配置变更: yes` 时，升级流程必须强提示或门禁创建存档。存档功能本身仍可独立使用。

## 实施阶段

### 阶段 A：Data Foundation

目标：3.0.0 前稳定数据状态边界和迁移基础。

范围：

- `data/dicepp.db` 与 `DicePPDatabase`。
- local control token 入库，旧文件一次性导入后删除。
- config canonical rewrite。
- SchemaTarget。
- fresh DB create latest schema。
- existing DB forward-only migration。
- `schema_metadata` + `schema_migrations`。
- `bot_core`、`bot_log`、`instance`、`persona` targets。
- migration import whitelist、schema equivalence、legacy fixture 生命周期规则。

### 阶段 B：Manager Foundation

目标：3.0.0 前建立本地 Manager 作为 Dashboard 与运行时之间的最小权限管理层。

范围：

- Manager Core。
- 本地 API 与鉴权。
- 操作状态机、审计、健康检查、并发控制。
- Linux DockerRuntime 与 Windows ProcessRuntime contract。
- Bot/Dashboard 接入 Manager。
- DiceHub 远程控制仅保留 TODO。

### 阶段 C：Local Update And Rollback

目标：3.0.0 前让 Dashboard 通过 Manager 完成本地生命周期、更新和失败恢复。

范围：

- 启停、重启、状态、日志。
- Windows staging 更新与失败恢复。
- Linux 镜像 tag 更新与回滚。
- 版本兼容检查。
- Dashboard 不直接访问 Docker socket 或替换进程/文件。

### 阶段 D：Dashboard Save Archives

目标：3.0.0 前提供 Dashboard 存档/恢复能力，并与升级风险门禁联动。

范围：

- 创建、查看、删除、恢复存档。
- zip + manifest + sha256 checksum。
- `data/backups/` 默认存储。
- pre-restore 存档。
- 恢复失败处理。
- 升级前存档门禁。
- 明确不包含 Dashboard DB、content、LLOneBot 数据。

## 参考

- SQLite `user_version` / `application_id`: https://sqlite.org/pragma.html
- SQLite Online Backup API: https://sqlite.org/backup.html
- Django squashed migrations: https://docs.djangoproject.com/en/6.0/topics/migrations/
- Flyway baseline migrations: https://documentation.red-gate.com/fd/baseline-migrations-273973336.html
- Alembic SQLite batch mode: https://alembic.sqlalchemy.org/en/latest/batch.html
- Gitea backup and restore: https://docs.gitea.com/administration/backup-and-restore
- Immich backup and restore: https://docs.immich.app/administration/backup-and-restore/
- Home Assistant backups: https://www.home-assistant.io/common-tasks/general/
