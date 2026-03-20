---
name: onboard
description: "当你需要了解 DicePP 项目结构、不清楚代码组织方式、或首次在此项目中工作时，调用此技能快速获取项目概览"
license: MIT
metadata:
  author: DicePP
  version: "1.2"
---

# DicePP 项目入门引导

**何时使用此技能**: 
- 你首次在 DicePP 项目中工作
- 你需要了解项目整体结构
- 你不确定某个功能应该放在哪个模块
- 你需要快速定位关键文件

---

## 项目简介

DicePP 是基于 NoneBot2 的 QQ 骰子机器人插件，用于 TRPG（桌面角色扮演游戏）。

**核心功能**: 掷骰系统、角色卡管理、先攻追踪、规则书查询、抽卡、日志记录

---

## 目录结构

```
nonebot-dicepp/
├── src/plugins/DicePP/   # 主插件代码
│   ├── core/             # 核心框架 (Bot, Command, Data)
│   ├── module/           # 功能模块 (roll, character, initiative...)
│   └── adapter/          # NoneBot 适配器
├── tests/                # 测试文件
└── bot.py                # 入口文件
```

---

## 详细文档位置

**完整架构文档**: `src/plugins/DicePP/docs/README.md`

包含:
- 架构总览 - 核心模块职责与数据流
- 命令模式 - 如何开发新命令
- 掷骰示例 - 完整命令实现流程
- 指令速查 - 所有 36 个用户指令参考

**开发规范**: `docs/agent/rules/dicepp.md`

---

## 关键文件速查

| 需求 | 文件位置 |
|------|----------|
| Bot 主类 | `src/plugins/DicePP/core/bot/dicebot.py` |
| 命令基类 | `src/plugins/DicePP/core/command/user_cmd.py` |
| 掷骰命令示例 | `src/plugins/DicePP/module/roll/roll_dice_command.py` |
| 数据层（SQLite） | `src/plugins/DicePP/core/data/database.py` |

---

## 快速开始命令

```powershell
uv venv .venv && uv pip install ".[dev]"  # 安装依赖
uv run pytest -v                           # 运行测试
```

---

**Further Reading**: 

1. 阅读 `src/plugins/DicePP/docs/README.md` 了解完整架构
2. 阅读 `docs/agent/rules/dicepp.md` 了解开发规范