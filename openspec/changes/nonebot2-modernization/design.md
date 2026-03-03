## Context

### 当前状态

项目基于 NoneBot2 2.0.0b1（Beta 版本，约 2021 年发布），核心依赖版本：

| 依赖 | 当前版本 | 最新稳定版 |
|------|----------|------------|
| nonebot2 | ^2.0.0b1 | ^2.4.0 |
| nonebot-adapter-onebot | 2.0.0b1 | ^2.4.0 |
| nb-cli | ^0.6.4 | ^1.4.0 |
| Python | >=3.8 | >=3.10 |

### 现有架构

```
bot.py                          # NoneBot 入口
├── nonebot.init()
├── driver.register_adapter(OneBot_V11_Adapter)
└── nonebot.load_from_toml("pyproject.toml")

src/plugins/DicePP/
├── adapter/nonebot_adapter.py  # OneBot V11 适配层
├── core/                       # 核心框架（与 NoneBot 解耦）
└── module/                     # 业务模块（与 NoneBot 解耦）
```

关键发现：项目架构设计良好，核心业务逻辑与 NoneBot 框架解耦，仅 `bot.py` 和 `adapter/` 直接依赖 NoneBot API。

### 约束条件

- 不能破坏现有用户数据（JSON/SQLite 文件）
- 需保持与主流 OneBot 实现的兼容性（Lagrange、NapCat 等）
- 部署环境：Docker / Linux / Windows

## Goals / Non-Goals

**Goals:**
- 升级 NoneBot2 到最新稳定版 2.4.x
- 清理所有已弃用的依赖引用
- 提供完整的 `.env.example` 配置模板
- 更新文档使新用户能够快速部署
- 保持所有现有功能正常工作

**Non-Goals:**
- 不重构核心业务逻辑
- 不添加新的 Bot 功能
- 不迁移到其他 Bot 框架
- 不支持 OneBot V12（保持 V11 兼容）

## Decisions

### D1: NoneBot2 版本选择

**决策**: 升级到 NoneBot2 ^2.4.0

**理由**:
- 2.4.x 是当前稳定版本线，有持续维护
- 相比 2.0 beta 有大量 bug 修复和性能优化
- API 变化主要在初始化和配置，适配层变化小

**备选方案**:
- 2.3.x: 稳定但功能较旧
- 3.x: 尚未发布，不考虑

### D2: Python 版本要求

**决策**: 最低版本从 3.8 提升到 3.10

**理由**:
- Python 3.8 已于 2024 年 10 月 EOL
- NoneBot2 2.4.x 推荐 Python 3.10+
- 3.10 提供更好的类型提示和 match 语法

**影响**:
- Dockerfile 基础镜像更新为 `python:3.12-slim`
- 旧版 Python 用户需要升级环境

### D3: 配置管理方案

**决策**: 保持 pyproject.toml + .env 双配置模式

**理由**:
- NoneBot2 原生支持此模式
- `.env` 用于环境敏感配置（端口、密钥等）
- `pyproject.toml` 用于项目元数据和 NoneBot 插件配置

**配置文件职责**:
```
.env.example     # 配置模板（新增）
.env             # 本地开发配置（gitignore）
.env.prod        # 生产环境配置示例
pyproject.toml   # 项目依赖 + NoneBot 插件声明
```

### D4: 依赖管理工具

**决策**: 同时支持 pip/uv 和 Poetry

**理由**:
- 现有项目使用 Poetry，保持兼容
- uv 更快，适合 CI/CD 和 Docker 构建
- `pyproject.toml` 已包含 `[project]` 段落支持 uv

### D5: 适配器层修改策略

**决策**: 最小化修改，仅更新 import 路径和已弃用 API

**理由**:
- 现有 `nonebot_adapter.py` 已使用 `nonebot.adapters.onebot.v11`
- OneBot V11 适配器 API 在 2.0 → 2.4 变化较小
- 无需大规模重构

**需要检查的 API**:
```python
# 可能需要更新的 import
from nonebot.adapters.onebot.v11 import Adapter  # 确认无变化
from nonebot.adapters.onebot.v11 import ActionFailed  # 确认无变化

# 需要验证的 API
bot.send_group_msg()
bot.send_private_msg()
bot.call_api()
```

## Risks / Trade-offs

### R1: API 兼容性风险
**风险**: NoneBot2 2.0 → 2.4 可能存在未文档化的 API 变更
**缓解**: 
- 在开发环境完整运行所有测试用例
- 在测试群进行功能验证
- 准备回滚脚本

### R2: 第三方 OneBot 实现兼容性
**风险**: 部分用户使用的 OneBot 实现可能与新版适配器不兼容
**缓解**:
- 文档中明确支持的 OneBot 实现版本
- 保持 OneBot V11 协议，不升级到 V12

### R3: 用户环境升级成本
**风险**: Python 3.8/3.9 用户需要升级 Python
**缓解**:
- 在 README 中明确说明版本要求
- 提供 Docker 镜像作为开箱即用方案

### R4: 部署脚本兼容性
**风险**: `tools/deploy/` 下的脚本可能需要更新
**缓解**:
- 检查并更新所有部署脚本
- 更新 nb-cli 命令语法（如有变化）

## Migration Plan

### Phase 1: 依赖升级（本地验证）
1. 更新 `pyproject.toml` 和 `requirements.txt` 中的版本号
2. 运行 `uv sync` 或 `pip install -r requirements.txt`
3. 运行 `pytest` 验证所有测试通过
4. 本地启动 bot 验证基本功能

### Phase 2: 代码适配
1. 检查 `bot.py` 初始化代码，适配新版 API
2. 检查 `adapter/nonebot_adapter.py`，修复任何弃用警告
3. 更新日志配置（如有 loguru 变化）

### Phase 3: 配置与文档
1. 创建 `.env.example` 模板
2. 更新 `README.md` 安装指南
3. 更新 `Dockerfile` 基础镜像
4. 更新 `tools/docs/DEPLOY.md`

### Phase 4: 发布
1. 更新版本号（1.0.0 Beta2 → 2.0.0）
2. 创建 Release 并更新 CHANGELOG
3. 构建并推送新版 Docker 镜像

### 回滚策略
- 保留原始依赖版本在 git 历史中
- 如遇严重问题，可 `git revert` 并重新部署

## Open Questions

1. **nb-cli 是否仍需要？** - 新版 NoneBot2 可以直接 `python bot.py` 启动，nb-cli 主要用于项目脚手架
2. **是否需要支持 OneBot V12？** - 当前决策是仅支持 V11，但未来可考虑
3. **go-cqhttp 模板是否保留？** - go-cqhttp 已停止维护，考虑替换为 Lagrange/NapCat 模板
