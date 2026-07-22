# 实例布局与数据资产目录

本文记录第一批架构调整落地后的开发约束。总体目标和后续事务语义见 [Manager、归档恢复与自动升级架构](manager-archive-update-architecture.md)。

## 单一实例布局

`src/dicepp_data/` 是 Bot、Dashboard 和未来 Manager 共用的无运行时副作用包，只依赖 Python 标准库。`InstanceLayout` 统一解析：

- `config/`：实例配置；
- `data/`：数据库、本地图片和运行状态；
- `content/`：用户自己维护的内容；
- Dashboard 本地状态、日志和归档目录。

`core.config.basic.Paths` 与 `dashboard.src.config.DashboardPaths` 暂时保留为兼容门面，新代码应优先接收或获取 `InstanceLayout`，不要再建立第三套路径类。`DICEPP_DATA_DIR` 仍受支持，并由 Bot 与 Dashboard 一致解释。

## DataAsset Catalog

`DATA_CATALOG` 是受管理持久化文件的事实来源。每个 `DataAsset` 声明：

- 稳定 ID；
- 所属逻辑区域和路径模板；
- 文件、文件集、目录或 SQLite 类型；
- 所属常规/完整归档 profile；
- SQLite 对应的 schema 名称和最新版本；
- 动态路径参数的安全解析与反向匹配；
- 资产专属的恢复授权根（如 `config/bots`、`data/bots`、`data/local_images`）；
- 精确恢复策略。

运行时代码使用具体资产的 `resolve()` 获取动态路径；需要发现动态资产时使用带参数约束的 `iter_matches()`，不得重新拼接 glob。归档通过 Catalog 的 `collect()` 枚举文件，并通过资产声明的 restore scope 解析恢复目标，不把整个 `config/` 或 `data/` 默认视为同一个写入授权根。Catalog 的稳定 JSON 描述和 SHA-256 摘要由定义自动生成，不维护手写 `archive_contract_version`。

当前资产包括用户配置、Bot 配置、实例数据库、Bot core/log 数据库、Persona 数据库、本地图片和用户 `content/`。常规 profile 不含 `content/`，完整 profile 包含。

## Schema 生命周期

`DataAsset` 不替代 `SchemaTarget`。Catalog 中的 `SchemaReference` 只记录可跨进程序列化的 schema 身份；现有 `SchemaTarget` 继续执行建表和 forward migration，并从相同的 `SchemaReference` 获取名称和最新版本。修改 schema 版本时必须同时更新迁移实现和对应的共享引用，相关测试会验证两者一致性。

## 模板与用户内容

- `content/` 是用户资产，Bot 只从这里读取内容。
- `templates/characters/default/` 是随 Release 发布的只读模板。
- 启动和升级不得把模板自动复制、合并或覆盖到 `content/`。
- 模板目前只为未来 Dashboard 显式“新建角色”保留。

## 新增持久化数据

新增 DicePP 管理的持久化文件时：

1. 在 `dicepp_data` 中增加或扩展 `DataAsset`；
2. 业务代码通过资产解析路径，不直接拼接文件名；
3. SQLite 数据继续提供 `SchemaTarget`，并与资产 schema 引用保持一致；
4. 为 profile 枚举、路径安全和摘要稳定性补充行为测试。

其他 NoneBot 插件及 NapCat/LLOneBot 数据不属于本 Catalog。
