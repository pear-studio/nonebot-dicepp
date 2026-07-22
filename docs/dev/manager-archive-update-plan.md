# Manager、归档恢复与自动升级实施计划

> 状态：执行中。五批严格串行推进，每批均执行“实现、独立审查、主代理验收、测试、提交”的闭环。

## 1. 执行规则

- 五批全部属于当前 feature 分支目标，不将第四、第五批移入 backlog。
- 上一批未满足完成门槛时，不开始下一批。
- 每批由实现 subagent 编码，由独立 review subagent 检查质量、兼容性和风险。
- 主代理对照架构文档验收，处理 review 结论并运行与风险相称的测试后提交。
- 非阻断风险和新发现记录在本文“风险记录”中，优先采用不扩大范围的兼容方案继续推进。
- 用户文档与对应能力同批完成，不把文档集中拖到最后。

## 2. 总体状态

| 批次 | 状态 | 完成提交 |
|---|---|---|
| 1. 统一实例数据基础 | 已完成 | 本批实现提交 |
| 2. 常驻 Manager 标准化 | 待开始 | - |
| 3. 事务化归档与精确恢复 | 待开始 | - |
| 4. 版本发现与下载 | 待开始 | - |
| 5. 确认安装与自动回退 | 待开始 | - |

## 3. 第一批：统一实例数据基础

### 目标

建立跨 Bot、Dashboard、Manager 共用的数据布局和持久化资产事实来源，在不改变现有用户行为的前提下消除路径重复。

### 主要工作

- 引入无 NoneBot 启动副作用的共享 `InstanceLayout`。
- 迁移 Bot `Paths` 与 Dashboard `DashboardPaths` 的实例路径解析。
- 建立 `DataAsset` 与内置 Catalog，复用现有 `SchemaTarget`。
- 用动态路径模板统一 bot core、log、Persona、配置和本地图片的定位与扫描。
- 让实际数据库和内容访问开始消费 Catalog，移除对应持久化路径字面量。
- 自动生成 Catalog 摘要和可序列化描述。
- 将实例 `content/` 标记为用户资产；把默认 Persona 示例变为只读模板资源，不自动写入实例。
- 补充配置和数据目录开发文档。

### 完成门槛

- Bot 与 Dashboard 在相同实例根目录下解析出相同路径。
- 当前支持的所有归档数据均能由 Catalog 枚举。
- schema 迁移仍由现有 `SchemaTarget` 驱动。
- 关键存储代码不再独立拼接已纳入 Catalog 的文件名。
- 现有配置、数据库和 Dashboard 测试通过。

## 4. 第二批：常驻 Manager 标准化

### 目标

让 Manager 成为 Windows/Linux 标准部署组件，并把运行控制收敛为 RuntimeUnit 模型。

### 主要工作

- 将 Manager 提取为独立常驻服务，建立内部 API、鉴权和持久化 operation store。
- 建立实例级排他维护锁和可恢复的 operation 状态机基础。
- Runtime Backend 从 `bot_id` 生命周期迁移为 RuntimeUnit 生命周期。
- Dashboard 改为 Manager 客户端并支持 operation 断线重连。
- Linux Compose 增加 Manager 服务、内部网络、共享挂载、状态卷和 Docker Socket。
- Linux Docker Adapter 通过 DicePP 标签和固定操作集合控制目标容器。
- Windows 启动器改为托盘 Manager，负责启动 Bot 和 Dashboard。
- Windows 增加默认关闭的登录自启动开关。
- 增加部署 schema 与 Manager/API 兼容信息。
- 更新 Linux、Windows 部署文档。

### 完成门槛

- 默认 Linux Compose 启动后 Manager 可控制共享 Bot RuntimeUnit。
- Windows Manager 可以启动、停止并监控 Bot/Dashboard 子进程。
- 多个 Bot 账号共享进程时，Dashboard 不再宣称支持逐账号启停。
- Dashboard 不持有 Docker Socket。
- Manager 重启后仍能读取既有 operation 记录。
- 无 Manager 部署被明确识别为不支持状态，不保留长期直写降级路径。

## 5. 第三批：事务化归档与精确恢复

### 目标

把归档创建与恢复全部移入 Manager，完成一致快照、精确恢复、自动补偿和跨平台迁移。

### 主要工作

- 实现常规/完整两个 archive profile。
- 创建时暂停 RuntimeUnit，直接流式写 `.zip.inprogress`，校验后原子发布。
- manifest 记录 DataAsset、Catalog、schema、profile、平台和文件摘要。
- 恢复预览报告新增、覆盖和移除。
- pre-restore 必须采用目标归档相同 profile，并在修改前完成验证。
- 实现逐文件安全替换、精确删除、forward migration 和自动完整回退。
- 用持久化 journal 支持 Manager/主机中断后的确定恢复。
- 实现硬/软健康检查边界。
- 支持当前 format v1 归档的保守读取和恢复。
- 实现系统归档保留 5 份、手动归档不自动删除。
- 实现归档敏感信息标记。
- 在本批末尾完成浏览器导入/导出；导入只保存和校验，不自动恢复。
- 更新归档、恢复和跨平台迁移文档。

### 完成门槛

- 归档创建期间 Bot 不写入受管数据。
- 常规恢复不会触碰 `content/`，完整恢复对 `content/` 精确同步。
- 任一受控失败点都能自动恢复 pre-restore 并重新达到本地健康状态。
- 主机或 Manager 在事务关键阶段退出后能够恢复或回退。
- 新 schema 归档不能恢复到不支持的旧程序。
- Windows/Linux 归档可以互相导入、预览和恢复。
- Dashboard 已无归档文件直写实现。

## 6. 第四批：版本发现与下载

### 目标

建立 GitHub Release 契约、稳定/预发布频道和平台差异化下载，但不在用户确认前安装。

### 主要工作

- 定义并生成 `dicepp-release.json`。
- Release Contract 包含平台、架构、摘要、Catalog、部署 schema 和最低 Manager 版本。
- 在统一 DicePP 配置加入：默认开启发现、默认关闭自动下载、默认 stable 频道。
- Dashboard 提供版本、频道、变更范围和兼容性展示。
- Manager 实现定时发现、手动检查、可选自动下载、断点/失败清理和摘要验证。
- Linux 默认下载 GitHub Release 镜像包，GHCR pull 作为备用。
- Windows 生成规范命名的 Portable、Setup、Velopack package 和 feed。
- Linux 包移除 `offline` 命名并携带 Compose、镜像和校验元数据。
- 下载缓存按版本管理，默认保留最近 2 个可回退版本。
- 更新配置与发布产物文档。

### 完成门槛

- 默认只发现 stable，用户启用后才发现 RC/预发布。
- 自动下载关闭时不会获取大型 artifact。
- 所有下载在进入可安装状态前完成摘要和契约校验。
- Linux/Windows 只选择与当前平台和架构匹配的 artifact。
- 发现与下载不会改变当前运行版本。

## 7. 第五批：确认安装与自动回退

### 目标

在用户确认后完成 Windows/Linux 兼容版本安装，并在硬性健康失败时自动恢复程序与数据。

### 主要工作

- 建立 Upgrade Coordinator，复用第三批的维护锁、归档、journal 和健康检查。
- Linux 实现 Release 镜像加载、Bot/Dashboard 切换、migration、提交和完整回退。
- Linux 拒绝自动安装要求 Manager 自升级或 Compose 拓扑变化的 Release。
- Windows 集成 Velopack Portable/Setup 和版本 feed。
- Windows 增加版本目录外的 UpdateGuard 与健康标记协议。
- Windows 回退同时执行 Velopack 降级和 pre-upgrade 数据恢复。
- Dashboard 展示确认、进度、断线重连、失败原因和最终回退结果。
- 完成发布构建、安装、升级和故障恢复文档。

### 完成门槛

- 未经用户确认不会安装新版本。
- 升级前归档或旧程序保留失败时拒绝开始。
- Linux 兼容版本可以从 GitHub Release 包完成安装，不依赖成功访问 GHCR。
- Windows Portable 与 Setup 都可完成后续自更新。
- 在程序切换、migration、进程启动和健康检查故障下注入失败，均能恢复旧程序和旧数据。
- 外部服务故障不会误触发程序回退。
- 五批对应用户文档和运维说明完整。

## 8. 风险记录

| 编号 | 状态 | 风险 | 当前处理 |
|---|---|---|---|
| K1 | 已接受 | 完整归档可能包含 1 GB 级查询库并造成较长停机 | 用户接受停机；创建前展示大小和空间，直接流式写 ZIP |
| K2 | 已接受 | 归档明文包含 API Key | 保持完整恢复能力，界面明确敏感标记，不做自动云上传 |
| K3 | 已接受 | Linux Manager 挂载 Docker Socket | 内部网络、固定操作、标签过滤和审计，Dashboard 不接触 Socket |
| K4 | 已接受 | 第一阶段不能自动升级 Manager 或 Compose 拓扑 | Release Contract 阻止自动安装并给出手动迁移指引 |
| K5 | 待实施验证 | Windows Velopack 与 UpdateGuard 的进程切换边界 | 第五批以故障注入和真实打包烟测验证 |
