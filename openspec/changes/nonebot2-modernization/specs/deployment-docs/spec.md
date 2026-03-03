## ADDED Requirements

### Requirement: README.md 项目介绍
README.md SHALL 提供清晰的项目介绍和快速开始指南。

#### Scenario: 项目描述清晰
- **WHEN** 查看 README.md 开头
- **THEN** SHALL 包含项目名称、简短描述、主要功能列表

#### Scenario: 包含功能特性列表
- **WHEN** 查看 README.md
- **THEN** SHALL 列出主要功能：掷骰、COC 角色卡、DnD 角色卡、日志记录等

### Requirement: README.md 安装指南
README.md SHALL 提供详细的安装步骤。

#### Scenario: 包含环境要求
- **WHEN** 查看 README.md 安装部分
- **THEN** SHALL 说明 Python 版本要求（>=3.10）

#### Scenario: 包含依赖安装命令
- **WHEN** 查看 README.md 安装部分
- **THEN** SHALL 提供 pip 和 uv 两种安装方式的命令

#### Scenario: 包含配置说明
- **WHEN** 查看 README.md 安装部分
- **THEN** SHALL 说明如何复制 `.env.example` 并配置

### Requirement: README.md 启动指南
README.md SHALL 提供启动 Bot 的方法。

#### Scenario: 包含启动命令
- **WHEN** 查看 README.md 启动部分
- **THEN** SHALL 提供 `python bot.py` 或 `nb run` 命令

#### Scenario: 包含 Docker 启动方式
- **WHEN** 查看 README.md 启动部分
- **THEN** SHALL 提供 `docker-compose up` 命令及说明

### Requirement: README.md OneBot 实现说明
README.md SHALL 说明支持的 OneBot 实现。

#### Scenario: 列出推荐的 OneBot 实现
- **WHEN** 查看 README.md
- **THEN** SHALL 列出推荐的 OneBot 实现（如 Lagrange、NapCat）及链接

#### Scenario: 说明 go-cqhttp 已停止维护
- **WHEN** 查看 README.md
- **THEN** SHALL 说明 go-cqhttp 已停止维护，建议使用其他实现

### Requirement: Dockerfile 现代化
Dockerfile SHALL 使用现代最佳实践。

#### Scenario: 使用多阶段构建
- **WHEN** 检查 Dockerfile
- **THEN** SHALL 使用多阶段构建（builder + runtime）

#### Scenario: 使用现代 Python 版本
- **WHEN** 检查 Dockerfile 基础镜像
- **THEN** SHALL 使用 `python:3.12-slim` 或更高版本

#### Scenario: 包含健康检查
- **WHEN** 检查 Dockerfile
- **THEN** SHALL 包含 HEALTHCHECK 指令

### Requirement: docker-compose.yml 完整配置
docker-compose.yml SHALL 提供完整的部署配置。

#### Scenario: 服务配置正确
- **WHEN** 检查 docker-compose.yml
- **THEN** SHALL 包含端口映射、卷挂载、重启策略

#### Scenario: 数据持久化配置
- **WHEN** 检查 docker-compose.yml volumes 配置
- **THEN** SHALL 挂载 Data 目录以持久化用户数据

### Requirement: DEPLOY.md 部署文档
docs/DEPLOY.md SHALL 提供详细的生产环境部署指南。

#### Scenario: 包含服务器要求
- **WHEN** 查看 DEPLOY.md
- **THEN** SHALL 说明服务器配置要求（内存、存储等）

#### Scenario: 包含 Docker 部署步骤
- **WHEN** 查看 DEPLOY.md
- **THEN** SHALL 提供完整的 Docker 部署步骤

#### Scenario: 包含反向代理配置示例
- **WHEN** 查看 DEPLOY.md
- **THEN** SHALL 提供 Nginx/Caddy 反向代理配置示例（可选）

#### Scenario: 包含常见问题解答
- **WHEN** 查看 DEPLOY.md
- **THEN** SHALL 包含常见部署问题及解决方案
