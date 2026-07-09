---
name: version-deploy
description: 在生产环境部署或回退 DicePP 已发布版本。当用户要求部署、上线、更新代码、切换版本、回退、rollback、pull 镜像或应用 release 时使用；消费开发环境 version-release 创建的 vX.Y.Z release。
license: MIT
metadata:
  author: DicePP
  version: "1.0"
---

# Version Deploy

在生产环境部署或回退 DicePP 已发布版本。该技能只处理版本变更决策和确认流程；具体 Linux Docker/Compose 操作遵守 'deploy-docker'。

## 适用场景

- 用户要求在生产环境部署、上线、更新代码、切换版本、回退或 rollback。
- 用户要求 pull 新镜像、使用离线镜像包、应用某个 release、把生产切到 'vX.Y.Z'。
- 用户要求确认当前生产版本、目标版本、release metadata 或部署风险。

## 核心约定

- 生产部署/回退以 'vX.Y.Z' release 为单位。
- 目标 release 必须由开发环境 'version-release' 创建。
- 生产使用 GHCR 镜像: 'ghcr.io/pear-studio/nonebot-dicepp:vX.Y.Z'；包含 Dashboard 的版本还会使用 'ghcr.io/pear-studio/dicepp-dashboard:vX.Y.Z'。
- 如果生产环境无法访问 GHCR, 可使用目标 Release asset `DicePP-vX.Y.Z-linux-amd64-offline.zip` 作为离线输入；离线路径仍必须使用相同镜像 tag 和同一份目标 `docker-compose.yml`。
- 生产更新风险摘要的唯一源头是 GitHub Release body, 由开发环境 'docs/releases/vX.Y.Z.md' 生成, 仅用于人工部署/回退风险核对。
- 'DICEPP_IMAGE_TAG' 通过命令环境变量传递, 不写入任何配置文件。
- 默认只读。修改运行配置、pull/load 镜像、重启/更新容器等写操作必须先展示影响、命令和回滚方式, 等待用户明确确认。

## Preconditions

- 只在生产环境中使用。
- 当前环境必须遵守生产规则: 默认只读, 敏感值不输出。
- 生产部署方式为 Linux Docker/Compose 时, 具体命令必须遵守 'deploy-docker'。
- 如果无法读取 release metadata, 将风险视为 'unknown', 更新前要求用户明确确认风险和备份状态。

## Read-only Audit

执行任何写操作前, 先完成只读审计：

1. 确认目标版本

   - 目标必须形如 'vX.Y.Z'。
   - 禁止默认部署分支 HEAD。
   - commit SHA 只允许作为用户明确要求的临时例外, 并必须标记为不可标准回退路径。

2. 读取当前生产目标镜像

   - 通过 'docker ps' 或 'docker inspect' 确认当前运行镜像。
   - 不输出 secrets。

3. 读取目标 release metadata

   按优先级尝试：

   a. 'gh release view vX.Y.Z --json body' (GitHub Releases API)
   b. 'git show vX.Y.Z:docs/releases/vX.Y.Z.md' (如本地有仓库)

   如果两种方式都不可用, 将风险视为 'unknown', 要求用户明确确认。

   release metadata / release body 只作为人工部署或回退前的风险阅读材料, 不写入本地同步目录, 不驱动自动部署流程。

4. 读取目标部署说明与 compose

   目标版本可能改变 Docker Compose 拓扑（新增 service、环境变量、volume 或端口）。在展示部署计划前, 必须尽量读取目标版本的部署说明和 compose：

   a. 如生产目录包含本仓库 checkout, 可先执行只用于读取 release 文件的 `git fetch --tags --prune origin`；这不是生产部署方式, 不允许据此部署分支 HEAD。
   b. 优先读取 `git show vX.Y.Z:docs/linux.md` 和 `git show vX.Y.Z:docker-compose.yml`。
   c. 如果本地没有仓库, 通过 GitHub Release asset、Linux offline zip 内置文档或远端 tag 内容读取 `docker-compose.yml`；能读取 `docs/linux.md` 时也必须读取。
   d. 读取失败时, 在风险摘要中明确标记“部署说明/compose 未确认”, 并要求用户确认是否继续。

5. 核对目标镜像

   从 metadata 读取 image, 应为：

       ghcr.io/pear-studio/nonebot-dicepp:vX.Y.Z

   如果目标 compose 包含 Dashboard, 还应核对 Dashboard 镜像为：

       ghcr.io/pear-studio/dicepp-dashboard:vX.Y.Z

   若 metadata、目标 compose 与目标版本不一致, 停止并要求澄清。

6. 对比生产 compose

   读取当前生产 `docker-compose.yml` 与目标版本 compose 的服务拓扑。若目标版本新增或改变标准部署块, 必须在计划中说明如何同步, 例如：

   - 新增独立 `dashboard` service；
   - 给 `bot` 增加 `DPP_ADMIN_HOST` / `DPP_ADMIN_PORT`；
   - 新增 `dashboard/data` 持久化目录；
   - 新增或改变端口映射、volume、环境变量。

   原则：

   - 不手工发明部署架构；以目标 release 的 `docker-compose.yml` 和 `docs/linux.md` 为准。
   - 如果生产 compose 没有本地定制, 可计划用目标 release 的 `docker-compose.yml` 替换。
   - 如果生产 compose 有本地定制, 只计划合并目标 release 的标准块；无法安全合并时停止并让用户裁决。

7. 汇总生产更新风险

   必须展示：

   - 当前运行镜像
   - 目标版本
   - 目标镜像（bot；如适用也包括 dashboard）
   - 当前 compose 服务列表
   - 目标 compose 服务列表
   - 是否需要同步 `docker-compose.yml`
   - '数据变更' (yes/no)
   - '配置变更' (yes/no)
   - metadata 中的 Risk Notes 摘要

8. 备份判断

   - 如果 `数据变更: yes` 或 `配置变更: yes`, 更新前必须确认已完成升级前备份。
   - 如果 metadata 缺失或无法解析, 必须要求用户明确确认备份状态或接受风险。
   - 镜像回退不等于数据回退；涉及数据/配置变更时, 需要按备份/恢复流程处理。

9. 展示计划

   在用户确认前只展示将执行的改动, 包括：

   - 如需同步 compose, 展示 compose 更新来源、影响的 service 和回滚方式。
   - 在线路径: 将注入环境变量 'DICEPP_IMAGE_TAG=vX.Y.Z' 并调用 'deploy-docker' 执行 pull/up/健康检查。
   - 离线路径: 先校验并解压 `DicePP-vX.Y.Z-linux-amd64-offline.zip`, 再按包内说明校验并导入镜像, 注入 'DICEPP_IMAGE_TAG=vX.Y.Z' 并调用 'deploy-docker' 执行 `up --pull never`/健康检查。不得在离线路径中执行 `docker compose pull`。
   - 如目标部署说明要求首次初始化 Dashboard, 展示需在 `dashboard` service 内执行的初始化命令, 但只有用户确认后才可执行。
   - 如需回退, 回滚方式是重新执行本技能并指定另一个已发布的 'vX.Y.Z'。

## Confirmed Execution

只有当用户明确确认部署或回退目标版本后, 才允许执行：

1. 如计划包含 compose 同步, 先按用户确认的方式同步 `docker-compose.yml`，并保留回滚路径。
2. 如果使用离线镜像包, 按用户确认的路径解压，校验包内 `checksums.sha256` 并 `docker load`；确认输出包含 bot/dashboard 两个目标镜像。
3. 在命令中注入环境变量 'DICEPP_IMAGE_TAG=vX.Y.Z'。
4. 按 'deploy-docker' 执行项目 Docker Compose 更新；在线路径使用 pull/up, 离线路径使用 up --pull never。

确认语句应包含目标版本, 例如：

- 确认部署 v3.0.1
- 确认回退到 v3.0.0

## Important Notes

- 不执行 'git pull master' 作为生产部署方式。
- 不基于模糊的“最新代码”更新生产；必须明确版本。
- 允许为了读取目标 release 文档执行 `git fetch --tags`, 但不得把本地分支 HEAD 当作部署依据。
- 不自动操作数据库、恢复备份或删除数据；这些必须由专门运维流程处理。
- 不输出 secrets、token 或敏感配置。
