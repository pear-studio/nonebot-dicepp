---
name: onboard
description: Help new developers understand the DicePP project structure and get started with development.
license: MIT
metadata:
  author: DicePP
  version: "1.0"
---

Help new developers understand the DicePP project structure and get started with development.

**Input**: None required. This skill provides an overview of the project.

**Steps**

1. **Project Overview**

   DicePP 是一个基于 NoneBot2 的 QQ 骰子机器人插件，主要用于 TRPG（桌面角色扮演游戏）场景。

   **核心功能**:
   - 掷骰系统 (支持多种骰子表达式)
   - 查询系统 (DND5E 等规则书资料)
   - 角色卡管理 (COC/DND)
   - 先攻管理
   - 抽卡/随机生成器
   - 日志记录

2. **Directory Structure**

   ```
   nonebot-dicepp/
   ├── src/plugins/DicePP/   # 主插件代码
   │   ├── core/             # 核心框架
   │   ├── module/           # 功能模块
   │   ├── adapter/          # NoneBot 适配器
   │   └── utils/            # 工具函数
   ├── tests/                # 测试文件
   ├── docs/                 # 文档
   ├── openspec/             # 变更规范
   ├── bot.py                # 入口文件
   └── pyproject.toml        # 项目配置
   ```

3. **Core Framework**

   | 目录 | 说明 |
   |------|------|
   | `core/bot.py` | Bot 主类，管理所有命令和数据 |
   | `core/command/` | 命令基类和装饰器 |
   | `core/data/` | 数据持久化 (DataManager) |
   | `core/config/` | 配置管理 (ConfigManager) |
   | `core/localization/` | 本地化管理 |
   | `core/communication/` | 消息通信抽象 |

4. **Module System**

   每个功能模块位于 `module/` 目录下：

   | 模块 | 说明 |
   |------|------|
   | `common/` | 通用命令 (help, master, mode 等) |
   | `roll/` | 掷骰命令 (.r, .rd, .rh 等) |
   | `query/` | 查询命令 (.查询, .q) |
   | `deck/` | 抽卡命令 (.draw) |
   | `character/` | 角色卡 (COC/DND5E) |
   | `initiative/` | 先攻管理 (.ri, .init) |

5. **How to Add a New Command**

   1. 在合适的模块目录下创建 `xxx_command.py`
   2. 继承 `UserCommandBase` 类
   3. 使用 `@custom_user_command` 装饰器
   4. 实现必要的方法:
      - `can_process_msg()` - 判断是否处理消息
      - `process_msg()` - 处理消息并返回回复
      - `get_help()` - 返回帮助文本
      - `get_description()` - 返回命令描述

6. **Development Setup**

   ```powershell
   # 克隆项目
   git clone <repo>
   cd nonebot-dicepp

   # 初始化环境并安装依赖
   uv venv .venv && uv pip install ".[dev]"

   # 配置环境
   cp .env.example .env
   # 编辑 .env 填入 QQ 账号等信息

   # 运行测试
   uv run pytest -v

   # 启动机器人
   uv run python bot.py
   ```

7. **Key Concepts**

   **DataChunk**: 数据持久化单元，用于保存用户数据、配置等
   
   **LocalizationText**: 本地化文本，支持多语言和自定义回复
   
   **MessageMetaData**: 消息元数据，包含发送者、群组等信息
   
   **BotCommandBase**: 机器人执行的命令（发送消息、发送文件等）

8. **Useful Files to Read**

   - `src/plugins/DicePP/core/bot.py` - 了解 Bot 生命周期
   - `src/plugins/DicePP/core/command/user_command.py` - 命令基类
   - `src/plugins/DicePP/module/roll/roll_dice_command.py` - 掷骰命令示例
   - `docs/agent/rules/dicepp.md` - 开发规范

**Output**

输出项目概览和入门指南，帮助开发者快速理解项目结构。

**Next Steps**

1. 阅读 `docs/agent/rules/dicepp.md` 了解开发规范