# DicePP 代码框架文档

> **文档导航**: 首页 | [架构总览](./architecture.md) | [命令模式](./command_pattern.md) | [掷骰示例](./example_roll.md) | [指令速查](./command_reference.md)

本目录包含 DicePP 项目的代码框架文档，帮助开发者理解系统架构和开发规范。

---

## 文档目录

| 文档 | 说明 |
|------|------|
| [架构总览](./architecture.md) | 项目整体架构介绍，包括核心模块职责与数据流 |
| [命令模式](./command_pattern.md) | 命令系统的设计与实现，自定义命令开发指南 |
| [命令示例：以掷骰为例](./example_roll.md) | 以 `.r` 掷骰命令为例，详解完整实现流程 |
| [指令速查手册](./command_reference.md) | 所有 36 个用户指令的完整参考文档 |

---

## 快速导航

### 核心模块 (core/)

| 模块 | 职责 |
|------|------|
| `bot/` | 机器人主类 Bot，命令注册与消息分发 |
| `command/` | 命令基类定义，消息处理接口 |
| `communication/` | 消息封装与端口管理 |
| `config/` | 配置管理系统 |
| `data/` | 数据持久化管理 |
| `localization/` | 国际化文本管理 |
| `statistics/` | 统计数据收集 |

### 功能模块 (module/)

| 模块 | 职责 | 相关指令 |
|------|------|----------|
| `roll/` | 骰子引擎与掷骰命令 | `.r`, `.c`, `.w`, `.karmadice` |
| `character/` | 角色系统 (CoC / D&D 5e) | `.角色卡`, `.hp` |
| `common/` | 通用功能 (日志、宏、变量等) | `.help`, `.nn`, `.log`, `.def` |
| `initiative/` | 先攻系统 | `.init`, `.ri`, `.bt` |
| `deck/` | 卡牌与随机生成器 | `.draw`, `.gen` |
| `dice_hub/` | 多机器人互联 | `.hub` |
| `query/` | 自定义查询系统 | `.q`, `.hb` |
| `misc/` | 杂项工具 | `.jrrp`, `.coc`, `.dnd`, `.统计` |
| `fastapi/` | HTTP API 接口 | - |

---

## 关键概念

1. **命令模式**: 所有用户交互通过 `UserCommandBase` 处理，详见 [命令模式](./command_pattern.md)
2. **数据驱动**: 业务数据通过 `BotDatabase`（SQLite + `Repository`）持久化，详见 [架构总览](./architecture.md) 与仓库根目录 `docs/DATA_LAYER.md`
3. **国际化**: 文本通过 `LocalizationManager` 管理

---

## 推荐阅读顺序

1. **新手入门**: [架构总览](./architecture.md) -> [命令模式](./command_pattern.md) -> [掷骰示例](./example_roll.md)
2. **快速查阅指令**: [指令速查手册](./command_reference.md)
3. **开发新功能**: [命令模式](./command_pattern.md) -> [指令速查手册 - 开发者指南](./command_reference.md#开发者指南)