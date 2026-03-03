# DicePP 代码框架文档

本目录包含 DicePP 项目的代码框架文档，帮助开发者理解系统架构和开发规范。

## 文档目录

- [架构总览](./architecture.md) - 项目整体架构介绍
- [命令模式](./command_pattern.md) - 命令系统的设计与实现
- [命令示例：以掷骰为例](./example_roll.md) - 以 `.r` 掷骰命令为例详解

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

| 模块 | 职责 |
|------|------|
| `roll/` | 骰子引擎与掷骰命令 |
| `character/` | 角色系统 (CoC / D&D 5e) |
| `common/` | 通用功能 (日志、宏、变量等) |
| `initiative/` | 先攻系统 |
| `deck/` | 卡牌与随机生成器 |
| `dice_hub/` | 多机器人互联 |
| `query/` | 自定义查询系统 |
| `misc/` | 杂项工具 |
| `fastapi/` | HTTP API 接口 |

## 关键概念

1. **命令模式**: 所有用户交互通过 `UserCommandBase` 处理
2. **数据驱动**: 配置和数据通过 `DataManager` 持久化
3. **国际化**: 文本通过 `LocalizationManager` 管理
