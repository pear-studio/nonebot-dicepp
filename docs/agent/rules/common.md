# DicePP Agent Common Rules

## 强制行为准则

1. **写代码前先阅读现有文件。** 不了解上下文就不要动手改。
2. **优先编辑，而非重写整个文件。** 最小化变更范围。
3. **不确定先询问用户。** 不要猜测或假设。
4. **在宣布完成前验证你的工作。** 未验证时必须明确说明。
5. **不要有奉承的开场白或结束语。** 保持简洁直接。
6. **真正解决根本问题。** 禁止用临时补丁、注释逻辑、防御性空检查来绕过问题。
7. **用户指令始终覆盖此文件。**
8. **绝不自动 commit 或 push 代码。** 除非用户明确声明，否则不执行任何 git commit 或 git push 操作。

## 项目概述

DicePP 是基于 NoneBot2 的 QQ 骰子机器人插件，用于 TRPG（桌面角色扮演游戏）。

**核心功能**：掷骰系统、角色卡管理、先攻追踪、规则书查询、抽卡、日志记录

### 目录结构

```text
nonebot-dicepp/
├── src/plugins/DicePP/   # 主插件代码
│   ├── core/             # 核心框架 (Bot, Command, Data)
│   ├── module/           # 功能模块 (roll, character, initiative...)
│   └── adapter/          # NoneBot 适配器
├── tests/                # 测试文件
├── docs/                 # 文档
│   ├── dicepp/           # 项目架构文档
│   └── agent/            # Agent 配置与技能
├── bot.py                # 入口文件
└── pyproject.toml        # 依赖与工具配置
```

### 关键文件索引

| 需求 | 文件位置 |
|------|----------|
| Bot 主类 | `src/plugins/DicePP/core/bot/dicebot.py` |
| 命令基类 | `src/plugins/DicePP/core/command/user_cmd.py` |
| 掷骰命令示例 | `src/plugins/DicePP/module/roll/roll_dice_command.py` |
| 数据层（SQLite） | `src/plugins/DicePP/core/data/database.py` |
| 完整架构文档 | `docs/dicepp/README.md` |

## Agent 配置管理

Agent 规则与技能由 `docs/agent/sync.py` 管理。需要同步、检查、迁移或解释 `.codex`、`.claude` 中的 agent 配置时，运行该脚本的 help 并按脚本输出操作。

## 代码风格

- **最小化变更**：只改必要的内容。
- **git comment 主要用中文**。
- **细致完成任务**：不赶时间，不跳步骤。
- **保持简单直接**：避免过度工程。
- **命名准确，函数职责单一**。
- **适当处理边界情况**，但不要添加无意义的防御性检查。

## 配置文件

| 用途 | 文件 |
|------|------|
| 依赖声明 | `pyproject.toml` |
| 测试配置 | `pyproject.toml` `[tool.pytest.ini_options]` |
| 覆盖率配置 | `.coveragerc` |
| 环境变量 | `.env` |

## 严禁行为（全员适用）

1. **禁止绕过问题**：不可用临时补丁、注释逻辑、防御性空检查、空 catch 块来回避根因。
2. **禁止越界修改**：各角色只能操作自己边界内的文件。
3. **禁止无验证的声称**：不可在未运行验证前声称"已完成"或"测试通过"。
4. **禁止硬编码业务耦合的默认值**：配置项应走配置系统，不可埋 magic number/string。
5. **禁止吞掉错误**：不可忽略返回值或无理由强转类型。

## Backlog

本地 backlog 文件是 `docs/dev/backlog.md`，由 `scripts/tools/backlog.py` CLI 管理。需要新增、核实清理或实现 backlog 条目时，优先使用对应的 backlog 技能。

常用操作包括 `add`、`list`、`show`、`close`、`validate` 和 `sort`。
