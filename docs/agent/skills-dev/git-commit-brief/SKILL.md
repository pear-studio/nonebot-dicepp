---
name: git-commit-brief
description: 每次创建或重写 Git commit 前使用。检查实际变更边界，并生成符合项目约定的中文 Commit 提交说明。
---

# Git Commit Brief

提交前读取实际 diff，确认改动属于同一主题。若包含多个独立主题，考虑拆分提交；无法判断时询问用户。实现 backlog 时，对应 backlog 文件的修改与实现本体一起提交，不必拆分。

## 格式

```text
<type>(<scope>): <中文主题>

目的:
- 说明为什么需要这次修改，以及要解决的问题。

改动:
- 概括实现方式和关键变化。

影响:
- 说明对行为、兼容性、数据或使用者的影响。
```

`type` 和 `scope` 必填，使用小写英文。主题和正文使用中文；代码、API、配置项等专有名称可以保留英文。

正文必须包含 `目的`、`改动`、`影响`。没有明显影响时可写“无明显影响”。

## Type

| Type | 用途 |
|---|---|
| `feat` | 新增功能或能力 |
| `fix` | 修复错误 |
| `refactor` | 调整实现但不改变外部行为 |
| `test` | 新增或调整测试 |
| `docs` | 仅修改文档 |
| `chore` | 依赖、配置及其他维护工作 |

## Scope

| Scope | 范围 |
|---|---|
| `persona` | 角色卡、AI、对话、评分及 prompt |
| `core` | Bot 核心框架、命令、数据、数据库及适配器 |
| `module` | roll、deck、character 等其他功能模块 |
| `dashboard` | Dashboard 前端及其交互 |
| `runtime` | Bot 进程控制、数据运行时及部署运行时 |
| `dev` | 测试基础设施、开发工具、构建、CI、发布及环境配置 |
| `agent` | Agent 规则、技能及同步工具 |
| `docs` | 架构、开发指南等项目文档 |

确定 scope 时，选择主要影响对象。测试用例使用被测对象的 scope；测试基础设施使用 `dev`。不确定可以询问用户的意见。

## 内容要求

只记录脱离当前会话后仍有追溯价值的信息。不要写入本地路径、临时序号、review 阶段、任务执行过程等上下文，不主动添加任务或 PR 编号。

根据实际修改编写提交说明，不要只看文件名。Agent 配置以 `docs/agent/*` 为源，不要重复提交同步生成的本地投影。

不要使用 emoji。

## 示例

- 反例 `fix: 处理 review 反馈四项` → 正例 `fix(persona): 修正 share_desire 阈值边界`
- 反例 `feat: 事件生成超时策略与兜底处理 (B-260507-d3cc8b)` → 正例 `feat(persona): 增加事件生成超时与兜底策略`
- 反例 `refactor(test): 将 unittest.TestCase 迁移为 pytest` → 正例 `refactor(dev): 将测试基础设施迁移为 pytest`
