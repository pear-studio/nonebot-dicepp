---
name: onboard
description: "当你需要了解 DicePP 项目结构、不清楚代码组织方式、或首次在此项目中工作时，调用此技能快速获取项目概览"
license: MIT
metadata:
  author: DicePP
  version: "1.2"
---

# DicePP 项目入门引导

## 项目简介

DicePP 是基于 NoneBot2 的 QQ 骰子机器人插件，用于 TRPG（桌面角色扮演游戏）。

需要了解项目结构、模块边界、命令机制或开发配方时，优先阅读 `docs/dicepp/README.md`。

## 入口索引

- 主插件代码：`src/plugins/DicePP/`
- 测试目录：`tests/`
- 开发 backlog：`docs/dev/backlog.md`
- Agent 配置源：`docs/agent/`
- Agent 同步工具：`docs/agent/sync.py`

## 使用原则

- 不在本技能里复述架构文档；需要细节时直接读取 `docs/dicepp/README.md`。
- 涉及测试、shell 验收、backlog、agent 同步时，改用对应技能。
- 不确定当前是开发还是生产环境时，先查看同步状态或读取环境规则。
