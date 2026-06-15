---
name: agent-sync
description: 管理 DicePP agent 配置同步、环境识别、工作目录检查、rules/skills 投影与平台差异处理。需要调整或排查 .codex、.claude 中的 agent rules、skills、Claude Linux settings/hooks，或新增、移动、重命名、删除 agent skill/rule 后使用。
---

# Agent Sync

使用本技能处理 DicePP agent 配置的同步与维护。

## 使用场景

- 需要把 `docs/agent` 中的规则或技能同步到具体 agent 工具目录。
- 需要确认当前目录加载的是开发、生产或其他 agent 环境。
- 需要检查 `.codex`、`.claude` 与 `docs/agent` 的同步状态。
- 工具工作目录中出现开发者本地技能，需要确认它是否被忽略而不纳入项目同步。
- 新增、移动、重命名、删除 agent skill 或 rule 后，需要刷新工具目录。
- 需要排查 agent 配置链接失效、环境混用、技能未加载或错误加载。
- 需要处理 Claude + Linux 专用的 settings/hooks 同步。

## 使用原则

- 先读取并遵循 `docs/agent/sync.py` 的 help 输出。
- 以 `docs/agent` 为项目 agent 配置源目录，工具目录作为同步结果。
- 不在本技能中复制具体命令、参数或流程；命令细节以 `sync.py` help 为准。
- 如果发现工具目录存在未同步、未归档或本地专用内容，优先使用 `sync.py` 提供的检查与汇报能力判断。
