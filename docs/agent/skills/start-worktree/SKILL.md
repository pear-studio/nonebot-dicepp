---
name: start-worktree
description: 为 feature 分支创建独立的 git worktree，并自动共享 dev 目录的 .venv 环境。
license: MIT
metadata:
  author: DicePP
  version: "2.0"
---

为 feature 开发创建隔离的 git worktree，使用 Claude Code 内置的 `EnterWorktree` 工具。`.venv` 通过 PostToolUse hook 自动以符号链接共享。

**Input**: 用户请求创建 feature worktree，如 "开新功能"、"创建 worktree"、或提供分支名。

**Steps**

1. **使用 EnterWorktree 工具**

   直接调用 `EnterWorktree`，Claude Code 会自动在 `.claude/worktrees/` 下创建 worktree 并切换到该目录。

   如果需要指定分支名，传递 `name` 参数；否则自动生成。

2. **验证 .venv**

   检查 `.venv` 是否已自动创建（由 `PostToolUse` hook 触发）：

   ```bash
   test -L .venv && echo ".venv symlink ok" || echo "需要手动创建"
   ```

   如果 hook 未触发（首次使用或 hook 配置问题），手动创建：

   ```bash
   ln -sf /home/ubuntu/dicepp/dev/.venv .venv
   ```

3. **验证环境**

   ```bash
   uv run python -c "import sys; print(sys.executable)"
   ```

**清理 worktree**

开发完成、分支合并后，使用 `ExitWorktree` 工具退出并清理 worktree，Claude Code 会自动删除 worktree 目录和关联分支。

或将 `action` 设为 `keep` 保留 worktree。

**Important Notes**

- worktree 由 Claude Code 自动管理生命周期，session 结束后可自动清理。
- `.venv` 通过符号链接共享，所有 worktree 使用同一套 Python 环境。
- 各 worktree 的 `config/`、`data/` 等文件相互独立。
- hook 配置位于 `.claude/settings.json`，匹配 `EnterWorktree` 的 `PostToolUse` 事件。
