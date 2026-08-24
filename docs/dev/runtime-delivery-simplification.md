# 运行与交付简化方案

- 状态：已确认，待实施；实现完成后删除本方案文档
- 日期：2026-08-24
- 影响范围：Windows Portable、Linux Docker、Dashboard、Bot Runtime、数据迁移与发布流水线

## 背景

当前标准部署引入了独立 Manager，统一负责 Windows 子进程和 Linux 容器的生命周期、配置写入、归档恢复、版本发现、自动安装、跨进程交接和失败回滚。为了让 Manager 能升级自身并在不同平台恢复中断事务，项目又建立了持久 operation、journal、控制通道、Linux handoff、Windows Velopack 恢复材料、候选证据和跨版本真实矩阵。

这套实现已经基本跑通，但它让少量发布便利承担了过高的长期维护成本。项目的首要目标是让普通用户容易部署 DicePP，而不是提供无人值守更新平台。维护者接受部分功能退化，并明确选择简单、可解释、由用户参与的失败处理。

Bot 与 Dashboard 属于同一个 DicePP 产品版本，不存在独立升级或维护两套版本兼容矩阵的需求。

## 决策

### 1. 产品运行形态只保留 Bot 与 Dashboard

删除 Standalone Manager，包括其 4091 监听端口、HTTP Interface、内部 token、持久 operation store、RuntimeUnit 抽象、Docker Socket 挂载以及 Bot↔Manager 控制通道。

仍有价值的实现不整体搬迁或改名为 Manager，而是按职责收敛到更小的 Module：

- Data Catalog、SQLite 读取、路径安全和 schema migration 保留在共享数据 Module；
- Windows 子进程控制保留在 Dashboard launcher；
- Bot 心跳、状态和配置重载直接连接 Dashboard；
- 配置、存档、清空和导入由 Dashboard 直接调用本地实现。

### 2. Bot 与 Dashboard 始终同版本交付

一个 Git tag 只代表一个 DicePP 版本。Bot 与 Dashboard 必须来自同一次构建，不支持不同版本组合，也不建立二者之间的兼容矩阵。

Windows Portable 可以包含 `DicePP.exe` 与 `DicePP-Runtime.exe` 两个可执行文件，但它们属于同一发布产物、使用同一版本。Linux 只发布一个 DicePP 镜像。

### 3. Windows 只发布 Portable ZIP

Windows 用户下载新版 Portable ZIP，解压到新的空目录并启动。旧目录不允许被新版覆盖解压。

`DicePP.exe` 承载 Dashboard、托盘和登录自启动，并直接启动、停止、重启 `DicePP-Runtime.exe`。删除 Windows Setup、Velopack、nupkg、程序自动换位和恢复脚本。

### 4. Linux 保留 Docker，但合并为一个 DicePP 容器

Linux 继续提供官方 Docker 镜像和 Compose。Bot 与 Dashboard 运行在同一个 DicePP 容器中：Dashboard 是容器主进程，并管理 Bot 子进程。

Dashboard 继续提供 Bot 启动、停止、重启、状态和运行日志。Bot 日志来自子进程 stdout/stderr 或文件日志，不读取 Docker daemon 的原生日志。容器级日志由用户执行 `docker compose logs` 查看。

Dashboard 不挂载 Docker Socket。Compose 只管理整个 DicePP 容器的启动、停止和手工版本更新。

### 5. 取消程序自动更新

删除版本在线发现、频道选择、自动下载、自动安装、自动回滚、Linux Manager handoff、Windows Velopack 更新、升级协议 registry、升级证据和跨版本程序升级矩阵。

Dashboard 只显示当前版本和指向 GitHub Releases 的静态下载链接，不联网判断最新版本。

Linux 用户通过明确镜像版本执行 `docker compose pull` 与 `docker compose up -d`。Windows 用户手工下载并解压新版 Portable。

### 6. 迁移和恢复只允许导入空业务实例

新版支持从已停止、属于明确受支持版本和布局的旧实例目录导入数据，也支持从用户已有存档导入。导入目标必须没有 Catalog 管理的业务配置、数据和用户内容。

导入只处理明确受支持的 `config/`、`data/` 与 `content/` 资产，并执行已有 schema forward migration。不迁移旧 Manager token、journal、operation、包缓存和恢复材料，也不迁移 NapCat、LLOneBot 或其他插件数据。

不支持向非空实例合并、覆盖或恢复数据，不承诺猜测未知历史布局。具体支持的历史版本范围由实现和测试清单明确声明。

### 7. 提供显式的“清空业务数据”操作

清空按 Data Catalog 精确删除业务配置、数据库、本地图片和用户内容。Dashboard 管理员状态、用户已有存档、日志和程序文件不在清空范围内。

清空前不强制创建安全存档，不自动保护或延长存档保留期。存档由用户主动创建、下载、保留和删除。

清空和导入只要求 Bot 已停止，并保留一个最小启动门，防止 Bot 在操作未完成时启动。失败时直接报告错误；用户重新清空、重新导入。系统不自动备份、回滚、恢复原运行状态或执行终态裁决。

### 8. 发布流水线退化为普通构建发布

目标流水线只需：

1. 运行普通测试；
2. 构建 Windows Portable ZIP；
3. 构建并推送单个 Linux DicePP 镜像；
4. 验证全新启动；
5. 使用明确支持的旧版本 fixture 验证空实例导入；
6. 发布 GitHub Release、Compose 和必要校验值。

不再建立候选 receipt、promotion、upgrade evidence 或程序升级组合矩阵。

## 接受的后果

- 用户必须手工发现、下载和安装新版；安全更新采用率可能下降。
- Windows 不再提供安装器、卸载项和 Velopack 更新入口。
- Linux Bot 与 Dashboard 不再具有容器级故障隔离、独立资源限制或独立升级能力；整个容器 OOM 时二者会一起退出。
- Dashboard 展示 Bot 子进程日志，而不是 Docker daemon 原始日志。
- 不支持非空恢复、自动回滚、断电后自动续作或无人值守恢复。
- 导入失败后需要用户清空并重试；存档选择和保留由用户负责。
- 不保证任意历史版本都能直接迁移，只维护明确声明的版本范围和数据 forward migration。

作为交换，运行拓扑、跨进程 Interface、发布资产和测试矩阵都会显著缩小。维护知识集中在 Dashboard launcher、共享数据 Module 和 Bot↔Dashboard 心跳三个位置，提高 Locality，并减少为低概率失败维护状态机的成本。

## 明确不做

- 不把旧 Manager 改名为 privileged runtime controller；
- 不把 Docker Socket 挂入 Dashboard；
- 不恢复 release channel、下载缓存或自动安装；
- 不发布 Windows Setup、Velopack bundle 或 Linux 自动升级离线包；
- 不做非空 restore plan、pre-restore 安全存档、存档自动保护、自动 rollback 或健康裁决；
- 不支持覆盖安装旧程序目录；
- 不猜测未知旧目录、损坏数据库或缺失迁移链；
- 不迁移 Manager 状态、NapCat、LLOneBot 或其他插件数据。

## 曾考虑但未采用

### 保留瘦 Manager

它可以继续隔离 Docker Socket并保留三容器生命周期控制，但仍需要第三个容器、私有鉴权、跨进程调用和错误处理。既然 Linux 改为单容器子进程模型，这些成本不再产生足够 Leverage。

### Dashboard 直接挂载 Docker Socket

实现代码较少，但 Dashboard 是对外的 Web 入口。一旦被攻破，原始 Docker Socket 基本等同宿主机 root 权限，因此不作为官方部署方式。

### Bot 与 Dashboard 分别发布和运行

它保留容器级资源与故障隔离，却重新引入两套镜像、版本一致性和生命周期控制。项目没有独立升级二者的产品需求，因此不承担这项成本。

### 保留自动升级但冻结协议

即使冻结协议，Manager 自身替换、数据迁移、平台恢复和真实矩阵仍会形成持续维护负担。手工换版配合空实例导入更符合当前团队能力。

## 重新评估条件

只有出现下列实质需求时，才重新讨论本决策：

- 大量远程实例需要无人值守管理，或出现明确的安全更新时限；
- Linux 必须恢复 Bot 与 Dashboard 的独立资源限制或容器级故障隔离；
- 一个 Dashboard 需要控制多个 Runtime 或多台主机；
- 用户数据价值要求明确的 RPO/RTO、非空恢复、在线快照或断电自动恢复；
- 空实例清空重试产生了不可接受的实际支持成本；
- Dashboard 的权限模型变化，使其不再适合直接写入配置和业务数据。

满足条件只表示需要重新分析，不表示恢复旧 Manager；新的需求必须重新评估并记录新的方案。

## 与当前实现文档的关系

本方案描述已确认但尚未实施的目标架构。[Manager、归档恢复与升级架构](./manager-architecture.md) 在迁移完成前仍描述当前代码事实；其中 Standalone Manager、三服务 Compose、非空归档恢复和自动升级相关章节将在实施本方案时被删除或重写。
