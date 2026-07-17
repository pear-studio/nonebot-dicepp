---
name: agent-sync
description: 管理 DicePP agent 配置同步、环境识别、首次接管、后续同步、工作目录检查、rules/skills 投影与平台差异处理。需要调整或排查 .codex、.claude 中的 agent rules、skills、Claude Linux settings/hooks，新增、移动、重命名、删除 agent skill/rule，检查环境是否暴露了错误技能，或汇报当前 agent 配置状态时使用。
---

# Agent Sync

使用本技能处理 DicePP agent 配置的同步、接管、检查和汇报。

## 核心原则

- 先读取并遵循 `docs/agent/sync.py` 的 help 输出；具体命令、参数和配置格式以脚本为准。
- 以 `docs/agent` 为项目 agent 配置源目录，`.codex` 和 `.claude` 是同步后的工作目录。
- 不在本技能中复制具体命令或参数细节。
- 同步前先判断当前是首次接管、后续同步、环境切换还是故障排查。
- 每次写入前先运行 report/doctor，明确源技能、旧同步状态与目标目录之间的新增、stale managed、broken managed、unknown 差异。
- 每次写入后再次运行 doctor；只有目标返回 `doctor: ok` 才算同步完成。

## 场景判定

### 首次接管

适用于目标目录没有同步状态、存在旧链接脚本遗留、旧 `docs/agent/skills` 链接、手工创建的 skill，或无法确定 `.codex` / `.claude` 当前来源的情况。

处理重点：

- 读取 help 并检查当前本地环境配置。
- 汇报目标目录现状，包括规则文件、技能目录、旧链接、断链、未知内容和本地忽略项。
- 区分项目将同步的 skill、可能的开发者本地 skill、旧同步遗留和未知内容。
- 执行会改动目标目录的同步前，先向用户说明会写入哪些 rules、会同步哪些 skills、哪些目标目录内容不会被同步工具处理，以及是否会替换旧链接。
- 生产环境首次接管时，特别确认目标环境是 `prod`，且不会暴露开发专用 skills。

### 后续同步

适用于目标目录已有同步状态，只需要刷新 rules、同步新增或删除的 skill、或确认环境没有漂移的情况。

处理重点：

- 检查当前环境和同步状态是否一致。
- 同步 `skills-common` 与当前 `skills-<env>` 中的新增、移动、重命名和删除。
- 同时出现新增与 stale managed 时，先报告为可能的重命名或拆分；可读取 Git rename 记录确认映射，但不要仅凭名称猜测多对多关系。
- 对 stale managed、broken managed 这类已同步项，可按脚本建议修复。正式 apply 前先执行 dry-run，并确认计划明确包含所有 stale 项的删除；若缺失，停止 apply 并按同步器故障排查。
- 对 unknown 内容不要擅自删除；先判断是否是开发者本地 skill、历史遗留或错误暴露，必要时让用户决定是否加入本地 ignore 或迁回 `docs/agent`。
- 重命名源 skill 时，检查旧目录名、frontmatter `name`、相关文档/脚本引用，以及存在时的 `agents/openai.yaml` 是否一致更新。
- 同步后再次检查并汇报结果。若 stale 在 apply 后变成 unknown，视为同步器异常而不是完成；保留现场并继续诊断，未经用户确认不要删除该 unknown 项。

### 环境切换

适用于开发/生产目录切换、`.agent-env.json` 变更，或显式指定 env 的情况。

处理重点：

- 确认当前目标环境和用户意图一致。
- 检查 prod 环境只暴露 common + prod skills，dev 环境暴露 common + dev skills。
- 若发现 prod 目标目录仍有 dev 同步 skills，先汇报风险，再按脚本能力清理。

### 故障排查

适用于 skill 未加载、规则未更新、链接断裂、同步状态异常、目标目录内容和 `docs/agent` 不一致的情况。

处理重点：

- 先收集 report/doctor 结果，再定位具体问题。
- 区分脚本可自动修复的问题和需要用户判断的问题。
- 修复后再次检查，并说明剩余风险。

## 汇报格式

最终向用户汇报时，使用以下结构；没有的项写“无”或省略该小节，不要把原始命令输出整段贴回。

```text
环境：
- 当前 env：
- 目标：
- peer 路径：

已同步：
- rules：
- skills：

未同步但保留：
- 目标目录内容：
- 匹配的忽略规则：

发现的问题：
- 过期的已同步项：
- 断开的链接：
- 未知内容：
- 环境不一致：
- 其他：

本次处理：
- 写入/刷新：
- 新增/删除/替换：
- 未处理：

验证：
- doctor：必须明确写出 `doctor: ok`；否则列出剩余 warning/error，并将本次同步标记为未完成。
- 其他验证：

后续建议：
- ...
```

## 安全边界

- 首次接管或发现 unknown 内容时，不要直接清理；先向用户说明它们可能是个人 skill、旧工具遗留或错误暴露。
- “未同步但保留”表示目标目录中存在本次同步不处理的内容，通常是 `.agent-env.json` 中 ignore 规则匹配的个人/本地专用 skill；未匹配 ignore 的未知内容应作为问题汇报。
- 生产环境同步前，确认目标 env 为 `prod`，并确认暴露技能集合符合生产边界。
- 不把 `.codex` 或 `.claude` 中的临时内容当成项目事实来源；需要纳入项目时，应迁回 `docs/agent` 下对应目录。
