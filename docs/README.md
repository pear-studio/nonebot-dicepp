# 文档导航

本目录汇总 DicePP 项目的主要文档，按用途分组如下。

## 快速入口

- 部署与运行：[`deploy.md`](./deploy.md)
- Standalone 模式：[`standalone.md`](./standalone.md)
- 数据层架构：[`dicepp/data_layer.md`](./dicepp/data_layer.md)
- 掷骰引擎：[`dicepp/roll_engine.md`](./dicepp/roll_engine.md)
- DicePP 架构与命令文档：[`dicepp/README.md`](./dicepp/README.md)

## DicePP 开发文档（`docs/dicepp`）

- 系统总览：[`dicepp/system_overview.md`](./dicepp/system_overview.md)
- 命令运行机制：[`dicepp/command_runtime.md`](./dicepp/command_runtime.md)
- 命令目录：[`dicepp/command_catalog.md`](./dicepp/command_catalog.md)
- 数据层架构：[`dicepp/data_layer.md`](./dicepp/data_layer.md)
- 掷骰引擎：[`dicepp/roll_engine.md`](./dicepp/roll_engine.md)
- Standalone 运行：[`dicepp/standalone_runtime.md`](./dicepp/standalone_runtime.md)
- 开发配方：[`dicepp/dev_recipes.md`](./dicepp/dev_recipes.md)

## Agent 相关文档

- 技能与规则：[`agent/`](./agent/)

## 常用脚本指令（`scripts/`）

- 开发环境（Windows）：
  - `scripts\dev\install.bat`：安装/更新开发依赖
  - `scripts\dev\run.bat`：本地启动 DicePP
- 测试脚本（Windows）：
  - `scripts\test\run_unit_test.bat`：运行单元测试
  - `scripts\test\run_integration_test.bat`：运行集成测试
  - `scripts\test\run_build_test.bat`：运行构建验证测试
- 迁移与数据检查：
  - `scripts\migrate\manage_migrations.bat`：迁移管理入口（Windows）
  - `python scripts/migrate/manage_migrations.py`：迁移管理入口（跨平台）
  - `python scripts/capture_baseline.py`：采集/更新兼容性基线
- Linux 部署脚本：
  - `bash scripts/deploy/linux/setup.sh`：初始化部署
  - `bash scripts/deploy/linux/start.sh|stop.sh|restart.sh|status.sh|logs.sh|update.sh`：服务运维
  - `bash scripts/deploy/linux/llonebot/setup.sh`：安装/配置 LLOneBot
- Windows 部署辅助：
  - `scripts\deploy\windows\install_deps.bat`：安装运行依赖
  - `scripts\deploy\windows\start.bat`：启动（生产配置）
  - `scripts\deploy\windows\start_dev.bat`：开发模式启动
