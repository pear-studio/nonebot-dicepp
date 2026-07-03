---
name: git-commit-brief
description: Read before every local git commit.
---

# Git Commit Brief

## 提交格式规范

统一遵循 Conventional Commits: `<type>(<scope>): <一句话主题>`

- **type 和 scope 均为必填**，scope 使用小写英文
- 主题与正文只写"代码改了什么/为什么/影响什么"，让未来 `git log` 读者一眼看出本次代码改动本身的内容

### Type 分类

| Type | 含义 |
|---|---|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `refactor` | 重构（不改变外部行为） |
| `docs` | 纯文档变更 |
| `chore` | 杂项维护（不改变功能逻辑的琐事：配置清理、依赖更新、backlog 整理、CI 调整等） |

### Scope 词汇表

| Scope | 适用范围 |
|---|---|
| `persona` | persona 模块（角色卡、AI、对话、评分、prompt 等） |
| `core` | 核心框架（bot/command/data/db/adapter） |
| `module` | 其他功能模块（roll/deck/character/initiative/query/common） |
| `test` | 测试基础设施（框架、fixture、配置、用例拆分等） |
| `dev` | 开发环境/工具链（worktree/venv/Docker/shell/CI/配置） |
| `agent` | Agent 配置与技能文件（docs/agent/rules/、docs/agent/skills-*/、docs/agent/sync.py） |
| `docs` | 文档（架构、开发指南等） |

不确定 scope 时，选影响最大的模块作为 scope。

## 规则

- Agent 工具目录由 `docs/agent/sync.py` 生成；提交前避免把同步产生的本地工作目录状态重复计入，按真实源目录 `docs/agent/*` 核对后再暂存.
- **禁止把流程/过程性信息写进 commit message**. 这些元数据随时间贬值, 读者关心的是"代码到底改了什么", 不是"它走过哪几个开发流程节点":
  - 禁止 review 阶段标记: `review 闭环`、`review 反馈修复`、`R1/R2/R4`、`处理 review 反馈 N 项`
  - 禁止 backlog / 任务 ID 尾巴, 如 `(B-260507-d3cc8b)`
  - 禁止版本/阶段拼接: `xxx v4 + review 反馈修复`, 把"主修改 + 流程修复"塞一起
  - 禁止纯流程动词: `处理 review 反馈`、`完成 review`、`闭环` 这类只描述"流程节点"而非代码本身
  - 注: `(#NN)` 是 GitHub squash merge 自动追加的 PR 号, 不在禁止之列; 但人手写 commit 时不要主动加.
- 反/正例对照:
  - 反例 `feat: persona 好感度阶段-想念-衰减联动重构 (review 闭环) (#19)` → 正例 `feat(persona): persona 好感度阶段-想念-衰减联动重构 (#19)`
  - 反例 `feat: 事件生成 LLM 超时策略与 fallback delta 兜底 (B-260507-d3cc8b)` → 正例 `feat(persona): 事件生成 LLM 超时策略与 fallback delta 兜底`
  - 反例 `feat: persona 分段回复 v4 + review 反馈修复` → 正例 合并写为对修改本身的描述, 如 `feat(persona): persona 分段回复支持 XXX 与 YYY 修正`
  - 反例 `fix: 处理 review 反馈四项` → 正例 描述具体修了什么, 如 `fix(persona): 修正 share_desire 阈值边界`
  - 反例 `fix: worktree 环境隔离修复` → 正例 `fix(dev): worktree 环境隔离修复`
  - 反例 `test: unittest.TestCase 迁移为 pytest 风格` → 正例 `refactor(test): unittest.TestCase 迁移为 pytest 风格`
- **不要在 commit message 与本文档中使用 emoji 符号** (包括打勾打叉箭头表情等图形/装饰字符), 用纯文字标记代替, 如 `反例` / `正例`、`错` / `对`.
- 标点符号使用半角+空格, 如"xxx, xxx."
- 读取实际修改的文件, 确认是否都是一个主题修改, 如果无法确定则询问用户. 根据修改的内容编写 commit log, 避免只看文件名称.
- 可以考虑将不同类别的修改分批提交
