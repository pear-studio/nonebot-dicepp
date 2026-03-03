## Why

项目使用的 NoneBot2 版本为 2.0.0b1（Beta），距离最新稳定版 2.4.x 已有多个大版本更新。旧版依赖 `nonebot-adapter-cqhttp` 已被弃用并合并到 `nonebot-adapter-onebot`。长期不更新会导致安全风险、兼容性问题，且无法使用新版特性。参考 https://nonebot.dev/docs/ 和 https://luckylillia.com/guide/introduction 文档，需要进行全面现代化升级。

## What Changes

### 依赖升级
- **BREAKING**: 升级 `nonebot2` 从 `^2.0.0b1` 到 `^2.4.0`（稳定版）
- **BREAKING**: 升级 `nonebot-adapter-onebot` 从 `2.0.0b1` 到 `^2.4.0`
- 升级 `nb-cli` 从 `^0.6.4` 到 `^1.4.0`
- 移除 `pyproject.toml` 中已弃用的 `nonebot-adapter-cqhttp` 引用
- 更新其他过时依赖版本（`aiohttp`、`uvicorn`、`fastapi` 等）

### 配置现代化
- 更新 `pyproject.toml` 使用现代 Python 项目标准
- 添加 `.env.example` 模板文件，方便新用户配置
- 更新 NoneBot 配置段落适配新版格式
- 将 Python 最低版本要求从 3.8 提升到 3.10

### 代码适配
- 检查并更新 `bot.py` 中的 NoneBot 初始化代码
- 检查 adapter 层 API 兼容性
- 更新日志配置适配新版 loguru 用法

### 文档与部署
- 更新 `README.md`，添加完整的安装和部署指南
- 更新 `Dockerfile` 基础镜像版本（Python 3.10 → 3.12）
- 更新 `docker-compose.yml` 配置
- 完善 `docs/DEPLOY.md` 部署文档

## Capabilities

### New Capabilities
- `dependency-upgrade`: 依赖版本升级规范，定义升级策略和兼容性要求
- `config-modernization`: 配置文件现代化规范，包括 pyproject.toml 和环境变量模板
- `deployment-docs`: 部署文档规范，定义 README 和部署指南的内容要求

### Modified Capabilities
<!-- 无现有 spec 需要修改 -->

## Impact

### 受影响的代码
- `bot.py` - NoneBot 初始化和适配器注册
- `src/plugins/DicePP/adapter/nonebot_adapter.py` - OneBot 适配器层
- `pyproject.toml` - 项目依赖和配置
- `requirements.txt` - pip 依赖清单

### 受影响的配置
- `.env` / `.env.prod` / `.env.linux` - 环境变量配置
- `docker-compose.yml` - Docker 编排配置
- `Dockerfile` / `Dockerfile_pi` - 容器构建配置

### 破坏性变更风险
- NoneBot2 2.0 beta → 2.4 可能存在 API 变化
- Python 3.8 → 3.10 最低版本要求变化
- 旧版 go-cqhttp 配置模板可能需要更新
