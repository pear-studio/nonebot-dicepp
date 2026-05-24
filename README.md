# nonebot-dicepp

DicePP 是 TRPG 骰子机器人项目，支持掷骰、角色卡、先攻、日志、查询等常见跑团场景。

## 一键部署（Windows，推荐给最终用户）

下载或克隆本仓库后，**双击根目录的 `启动骰娘.bat`** 即可：

- 首次运行：自动下载 uv → 拉取独立 Python 3.10 → `uv sync` 装依赖 → 启动管理后台并打开浏览器（约 3–10 分钟，看网速）。
- 后续运行：秒级冷启动。
- 更新代码：双击 `更新.bat`。
- 清理环境：双击 `卸载.bat`（仅清运行时目录，保留你的骰娘数据）。

详细的用户向使用文档见 [`使用说明.md`](使用说明.md)。

> 所有运行时依赖（uv / Python / 依赖包）都装在项目目录内的 `bin/` `.python/` `.venv/`，**不污染系统环境**，卸载时整体删除即可。

## 快速开始（开发者）

```bash
git clone https://github.com/pear-studio/nonebot-dicepp.git
cd nonebot-dicepp
uv sync --dev
```

本地开发运行（Windows）：

```bat
scripts\dev\run.bat
```

## 运行方式

- NoneBot 插件模式：作为 OneBot V11 生态插件运行（常见于 QQ 机器人接入）。
- Standalone 模式：直接运行 `standalone_bot.py`，提供 `/dpp/*` HTTP 接口。

详见 `docs/dicepp/standalone_runtime.md`。

## 文档入口

根目录只保留总览，详细说明请查阅 `docs/`：

- 文档总导航：`docs/README.md`
- 部署文档：`docs/deploy.md`
- DicePP 开发文档：`docs/dicepp/README.md`

## 常用脚本

- 开发启动：`scripts\dev\run.bat`
- 安装依赖：`scripts\dev\install.bat`
- 单元测试：`scripts\test\run_unit_test.bat`
- 集成测试：`scripts\test\run_integration_test.bat`
- 构建验证：`scripts\test\run_build_test.bat`

完整脚本说明见 `docs/README.md`。

## 交流

交流群：`861919492`