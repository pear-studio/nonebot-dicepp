# Persona 模块文档

> DicePP 人格化 AI 系统文档中心

本目录包含 Persona 模块的完整文档，分为**使用文档**（面向部署者和玩家）和**开发文档**（面向开发者）两类。

---

## 使用文档

| 文档 | 说明 | 目标读者 |
|------|------|----------|
| [deploy.md](./deploy.md) | 部署指南：API Key 配置、角色卡准备、启动验证 | 部署者 |
| [config-example.md](./config-example.md) | 完整配置字段说明与示例 | 部署者 |
| [character_card.md](./character_card.md) | 角色卡编写指南（SillyTavern V2 兼容） | 角色作者 |
| [debug-commands.md](./debug-commands.md) | `.ai admin` 调试命令使用说明 | 管理员 |

### 快速开始

1. 阅读 [deploy.md](./deploy.md) 完成基础部署
2. 参考 [character_card.md](./character_card.md) 编写角色卡
3. 查阅 [config-example.md](./config-example.md) 调整配置参数

---

## 开发文档

| 文档 | 说明 | 目标读者 |
|------|------|----------|
| [architecture.md](./architecture.md) | 整体架构、模块分层、核心流程、扩展点 | 开发者 |

### 核心代码目录

```
src/plugins/DicePP/module/persona/
├── command.py          # DicePP 命令入口（@bot / .ai / .ai admin）
├── orchestrator.py     # 核心编排层
├── character/          # 角色卡加载与模型
├── llm/                # LLM 客户端与路由
├── memory/             # 上下文构建器
├── data/               # 数据模型、存储层、迁移脚本
├── game/               # 好感度系统、时间衰减
├── proactive/          # 主动消息调度、角色生活模拟、延迟任务队列
├── agents/             # 评分 Agent、事件 Agent
└── utils/              # 工具函数（隐私脱敏、掷骰适配等）
```

---

## 模块能力概览

Persona 模块为 DicePP 提供了一个**有性格、有记忆、会主动找你聊天**的虚拟角色：

- **深度人格**：通过 YAML 角色卡定义，兼容 SillyTavern V2
- **四层记忆**：短期记忆 + 用户档案 + 群聊观察 + 角色日记
- **好感度系统**：四维隐藏指标（亲密/激情/信任/安全感），影响对话温度
- **角色生活模拟**：全天实时生成生活事件，晚间总结为日记
- **主动消息**：事件分享、想念触发、定时问候
- **工具调用**：search_memory（深层记忆搜索）、roll_dice（掷骰子）
- **成本控制**：主模型限额、用户自带 Key、白名单免限额

---

## 相关文档

- [DicePP 完整架构文档](../README.md)
