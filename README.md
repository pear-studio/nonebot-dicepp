# nonebot-dicepp

DicePP 是 TRPG 骰子机器人项目，支持掷骰、角色卡、先攻、日志、查询等常见跑团场景。

## 快速开始

```bash
git clone https://github.com/pear-studio/nonebot-dicepp.git
cd nonebot-dicepp
uv sync --group dev
```

本地开发运行：

```bash
uv run python bot.py
```

## 运行方式

- NoneBot 插件模式：作为 OneBot V11 生态插件运行（常见于 QQ 机器人接入）。
- Standalone 模式：直接运行 `standalone_bot.py`，提供 `/dpp/*` HTTP 接口。

## 文档入口

根目录只保留总览，详细说明请查阅 `docs/`：

- 新手入口：`docs/start-here.md`
- Windows 本地运行：`docs/windows.md`
- Linux / Docker 部署：`docs/linux.md`
- 配置说明：`docs/configuration.md`
- 开发入口：`docs/dev/guide.md`

## 常用命令

- 安装开发依赖：`uv sync --group dev`
- 运行测试：`uv run pytest`
- 本地启动：`uv run python bot.py`
- Windows 打包：`scripts\build\build.bat`

完整使用路径见 `docs/start-here.md`。

## 交流

交流群：`861919492`
