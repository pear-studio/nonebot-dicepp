# nonebot-dicepp

DicePP 是 TRPG 骰子机器人项目，支持掷骰、角色卡、先攻、日志、查询等常见跑团场景。

## 快速开始

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

Standalone 示例：

```bash
python standalone_bot.py --bot-id 123456 --hub-url http://localhost:8000 --port 8080
```

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