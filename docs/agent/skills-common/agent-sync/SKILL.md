---
name: agent-sync
description: 管理 DicePP agent rules、项目技能与全局技能的同步和检查。新增、移动、重命名、删除 skill/rule，排查 .codex、.claude、.kimi-code 的同步状态，或调整 docs/agent 配置时使用。
---

# Agent Sync

使用本技能处理 `docs/agent` 到各 agent 工作目录及用户全局技能目录的同步。

## 核心原则

- 先读取 `docs/agent/sync.py help`，命令和配置格式以脚本为准。
- `docs/agent` 是事实来源；目标目录只是投影。
- 写入前运行 report/doctor，写入后再次 doctor。
- unknown 或 conflict 不自动覆盖、移动或删除。
- 生产环境只投影 common + prod 技能。

## 项目同步

- 首次接管先区分受管内容、个人内容、旧链接和未知内容，再说明 apply 会改什么。
- 后续同步先 dry-run；新增与 stale 同时出现时，按可能的重命名或拆分检查，不凭名称猜测。
- 源 skill 重命名时，同时检查目录名、frontmatter `name`、相关引用和 `agents/openai.yaml`。
- apply 后 stale 若变成 unknown，按同步器异常处理，不直接清理。

## 全局技能

`docs/agent/manifest.json` 使用稳定的 `repository` 标识同一仓库的 dev、prod 和 worktree，并在 `global.skills` 跟踪全局技能。全局技能可来自任意 `skills-*` 源目录，但名称必须唯一；原有环境分类不变。Codex、Kimi 和兼容 agent 使用 `~/.agents/skills`，Claude 使用 `~/.claude/skills`。同步器在 Windows 创建 junction，在 Linux/macOS 创建 symlink，不复制技能。

正常 report/doctor/apply 会提示全局状态，但不会修改用户全局目录。发现 missing 或 stale 时：

1. 向用户说明本次会为所有已检测 agent 新增或删除哪些全局链接，并询问是否同步。
2. 用户同意后，先执行 `apply global --dry-run`，确认无 conflict，再执行 `apply global`。
3. 随后重新 apply 项目目标，移除已经由全局目录提供的重复项目投影；最后检查 global 和项目目标。

用户拒绝时保持现状，不记录选择；项目本地投影继续可用，下次仍可询问。conflict 只汇报，不覆盖。显式执行 `apply global` 视为用户已确认计划中的新增和删除。

同一设备存在多个相同仓库副本时，首个全局链接指向的副本保持为提供者；其他副本将其视为 `provided-by-other-checkout`，不重复投影，也不自动争抢链接。只有用户明确要求切换并确认旧、新 checkout 后，才 dry-run 并执行 `apply global --relink`。

## 汇报格式

不要粘贴整段原始输出。按实际情况简要汇报：

```text
环境：
- 当前 env：
- 目标：

已同步：
- rules：
- 项目 skills：
- 全局 skills：

发现的问题：
- stale / broken / unknown / conflict：

验证：
- doctor：
```

## 安全边界

- 不清理未被同步器明确识别的内容。
- 不把目标目录中的临时内容反向当成项目事实；需要纳入项目时迁回 `docs/agent`。
- 全局同步只处理当前仓库或同 `repository` 仓库副本的已识别链接；普通目录、其他来源链接和无法识别的断链都保留并报告。
