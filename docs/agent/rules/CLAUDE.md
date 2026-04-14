# DicePP 开发规范

## 强制行为准则

1. **写代码前先阅读现有文件。** 不了解上下文就不要动手改。
2. **优先编辑，而非重写整个文件。** 最小化变更范围。
3. **不确定先询问用户。** 不要猜测或假设。
4. **在宣布完成前测试你的代码。** 验证通过是完成的必要条件。
5. **不要有奉承的开场白或结束语。** 保持简洁直接。
6. **真正解决根本问题。** 禁止用临时补丁、注释逻辑、防御性空检查来绕过问题。
7. **用户指令始终覆盖此文件。**
8. **绝不自动 commit 或 push 代码。** 除非用户明确声明，否则不执行任何 git commit 或 git push 操作。

## 项目概述

DicePP 是基于 NoneBot2 的 QQ 骰子机器人插件，用于 TRPG（桌面角色扮演游戏）。

**核心功能**：掷骰系统、角色卡管理、先攻追踪、规则书查询、抽卡、日志记录

### 目录结构

```
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

## 开发命令

```bash
# 初始化环境
uv venv .venv && uv pip install ".[dev]"

# 运行测试
uv run pytest

# 运行指定模块测试
uv run pytest tests/module/roll/ -v

# 启动机器人
uv run python bot.py
```

## 测试与验收

- **单元/集成测试**：优先使用 `run-tests` 技能，或直接运行 `uv run pytest`
- **交互式验收**：新功能完成前，**必须**使用 `dicepp-shell` 技能进行交互式机器人测试，确认指令行为正确
- **提交前**：必须跑通 `uv run pytest`，不自动 push

### 常用 dicepp-shell 测试流程

```bash
# 创建测试会话
python -m DicePP.shell start <scenario_name>

# 发送命令（可带确定性骰子结果）
python -m DicePP.shell send <scenario_name> --user <user> --msg "<cmd>" [--dice <seq>]

# 完成后清理
python -m DicePP.shell rm <scenario_name>
```

## 代码风格

- **最小化变更**：只改必要的内容
- **git comment 主要用中文**
- **细致完成任务**：不赶时间，不跳步骤
- **保持简单直接**：避免过度工程
- **命名准确，函数职责单一**
- **适当处理边界情况**，但不要添加无意义的防御性检查

## 配置文件

| 用途 | 文件 |
|------|------|
| 依赖声明 | `pyproject.toml` |
| 测试配置 | `pyproject.toml` `[tool.pytest.ini_options]` |
| 覆盖率配置 | `.coveragerc` |
| 环境变量 | `.env` |

---

## Agent Team 架构

**所有非平凡任务必须以 Agent Team 为执行单位。**

```
用户
 │
 └── Leader（项目负责人）— 统筹、决策、统一汇报窗口
      │
      ├── Designer（设计主管）— 需求分析、方案设计
      ├── Programmer（开发主管）— 代码实现、技术决策
      ├── QA（测试主管）— 测试质量、验证闭环
      └── Reviewer（审查专员）— 审查实现、触发验证、督促质量
```

### 角色职责与边界

| 角色 | 职责 | 可操作 | 不可操作 |
|------|------|--------|----------|
| **Leader** | 目标管理、进度统筹、资源协调、决策拍板、**统一对外汇报** | 读一切、写状态文件、协调任务 | 不直接修改代码 |
| **Designer** | 需求分析、方案设计、架构规划 | `docs/`、设计文档、接口定义 | `src/`、`tests/` |
| **Programmer** | 代码实现、技术决策、自测验证 | `src/`、`pyproject.toml`、工具脚本 | `tests/`（由 QA 负责） |
| **QA** | 测试质量把关、测试路径覆盖、验证闭环 | `tests/`、测试配置 | `src/`、测试框架本身 |
| **Reviewer** | 审查实现结果、触发验证、督促质量 | 读一切、写 `.agent/` 审查报告 | **任何代码文件** |

### 执行规则

- **Team 启动**：`TeamCreate` → `TaskCreate` → 分配给对应角色
- **spawn subagent 时使用 `mode: "bypassPermissions"`** 继承当前权限
- **工作流转**：
  1. Programmer 完成代码后 → 主动提交 Reviewer 审查
  2. Reviewer 独立运行 `uv run pytest` 验证 → 给出 PASS / NEEDS_FIX / REJECT
  3. Reviewer 通过后 → 上报 Leader，由 Leader 统一向用户汇报
- **状态维护**：Leader 负责维护 `.temp/agents-team/<任务名称>/progress.md`，记录当前任务、完成节点、阻塞项

### 汇报关系

- **Leader 是唯一对用户的直接接口**
- Designer、Programmer、QA、Reviewer 统一向 Leader 汇报
- 各角色不要直接向用户发送碎片化的进度消息

### 严禁行为（全员适用）

1. **禁止绕过问题**：不可用临时补丁、注释逻辑、防御性空检查、空 catch 块来回避根因。
2. **禁止越界修改**：各角色只能操作自己边界内的文件。
3. **禁止无验证的声称**：不可在未运行测试前声称"已完成"或"测试通过"。
4. **禁止硬编码业务耦合的默认值**：配置项应走配置系统，不可埋 magic number/string。
5. **禁止吞掉错误**：不可忽略返回值或无理由强转类型。
