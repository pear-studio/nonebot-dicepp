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
- 用户要求 pull 新镜像、应用某个 release、把生产切到 'vX.Y.Z'。
- 用户要求确认当前生产版本、目标版本、release metadata 或部署风险。

## 核心约定

- 生产部署/回退以 'vX.Y.Z' release 为单位。
- 目标 release 必须由开发环境 'version-release' 创建。
- 生产使用 GHCR 镜像: 'ghcr.io/pear-studio/nonebot-dicepp:vX.Y.Z'。
- 生产更新风险摘要的唯一源头是 GitHub Release body, 由开发环境 'docs/releases/vX.Y.Z.md' 生成并同步。
- 'DICEPP_IMAGE_TAG' 通过环境变量传递, 不写入 '.env' 或任何配置文件。
- 默认只读。修改 '.env'、pull 镜像、重启/更新容器等写操作必须先展示影响、命令和回滚方式, 等待用户明确确认。

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
   - 不整段打印 '.env', 不输出 secrets。

3. 读取目标 release metadata

   按优先级尝试：

   a. 'gh release view vX.Y.Z --json body' (GitHub Releases API)
   b. 'git show vX.Y.Z:docs/releases/vX.Y.Z.md' (如本地有仓库)

   如果两种方式都不可用, 将风险视为 'unknown', 要求用户明确确认。

4. 核对目标镜像

   从 metadata 读取 image, 应为：

       ghcr.io/pear-studio/nonebot-dicepp:vX.Y.Z

   若 metadata 与目标版本不一致, 停止并要求澄清。

5. 汇总生产更新风险

   必须展示：

   - 当前 'DICEPP_IMAGE_TAG'
   - 目标版本
   - 目标镜像
   - 'data_risk'
   - 'config_risk'
   - 'migration'
   - 'backup_required'
   - metadata 中的 Summary / Risk Notes / Verification 摘要

6. 备份判断

   - 如果 'backup_required: yes', 更新前必须确认已完成升级前备份。
   - 如果风险为 'unknown' 或 metadata 缺失, 必须要求用户明确确认备份状态或接受风险。
   - 镜像回退不等于数据回退；涉及数据/配置/迁移时, 需要按备份/恢复流程处理。

7. 展示计划

   在用户确认前只展示将执行的改动, 包括：

   - 将注入环境变量 'DICEPP_IMAGE_TAG=vX.Y.Z' 并调用 'deploy-docker' 执行 pull/up/健康检查。
   - 如需回退, 回滚方式是重新执行本技能并指定另一个已发布的 'vX.Y.Z'。

## Confirmed Execution

只有当用户明确确认部署或回退目标版本后, 才允许执行：

1. 在命令中注入环境变量 'DICEPP_IMAGE_TAG=vX.Y.Z'。
2. 按 'deploy-docker' 执行项目 Docker Compose 更新。

确认语句应包含目标版本, 例如：

- 确认部署 v3.0.1
- 确认回退到 v3.0.0

## Important Notes

- 不执行 'git pull master' 作为生产部署方式。
- 不基于模糊的“最新代码”更新生产；必须明确版本。
- 不自动操作数据库、恢复备份或删除数据；这些必须由专门运维流程处理。
- 不输出 secrets、token、完整 '.env' 或敏感配置。
